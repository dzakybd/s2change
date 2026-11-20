"""FastAPI app: results, database explorer, AOI stats, date comparison and the analyst chat. Run: uvicorn app.main:app"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import date
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from sqlalchemy.exc import DBAPIError, OperationalError

from app import agent, compare_jobs, db, explorer, results
from app.guard import SqlRejected
from s2cd import compare

load_dotenv()
log = logging.getLogger("s2cd.app")
MAX_CHARS, KEEP_MESSAGES = 2000, 10


def client_ip(request: Request) -> str:
    """Caddy appends the peer it saw to X-Forwarded-For, so the last entry is the one a client cannot forge."""
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[-1].strip() or (request.client.host if request.client else "unknown")


class DailyCap:
    """Global cap on agent calls per UTC day (in memory, per process)."""

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
DB_RATE = os.environ.get("DB_RATE", "30/minute")  # explorer queries and AOI stats: read-only, 5 s timeout
COMPARE_RATE = os.environ.get("COMPARE_RATE", "10/minute")  # comparison job submissions
jobs = compare_jobs.CompareJobs(int(os.environ.get("COMPARE_DAILY_CAP", "12")))

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


@app.get("/api/health")
def health():
    return {"ok": True, "db": db.ping(), "model": agent.model_name()}


@app.get("/api/runs")
def runs():
    return db.query(
        "SELECT r.run_id, r.method, r.date_before, r.date_after, "
        "round((coalesce(sum(f.area_m2), 0) / 1e4)::numeric, 2)::float AS changed_area_ha, count(f.id)::int AS n_features "
        "FROM change_runs r LEFT JOIN change_features f USING (run_id) GROUP BY r.run_id ORDER BY r.date_before, r.run_id"
    )


def bad_request(e: ValueError) -> HTTPException:
    return HTTPException(422, str(e))


@app.get("/api/changes")
def changes(run_id: str, min_confidence: float = Query(0, ge=0, le=1), limit: int = Query(5000, ge=1, le=20000)):
    if not db.query("SELECT 1 FROM change_runs WHERE run_id = :r", r=run_id):
        raise HTTPException(404, f"Unknown run_id {run_id!r}")
    rows = db.query(
        "SELECT id, area_m2, confidence, neglog10_p, change_direction, change_type, brief_category, explanation, facility_id, "
        "touches_aoi_edge, ST_AsGeoJSON(ST_Transform(geometry, 4326), 6) AS geom FROM change_features "
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


# ---- Database explorer ---------------------------------------------------------------------------------------------


@app.get("/api/db/schema")
def db_schema():
    return explorer.schema()


@app.get("/api/db/examples")
def db_examples(run_id: str | None = None):
    try:
        return explorer.examples(results.resolve_run(run_id))
    except ValueError as e:
        raise HTTPException(404, str(e)) from None


class SqlRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=5000)


@app.post("/api/db/query")
@limiter.limit(lambda: DB_RATE)
def db_query(request: Request, body: SqlRequest):
    try:
        return explorer.run_query(body.sql)
    except SqlRejected as e:
        raise bad_request(e) from None
    except DBAPIError as e:  # the user's SQL failed in the database: a bad column, a timeout, ...
        raise HTTPException(400, f"The database rejected the query: {str(e.orig).splitlines()[0][:200]}") from None


# ---- Area of interest ----------------------------------------------------------------------------------------------


class AoiRequest(BaseModel):
    geometry: dict
    run_id: str | None = None


@app.post("/api/aoi/stats")
@limiter.limit(lambda: DB_RATE)
def aoi_stats(request: Request, body: AoiRequest):
    try:
        return results.aoi_stats(body.geometry, body.run_id)
    except ValueError as e:
        raise bad_request(e) from None


# ---- Other dates ---------------------------------------------------------------------------------------------------


@app.get("/api/compare/dates")
def compare_dates():
    """The date catalogue (``s2cd.catalog``): one clear scene per month, already downloaded. ``ready`` marks dates whose
    comparison with the provided before date is precomputed."""
    from s2cd import catalog

    before = compare.parse_label("provided-before")
    return [
        {
            "date": e["date"],
            "aoi_cloud": e["aoi_cloud"],
            "ready": (jobs.root / compare.pair_id(before, date.fromisoformat(e["date"])) / "summary.json").exists(),
        }
        for e in catalog.load()
    ]


class CompareRequest(BaseModel):
    before: str = Field(max_length=20)
    after: str = Field(max_length=20)


@app.post("/api/compare")
@limiter.limit(lambda: COMPARE_RATE)
def compare_start(request: Request, body: CompareRequest):
    try:
        job = jobs.submit(body.before, body.after)
    except ValueError as e:
        raise bad_request(e) from None
    except compare_jobs.Refused as e:
        raise HTTPException(429, str(e)) from None
    return job.public()  # a cached pair is "done" at once, so the result must come with it


@app.get("/api/compare/files/{pair}/{file}")
def compare_file(pair: str, file: str):
    path = jobs.root / pair / file
    if not compare.PAIR.fullmatch(pair) or file not in compare_jobs.FILES or not path.is_file():
        raise HTTPException(404, "No such file.")
    return FileResponse(path, media_type=compare_jobs.FILES[file], headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/compare/{job_id}")
def compare_status(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Unknown job. It may have expired; submit the comparison again.")
    return job.public()


# ---- Analyst -------------------------------------------------------------------------------------------------------


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=MAX_CHARS)


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=100)


@app.post("/api/agent/chat")
@limiter.limit(lambda: RATE)
def chat(request: Request, body: ChatRequest):
    messages = [m.model_dump() for m in body.messages[-KEEP_MESSAGES:]]
    if messages[-1]["role"] != "user":
        raise HTTPException(422, "The last message must be from the user.")
    if not daily.take():
        raise HTTPException(429, "The analyst has reached its daily question limit. Please come back tomorrow.")
    try:
        return agent.run_chat(messages)
    except Exception:
        log.exception("agent failed")
        raise HTTPException(502, "The analyst could not answer right now. Please try again.") from None
