"""Background comparison jobs: one worker thread, an in-memory queue, results cached per pair on disk (data/compare/).

``s2cd.compare`` does the work. This module owns what a web service needs around it: validation before queueing, a daily cap
on new (uncached) jobs, a bounded queue, and friendly error messages.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from s2cd import compare

log = logging.getLogger("s2cd.app")
MAX_WAITING = 5  # queued jobs beyond the one running
MAX_REMEMBERED = 200  # finished jobs kept in memory for polling
FILES = {
    "changes.geojson": "application/geo+json",
    "summary.json": "application/json",
    "before.webp": "image/webp",
    "after.webp": "image/webp",
    "intensity.webp": "image/webp",
}


class Refused(Exception):
    """The job was not accepted: the daily cap or the queue is full. ``str(e)`` is shown to the user."""


@dataclass
class Job:
    job_id: str
    pair: str
    before: str
    after: str
    status: str = "queued"  # queued | running | done | error
    message: str = ""
    result: dict | None = None
    created: date = field(default_factory=date.today)

    def public(self) -> dict:
        body = {"job_id": self.job_id, "status": self.status, "message": self.message}
        return body | ({"result": self.result} if self.result else {})


def file_url(pair: str, name: str) -> str:
    return f"/api/compare/files/{pair}/{name}"


def result_payload(pair: str, folder: Path) -> dict:
    """The API ``result`` of a finished comparison: summary, the GeoJSON url and the overlays with their bounds."""
    summary = json.loads((folder / "summary.json").read_text())
    overlays = json.loads((folder / "manifest.json").read_text())["overlays"]
    return {
        "pair": pair,
        "summary": {k: summary[k] for k in ("changed_ha", "by_change_type", "by_facility")},
        "date_labels": summary["date_labels"],
        "changes_url": file_url(pair, "changes.geojson"),
        "overlays": {name: {"url": file_url(pair, o["file"]), "bounds": o["bounds"]} for name, o in overlays.items()},
    }


class CompareJobs:
    def __init__(self, daily_cap: int, root: Path = compare.ROOT, run=compare.run):
        self.daily_cap, self.root, self._run = daily_cap, Path(root), run
        self._jobs: dict[str, Job] = {}
        self._queue: queue.Queue[Job] = queue.Queue()
        self._lock = threading.Lock()
        self._day, self._used = date.min, 0
        self._worker: threading.Thread | None = None

    def submit(self, before: str, after: str) -> Job:
        """Queue a comparison: ValueError for bad dates, Refused when over capacity. Cached pairs are done at once."""
        d_before, d_after = compare.validate_pair(before, after)
        pair = compare.pair_id(d_before, d_after)
        with self._lock:
            if cached := self._cached(pair):
                return self._remember(Job(uuid.uuid4().hex[:12], pair, before, after, "done", "Cached result.", cached))
            if active := next((j for j in self._jobs.values() if j.pair == pair and j.status in ("queued", "running")), None):
                return active
            if self._queue.qsize() >= MAX_WAITING:
                raise Refused("Other comparisons are waiting. Please try again in a few minutes.")
            self._take_daily()
            job = self._remember(Job(uuid.uuid4().hex[:12], pair, before, after))
            self._queue.put(job)
            self._start_worker()
            return job

    def is_cached(self, before: str, after: str) -> bool:
        """True when the pair's result is already on disk (ValueError for bad dates, like ``submit``)."""
        d_before, d_after = compare.validate_pair(before, after)
        return (self.root / compare.pair_id(d_before, d_after) / "summary.json").exists()

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def _cached(self, pair: str) -> dict | None:
        folder = self.root / pair
        return result_payload(pair, folder) if (folder / "summary.json").exists() else None

    def _take_daily(self) -> None:
        today = date.today()
        if today != self._day:
            self._day, self._used = today, 0
        if self._used >= self.daily_cap:
            raise Refused(
                "The service has reached its daily limit of new comparisons. Cached pairs still work; try again tomorrow."
            )
        self._used += 1

    def _remember(self, job: Job) -> Job:
        self._jobs[job.job_id] = job
        for old in [j for j in self._jobs.values() if j.status in ("done", "error")][:-MAX_REMEMBERED]:
            del self._jobs[old.job_id]
        return job

    def _start_worker(self) -> None:
        if self._worker is None:
            self._worker = threading.Thread(target=self._work, name="compare-worker", daemon=True)
            self._worker.start()

    def _work(self) -> None:
        while True:
            job = self._queue.get()
            job.status, job.message = "running", "Running the change detection on the stored scenes (about a minute)."
            try:
                folder = self._run(job.before, job.after, self.root)
                job.result, job.status, job.message = result_payload(job.pair, folder), "done", "Done."
            except ValueError as e:  # pipeline: inconsistent inputs
                job.status, job.message = "error", str(e)
            except Exception:
                log.exception("comparison %s failed", job.pair)
                job.status, job.message = "error", "The comparison failed. Try other dates, or try again later."
