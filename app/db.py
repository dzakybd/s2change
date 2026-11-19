"""Read-only database access (``s2cd_reader``) shared by the API endpoints and the agent tools."""

from __future__ import annotations

import os
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text


@lru_cache(maxsize=1)
def reader() -> Engine:
    url = os.environ.get("READER_DATABASE_URL")
    if not url:
        raise RuntimeError("READER_DATABASE_URL is not set")
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)


def query(sql: str, **params) -> list[dict]:
    """Run a parameterised SELECT as the reader role and return rows as dicts."""
    with reader().connect() as conn:
        return [dict(r) for r in conn.execute(text(sql), params).mappings()]


def ping() -> bool:
    try:
        query("SELECT 1")
    except Exception:
        return False
    return True
