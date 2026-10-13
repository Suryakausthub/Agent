"""Live pipeline progress, streamed to the UI over Server-Sent Events.

The pipeline runs as a few long blocking calls -- extraction takes seconds,
tailoring makes one model call per bullet -- and without this the interface is
a spinner and a shrug. Every stage announces itself here, so the user can
watch which step is running, what it decided, and where the time goes.

Deliberately in-memory and unbounded-free: this is a local single-user tool,
events are advisory, and losing them on restart costs nothing. The durable
record is the audit trail in SQLite.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

# Stage identifiers the UI knows how to render.
STAGES = [
    ("fetch", "Fetch job description"),
    ("extract", "Extract structure"),
    ("resolve", "Resolve skill terms"),
    ("score", "Compute match score"),
    ("ats", "ATS keyword coverage"),
    ("select", "Select projects & bullets"),
    ("rewrite", "Rewrite bullets"),
    ("summary", "Re-emphasise summary"),
    ("validate", "Whitelist & diff checks"),
    ("render", "Render LaTeX"),
    ("fit", "Fit to page"),
    ("done", "Complete"),
]

RUNNING = "running"
OK = "ok"
WARN = "warn"
FAIL = "fail"


@dataclass
class Event:
    job_id: str
    stage: str
    status: str
    detail: str = ""
    ts: float = field(default_factory=time.time)
    data: dict[str, Any] = field(default_factory=dict)

    def sse(self) -> str:
        payload = {
            "job_id": self.job_id, "stage": self.stage, "status": self.status,
            "detail": self.detail, "ts": self.ts, "data": self.data,
        }
        return "data: %s\n\n" % json.dumps(payload, ensure_ascii=False)


class Broker:
    def __init__(self, history_limit: int = 200):
        self._lock = threading.Lock()
        self._subs: dict[str, list[queue.Queue]] = {}
        self._history: dict[str, list[Event]] = {}
        self._limit = history_limit

    def emit(self, job_id: str, stage: str, status: str = RUNNING,
             detail: str = "", **data: Any) -> None:
        ev = Event(job_id=job_id, stage=stage, status=status,
                   detail=detail, data=data)
        with self._lock:
            hist = self._history.setdefault(job_id, [])
            hist.append(ev)
            if len(hist) > self._limit:
                del hist[:-self._limit]
            for q in self._subs.get(job_id, []):
                try:
                    q.put_nowait(ev)
                except queue.Full:
                    pass          # a slow reader must not stall the pipeline

    def history(self, job_id: str) -> list[Event]:
        with self._lock:
            return list(self._history.get(job_id, []))

    def clear(self, job_id: str) -> None:
        with self._lock:
            self._history.pop(job_id, None)

    def subscribe(self, job_id: str) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=500)
        with self._lock:
            self._subs.setdefault(job_id, []).append(q)
        return q

    def unsubscribe(self, job_id: str, q: queue.Queue) -> None:
        with self._lock:
            subs = self._subs.get(job_id, [])
            if q in subs:
                subs.remove(q)
            if not subs:
                self._subs.pop(job_id, None)

    def stream(self, job_id: str, keepalive: float = 15.0) -> Iterator[str]:
        """SSE generator: replay history, then follow live."""
        q = self.subscribe(job_id)
        try:
            for ev in self.history(job_id):
                yield ev.sse()
            yield ": connected\n\n"
            while True:
                try:
                    ev = q.get(timeout=keepalive)
                    yield ev.sse()
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            self.unsubscribe(job_id, q)


broker = Broker()


class Stage:
    """Context manager that reports a stage's start, outcome and duration."""

    def __init__(self, job_id: str, stage: str, detail: str = ""):
        self.job_id, self.stage, self.detail = job_id, stage, detail
        self.t0 = 0.0
        self.result: dict[str, Any] = {}

    def __enter__(self) -> "Stage":
        self.t0 = time.time()
        broker.emit(self.job_id, self.stage, RUNNING, self.detail)
        return self

    def finish(self, detail: str, status: str = OK, **data: Any) -> None:
        self.detail, self.result, self.status = detail, data, status

    def __exit__(self, exc_type, exc, tb) -> bool:
        ms = int((time.time() - self.t0) * 1000)
        if exc is not None:
            broker.emit(self.job_id, self.stage, FAIL,
                        "%s: %s" % (exc_type.__name__, exc), ms=ms)
            return False
        broker.emit(self.job_id, self.stage,
                    getattr(self, "status", OK), self.detail, ms=ms,
                    **self.result)
        return False
