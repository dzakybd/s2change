"""The analyst chat's entry point, kept light: the LLM stack (api.agent, ~70 MB) is imported only on a cache miss.

The viewer's starter questions (webapp/viewer/src/views/analyst.ts SUGGESTIONS) are answered once and kept on disk, keyed by the
model, the agent's code and the published totals. A restart or deploy then costs no LLM calls, and the API stays at about
55 MB until someone asks something else.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger("s2cd.app")
DEFAULT_MODEL = "gemini-3.5-flash"
STARTERS = (
    "How much changed and why?",
    "Show the 5 largest changes in the mine footprint",
    "Which facility changed the most?",
    "Which changes are likely burn scars?",
)
APP = Path(__file__).parent
_lock = threading.Lock()
_cache: dict[str, dict] = {}
_fingerprint: str | None = None


def model_name() -> str:
    if os.environ.get("LLM_PROVIDER", "google") == "openrouter":
        return os.environ.get("AGENT_OPENROUTER_MODEL", f"google/{DEFAULT_MODEL}")
    return os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)


def cache_path() -> Path:
    return Path(os.environ.get("ANALYST_CACHE", ".cache/analyst_starters.json"))


def fingerprint() -> str:
    """What a cached answer depends on: model, prompt and tools, knowledge base and the published totals."""
    from api import results

    h = hashlib.sha256(model_name().encode())
    for p in [APP / "agent.py", APP / "tools.py", *sorted((APP / "knowledge").glob("*.md"))]:
        h.update(p.read_bytes())
    h.update(json.dumps(results.change_type_summary(), sort_keys=True, default=str).encode())
    return h.hexdigest()


def _load() -> None:
    """Fill the in-memory cache from disk once, dropping it when anything it depends on changed."""
    global _fingerprint
    if _fingerprint is not None:
        return
    try:
        _fingerprint = fingerprint()
    except Exception:  # database unreachable: no cache this time, try again on the next call
        log.warning("analyst cache unavailable: cannot read the published totals")
        return
    try:
        saved = json.loads(cache_path().read_text())
    except (OSError, ValueError):
        return
    if saved.get("fingerprint") == _fingerprint:
        _cache.update(saved.get("answers", {}))


def _save() -> None:
    if _fingerprint is None:
        return
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"fingerprint": _fingerprint, "answers": _cache}))
    tmp.replace(path)


def warm() -> None:
    """Answer the starters not cached yet. Stops at the first failure: a quota or credit error would repeat for all."""
    with _lock:
        _load()
    for q in STARTERS:
        if q in _cache:
            continue
        try:
            run_chat([{"role": "user", "content": q}])
        except Exception:
            log.exception("warming the analyst cache failed for %r; not trying the rest", q)
            return


def run_chat(messages: list[dict]) -> dict:
    """Run one chat turn and return the API response body. Single-turn starter questions are served from the cache."""
    key = messages[0]["content"].strip() if len(messages) == 1 and messages[0].get("role") == "user" else None
    if key in STARTERS:
        with _lock:
            _load()
            if key in _cache:
                return _cache[key]
    from api import agent

    out = agent.run(messages)
    if key in STARTERS and not out["answer"].startswith("I could not"):
        with _lock:
            _cache[key] = out
            _save()
    return out
