from __future__ import annotations

import threading
from collections.abc import Iterator

import pytest

from hillaudio.api.jobs import JobContext, JobManager


@pytest.fixture
def jobs() -> Iterator[JobManager]:
    m = JobManager()
    yield m
    m.shutdown()


def test_progress_and_result(jobs: JobManager) -> None:
    def work(ctx: JobContext) -> int:
        ctx.progress(0.5, "half")
        return 42

    job = jobs.wait(jobs.submit("t", "work", work).id)
    assert (job.status, job.result, job.progress) == ("done", 42, 1.0)


def test_failure_is_reported(jobs: JobManager) -> None:
    def boom(_ctx: JobContext) -> None:
        raise ValueError("bad input")

    job = jobs.wait(jobs.submit("t", "boom", boom).id)
    assert (job.status, job.error) == ("failed", "bad input")


def test_cancel_running_job(jobs: JobManager) -> None:
    started = threading.Event()

    def loop(ctx: JobContext) -> None:
        started.set()
        while True:
            ctx.check_cancel()
            threading.Event().wait(0.005)

    job = jobs.submit("t", "loop", loop)
    started.wait(2)
    jobs.cancel(job.id)
    assert jobs.wait(job.id).status == "cancelled"
    assert not jobs.active()


def test_version_changes_on_updates(jobs: JobManager) -> None:
    v0, _ = jobs.snapshot()
    jobs.wait(jobs.submit("t", "x", lambda ctx: None).id)
    v1, snap = jobs.snapshot()
    assert v1 > v0 and snap[0]["status"] == "done"
    jobs.clear_finished()
    assert jobs.snapshot()[1] == []


def test_lane_runs_jobs_one_at_a_time_in_order(jobs: JobManager) -> None:
    """Regression: parallel imports numbered sources in finish order, not pick order."""
    log: list[str] = []
    first_may_finish = threading.Event()

    def slow(ctx: JobContext) -> None:
        log.append("a-start")
        first_may_finish.wait(2)
        log.append("a-end")

    def fast(ctx: JobContext) -> None:
        log.append("b")

    a = jobs.submit("t", "a", slow, lane="import")
    b = jobs.submit("t", "b", fast, lane="import")
    threading.Event().wait(0.1)  # b would have run by now if it weren't queued behind a
    assert log == ["a-start"] and jobs.get(b.id).status == "queued"
    first_may_finish.set()
    jobs.wait(a.id)
    jobs.wait(b.id)
    assert log == ["a-start", "a-end", "b"]
