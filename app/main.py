"""FastAPI app: map/timeline data endpoints and the analyst chat. Run: uvicorn app.main:app"""

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
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from sqlalchemy.exc import OperationalError, ProgrammingError

from app import agent, db

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

app = FastAPI(title="s2cd analyst API")
app.state.limiter = limiter
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "https://s2change.dzaky.ai.id"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.exception_handler(RateLimitExceeded)
async def too_many(request: Request, exc: RateLimitExceeded):
    msg = "You are asking quickly. The analyst allows a few questions per minute, please try again shortly."
    return JSONResponse({"detail": msg}, status_code=429, headers={"Retry-After": "60"})


@app.exception_handler(OperationalError)
async def db_down(request: Request, exc: OperationalError):
    return JSONResponse({"detail": "The database is not reachable right now."}, status_code=503)


@app.get("/api/health")
def health():
    return {"ok": True, "db": db.ping(), "model": os.environ.get("GEMINI_MODEL", agent.DEFAULT_MODEL)}


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
        "SELECT id, area_m2, confidence, change_direction, in_disturbed_area, likely_cause, "
        "ST_AsGeoJSON(ST_Transform(geometry, 4326), 6) AS geom FROM change_features "
        "WHERE run_id = :r AND confidence >= :c ORDER BY area_m2 DESC, id LIMIT :n",
        r=run_id, c=min_confidence, n=limit,
    )  # fmt: skip
    features = []
    for r in rows:
        geom = json.loads(r.pop("geom"))
        props = {k: v for k, v in r.items() if v is not None or k != "likely_cause"}
        features.append({"type": "Feature", "id": r["id"], "geometry": geom, "properties": props})
    return {"type": "FeatureCollection", "features": features}


@app.get("/api/timeline")
def timeline():
    cols = (
        "month, status, clear_fraction, ndvi_mean, nbr_mean, mndwi_mean, bsi_mean, "
        "bare_ha, water_ha, footprint_ha, changed_ha, changed_ha_in_footprint"
    )
    try:
        return db.query(f"SELECT {cols} FROM timeline_months ORDER BY month")
    except ProgrammingError:  # table absent (older publish)
        return []


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
