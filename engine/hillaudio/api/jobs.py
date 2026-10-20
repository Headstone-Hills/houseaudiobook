"""Cancellable background jobs with progress (CLAUDE.md: long work never blocks the UI).

Jobs run on a small thread pool, or in a named serial *lane* when order matters:
jobs in one lane run one at a time, first submitted first (imports use this, so
sources are numbered in the order the user chose them).

Engine functions receive plain `progress` and `check_cancel` callables (see
hillaudio.progress), so they don't depend on this module. Clients watch jobs
over the WebSocket in api/app.py, which sends a snapshot whenever `version`
changes.
"""

from __future__ import annotations

import threading
import traceback
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Literal

from hillaudio.progress import Cancelled

Status = Literal["queued", "running", "done", "failed", "cancelled"]
TERMINAL: frozenset[str] = frozenset({"done", "failed", "cancelled"})


# Kept as the job runner's name for it; engine code raises and catches Cancelled.
JobCancelled = Cancelled


@dataclass
class Job:
    id: str
    kind: str
    label: str
    status: Status = "queued"
    progress: float = 0.0
    message: str = ""
    result: Any = None
    error: str | None = None
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "status": self.status,
            "progress": round(self.progress, 4),
            "message": self.message,
            "result": self.result,
            "error": self.error,
        }


class JobContext:
    def __init__(self, manager: JobManager, job: Job) -> None:
        self._m, self._job = manager, job

    def progress(self, fraction: float, message: str) -> None:
        self._m._update(self._job, progress=max(0.0, min(1.0, fraction)), message=message)

    def check_cancel(self) -> None:
        if self._job._cancel.is_set():
            raise JobCancelled


JobFn = Callable[[JobContext], Any]


class JobManager:
    def __init__(self, workers: int = 2) -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job")
        self._lanes: dict[str, ThreadPoolExecutor] = {}
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self.version = 0  # bumped on every change; cheap change detection for WebSockets

    def submit(self, kind: str, label: str, fn: JobFn, lane: str | None = None) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, label=label)
        with self._lock:
            self._jobs[job.id] = job
            self.version += 1
            if lane is None:
                executor = self._pool
            else:
                # One worker = strict FIFO within the lane.
                executor = self._lanes.setdefault(
                    lane, ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"lane-{lane}")
                )
            executor.submit(self._run, job, fn)
        return job

    def _run(self, job: Job, fn: JobFn) -> None:
        if job._cancel.is_set():
            self._update(job, status="cancelled", message="cancelled")
            return
        self._update(job, status="running")
        try:
            result = fn(JobContext(self, job))
        except JobCancelled:
            self._update(job, status="cancelled", message="cancelled")
        except Exception as e:  # report every failure to the UI rather than losing it
            traceback.print_exc()
            self._update(job, status="failed", error=str(e) or type(e).__name__)
        else:
            self._update(job, status="done", progress=1.0, result=result)

    def _update(self, job: Job, **changes: Any) -> None:
        with self._lock:
            for k, v in changes.items():
                setattr(job, k, v)
            self.version += 1

    def get(self, job_id: str) -> Job:
        with self._lock:
            return self._jobs[job_id]

    def all(self) -> list[Job]:
        with self._lock:
            return list(self._jobs.values())

    def snapshot(self) -> tuple[int, list[dict[str, Any]]]:
        with self._lock:
            return self.version, [j.to_json() for j in self._jobs.values()]

    def cancel(self, job_id: str) -> Job:
        job = self.get(job_id)
        job._cancel.set()
        return job

    def active(self) -> bool:
        with self._lock:
            return any(j.status not in TERMINAL for j in self._jobs.values())

    def clear_finished(self) -> None:
        with self._lock:
            self._jobs = {k: j for k, j in self._jobs.items() if j.status not in TERMINAL}
            self.version += 1

    def wait(self, job_id: str, timeout: float = 30.0) -> Job:
        """Block until a job finishes (tests and scripted demos)."""
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.get(job_id)
            if job.status in TERMINAL:
                return job
            time.sleep(0.01)
        raise TimeoutError(job_id)

    def shutdown(self) -> None:
        for j in self.all():
            j._cancel.set()
        for executor in [self._pool, *self._lanes.values()]:
            executor.shutdown(wait=True)
