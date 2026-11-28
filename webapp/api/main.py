"""FastAPI app: results, database explorer and the analyst chat. Run: uvicorn api.main:app --app-dir webapp"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import date
from typing import Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from sqlalchemy.exc import OperationalError

from api import analyst, auth, db, explorer, results

load_dotenv()
log = logging.getLogger("s2cd.app")
MAX_CHARS, KEEP_MESSAGES = 2000, 20


def client_ip(request: Request) -> str:
    """Caddy appends the peer it saw to X-Forwarded-For, so the last entry is the one a client cannot forge."""
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[-1].strip() or (request.client.host if request.client else "unknown")


class DailyCap:
    """Global cap on calls per UTC day (in memory, per process)."""

    def __init__(self, cap: int):
        self.cap, self.day, self.used, self._lock = cap, date.min, 0, threading.Lock()

    def take(self) -> bool:
        with self._lock:
            today = date.today()
            if today != self.day:
                self.day, self.used = today, 0
            if self.used >= self.cap:
                return False
            self.used += 1
            return True


limiter = Limiter(key_func=client_ip)
daily = DailyCap(int(os.environ.get("AGENT_DAILY_CAP", "300")))
RATE = os.environ.get("AGENT_RATE", "6/minute")
DB_RATE = os.environ.get("DB_RATE", "60/minute")  # explorer pages: read-only, 5 s timeout
EXPORT_RATE = os.environ.get("EXPORT_RATE", "6/minute")  # SQL dumps (cached for 10 minutes)
LOGIN_RATE = os.environ.get("LOGIN_RATE", "5/minute")  # access-code attempts, per client IP

app = FastAPI(title="s2change API")
app.state.limiter = limiter
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "https://s2change.dzaky.ai.id"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.exception_handler(RateLimitExceeded)
async def too_many(request: Request, exc: RateLimitExceeded):
    msg = "Too many requests in a short time. Please wait a minute and try again."
    return JSONResponse({"detail": msg}, status_code=429, headers={"Retry-After": "60"})


@app.exception_handler(OperationalError)
async def db_down(request: Request, exc: OperationalError):
    return JSONResponse({"detail": "The database is not reachable right now."}, status_code=503)


@app.on_event("startup")
def check_auth() -> None:
    auth.check_config()  # warns when the access code is not configured, refuses a half-configured one


@app.get("/api/health")
def health():
    return {"ok": True, "db": db.ping(), "model": analyst.model_name()}


@app.get("/api/runs")
def runs():
    return db.query(
        "SELECT r.run_id, r.method, r.date_before, r.date_after, "
        "round((coalesce(sum(f.area_m2), 0) / 1e4)::numeric, 2)::float AS changed_area_ha, count(f.id)::int AS n_features "
        "FROM change_runs r LEFT JOIN change_features f USING (run_id) GROUP BY r.run_id ORDER BY r.date_before, r.run_id"
    )


@app.get("/api/changes")
def changes(run_id: str, min_confidence: float = Query(0, ge=0, le=1), limit: int = Query(5000, ge=1, le=20000)):
    if not db.query("SELECT 1 FROM change_runs WHERE run_id = :r", r=run_id):
        raise HTTPException(404, f"Unknown run_id {run_id!r}")
    rows = db.query(
        "SELECT id, area_m2, confidence, neglog10_p, change_direction, change_type, brief_category, explanation, facility_id, "
        "touches_aoi_edge, headline, location, evidence, what_to_check, description, "
        "ST_AsGeoJSON(ST_Transform(geometry, 4326), 6) AS geom FROM change_features "
        "WHERE run_id = :r AND confidence >= :c ORDER BY area_m2 DESC, id LIMIT :n",
        r=run_id, c=min_confidence, n=limit,
    )  # fmt: skip
    features = [{"type": "Feature", "id": r["id"], "geometry": json.loads(r.pop("geom")), "properties": r} for r in rows]
    return {"type": "FeatureCollection", "features": features}


@app.get("/api/facilities")
def facilities():
    return results.facilities_geojson()


@app.get("/api/brief")
def brief(run_id: str | None = None):
    try:
        return {"markdown": results.brief(run_id)}
    except ValueError as e:
        raise HTTPException(404, str(e)) from None


# Access code


class LoginRequest(BaseModel):
    code: str = Field(max_length=200)


@app.get("/api/auth/status")
def auth_status(request: Request):
    return {"enabled": auth.enabled(), "authenticated": auth.authenticated(request)}


@app.post("/api/auth/login")
@limiter.limit(lambda: LOGIN_RATE)
def auth_login(request: Request, body: LoginRequest, response: Response):
    if auth.enabled():
        if not auth.verify_code(body.code, os.environ["ACCESS_CODE_HASH"]):
            raise HTTPException(401, "That access code is not right.")
        response.set_cookie(auth.COOKIE, auth.make_session(), **auth.cookie_kwargs())
    return {"enabled": auth.enabled(), "authenticated": True}


@app.post("/api/auth/logout")
def auth_logout(response: Response):
    response.delete_cookie(auth.COOKIE, path="/api", secure=True, httponly=True, samesite="strict")
    return {"enabled": auth.enabled(), "authenticated": not auth.enabled()}


# Database explorer


@app.get("/api/db/schema")
def db_schema():
    return explorer.schema()


@app.get("/api/db/tables/{table}")
@limiter.limit(lambda: DB_RATE)
def db_table(
    request: Request,
    table: str,
    page: int = Query(1, ge=1),
    size: int = 25,
    sort: str | None = Query(None, max_length=64),
    desc: bool = False,
    column: str | None = Query(None, max_length=64),
    value: str | None = Query(None, max_length=200),
    geojson: bool = False,
):
    try:
        return explorer.page(table, page, size, sort, desc, column, value, geojson)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None


@app.get("/api/db/export.sql")
@limiter.limit(lambda: EXPORT_RATE)
def db_export(request: Request, table: str | None = Query(None, max_length=64)):
    try:
        sql = explorer.export_sql(table)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    name = f"s2change_{table or 'database'}.sql"
    return Response(sql, media_type="application/sql", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/db/connection")
def db_connection():
    return explorer.connection()


@app.get("/api/db/connection/uri", dependencies=[Depends(auth.require_access)])
def db_connection_uri():
    """The read-only role's full URI (password included), for evaluators with the access code."""
    return {"uri": explorer.full_uri()}


# Analyst


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=MAX_CHARS)


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=100)


@app.on_event("startup")
def warm_analyst() -> None:
    """Answer the starter questions in the background so their first click is instant (needs an LLM key)."""
    if any(os.environ.get(k) for k in ("OPENROUTER_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")):
        threading.Thread(target=analyst.warm, name="analyst-warm", daemon=True).start()


@app.post("/api/agent/chat", dependencies=[Depends(auth.require_access)])
@limiter.limit(lambda: RATE)
def chat(request: Request, body: ChatRequest):
    messages = [m.model_dump() for m in body.messages[-KEEP_MESSAGES:]]
    if messages[-1]["role"] != "user":
        raise HTTPException(422, "The last message must be from the user.")
    if not daily.take():
        raise HTTPException(429, "The analyst has reached its daily question limit. Please come back tomorrow.")
    try:
        return analyst.run_chat(messages)
    except Exception as e:
        log.exception("agent failed")
        if _status(e) == 402:  # the LLM account is out of credit: retrying cannot help, so say so
            raise HTTPException(503, OUT_OF_CREDIT) from None
        raise HTTPException(502, "The agent could not answer right now. Please try again.") from None


OUT_OF_CREDIT = (
    "The agent's AI credit is used up, so it cannot answer until the owner tops it up. "
    "Everything else (map, report, database) works without it."
)


def _status(e: BaseException | None) -> int | None:
    """HTTP status of an LLM provider error anywhere in the exception chain (the agent framework wraps it)."""
    seen = 0
    while e is not None and seen < 10:
        code = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)
        if isinstance(code, int):
            return code
        e, seen = e.__cause__ or e.__context__, seen + 1
    return None
