"""M4 — the durable job queue.

Before M4 a run was a closure in `BackgroundTasks` and a dict in memory: a
restart orphaned it, a second process couldn't see it, and nothing could stop
it. These tests pin down the behaviour that replaced that.
"""
from __future__ import annotations

import threading
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

import jobs
from agents.orchestrator import ProgressEvent
from jobs import queue, worker
from shared import db


def _make_due(job_id: str) -> None:
    """Skip the retry backoff so a test doesn't have to wait it out."""
    with db.get_engine().begin() as c:
        c.execute(db.jobs.update().where(db.jobs.c.id == job_id)
                  .values(run_after=queue.utcnow()))


class FakeOrchestrator:
    """Stands in for the pipeline: emits the events we want, when we want."""

    def __init__(self, events=1, raises: Exception | None = None, on_each=None):
        self.events = events
        self.raises = raises
        self.on_each = on_each
        self.calls: list[dict] = []

    def plan(self, project_id=None, on_event=None, **payload):
        self.calls.append({"project_id": project_id, **payload})
        if self.raises is not None:
            raise self.raises
        for i in range(self.events):
            on_event(ProgressEvent(phase="story", status="started",
                                   message=f"step {i}", progress=i / self.events,
                                   project_id=project_id))
            if self.on_each:
                self.on_each(i)
        return None

    # The worker reaches these through METHODS; same shape as plan.
    run_full = plan
    render = plan
    re_run_phase = plan


# ---- durability -------------------------------------------------------------

def test_a_queued_run_survives_a_process_restart(isolated_dirs):
    """The old in-memory registry lost every in-flight run on restart."""
    job = jobs.enqueue("render", "p1", {"with_bgm": False})

    db.dispose_all()          # as if the API process had been restarted

    recovered = jobs.get(job.id)
    assert recovered is not None
    assert recovered.status == "queued" and recovered.payload == {"with_bgm": False}
    assert queue.claim("worker-after-restart").id == job.id


def test_a_job_is_claimed_by_exactly_one_worker(isolated_dirs):
    """Two workers racing for one job must not both run the pipeline on it."""
    jobs.enqueue("plan", "p1", {"prompt": "x"})
    claimed: list = []
    start = threading.Event()

    def grab(n):
        start.wait()
        job = queue.claim(f"worker-{n}")
        if job:
            claimed.append(job)

    threads = [threading.Thread(target=grab, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    start.set()
    for t in threads:
        t.join(timeout=10)

    assert len(claimed) == 1


def test_events_outlive_the_connection_that_watched_them(isolated_dirs):
    """A browser that reconnects replays what it missed instead of hanging."""
    job = jobs.enqueue("plan", "p1", {"prompt": "x"})
    for i in range(3):
        jobs.append_event(job.id, "p1", phase="story", status="started",
                          message=f"m{i}", progress=i / 3)

    db.dispose_all()

    replay = jobs.events_since(job.id, 0)
    assert [e["message"] for e in replay] == ["m0", "m1", "m2"]
    # And a cursor only returns what is newer.
    assert [e["message"] for e in jobs.events_since(job.id, replay[0]["id"])] == ["m1", "m2"]


# ---- running ----------------------------------------------------------------

def test_worker_runs_a_job_and_records_its_events(isolated_dirs):
    job = jobs.enqueue("plan", "p1", {"prompt": "a librarian", "scene_count": 2})
    orch = FakeOrchestrator(events=2)

    assert worker.run_job(job, orchestrator=orch) == "succeeded"
    assert orch.calls == [{"project_id": "p1", "prompt": "a librarian", "scene_count": 2}]
    assert jobs.get(job.id).status == "succeeded"
    assert [e["message"] for e in jobs.events_since(job.id, 0)] == ["step 0", "step 1"]


def test_a_failed_job_is_retried_then_marked_failed(isolated_dirs):
    jobs.enqueue("plan", "p1", {"prompt": "x"}, max_attempts=2)
    orch = FakeOrchestrator(raises=RuntimeError("ffmpeg exploded"))

    first = queue.claim("worker-1")
    assert worker.run_job(first, orchestrator=orch) == "queued"   # one attempt left
    requeued = jobs.get(first.id)
    assert requeued.status == "queued" and requeued.attempts == 1
    assert "ffmpeg exploded" in requeued.error

    # It backs off first, so a deterministic failure can't spin the worker.
    assert queue.claim("worker-2") is None
    _make_due(first.id)

    second = queue.claim("worker-2")
    assert second.attempts == 2
    assert worker.run_job(second, orchestrator=orch) == "failed"
    assert jobs.get(first.id).status == "failed"


def test_a_crashed_worker_hands_its_job_back(isolated_dirs):
    """No heartbeat for two minutes means the worker is gone, not slow."""
    job = jobs.enqueue("render", "p1", {})
    claimed = queue.claim("doomed-worker")
    assert claimed is not None and jobs.get(job.id).status == "running"

    with db.get_engine().begin() as c:
        c.execute(db.jobs.update().where(db.jobs.c.id == job.id).values(
            heartbeat_at=queue.utcnow() - timedelta(seconds=600)))

    assert jobs.requeue_stale() == 1
    back = jobs.get(job.id)
    assert back.status == "queued" and back.worker is None

    # Once the attempts are spent it fails instead of looping forever.
    with db.get_engine().begin() as c:
        c.execute(db.jobs.update().where(db.jobs.c.id == job.id).values(
            status="running", attempts=back.max_attempts,
            heartbeat_at=queue.utcnow() - timedelta(seconds=600)))
    assert jobs.requeue_stale() == 1
    assert jobs.get(job.id).status == "failed"


# ---- cancelling --------------------------------------------------------------

def test_cancelling_a_queued_job_stops_it_before_it_starts(isolated_dirs):
    job = jobs.enqueue("render", "p1", {})
    assert jobs.cancel(job.id) == "cancelled"
    assert jobs.get(job.id).status == "cancelled"
    assert queue.claim("worker-1") is None


def test_cancelling_a_running_job_stops_it_at_the_next_step(isolated_dirs):
    """Cancellation lands between steps, so no half-written file is left behind."""
    job = jobs.enqueue("plan", "p1", {"prompt": "x"})
    claimed = queue.claim("worker-1")

    def cancel_after_first(i):
        if i == 0:
            assert jobs.cancel(job.id) == "cancelling"

    orch = FakeOrchestrator(events=5, on_each=cancel_after_first)
    assert worker.run_job(claimed, orchestrator=orch) == "cancelled"
    assert jobs.get(job.id).status == "cancelled"

    # It stopped early: two events from the pipeline, plus the cancellation.
    phases = [e["phase"] for e in jobs.events_since(job.id, 0)]
    assert phases.count("story") < 5 and phases[-1] == "cancelled"
    # And it is not reported as a failure on the way out. The orchestrator
    # wraps the pipeline in `except Exception`, which used to log an error
    # event first and pop a "Pipeline failed" alert in the browser.
    assert "error" not in phases


def test_a_deliberate_stop_is_not_caught_as_a_pipeline_failure(isolated_dirs):
    """Agents catch Exception all over the pipeline and carry on; cancellation
    must pass straight through them."""
    job = jobs.enqueue("plan", "p1", {"prompt": "x"})
    claimed = queue.claim("worker-1")

    def swallow_everything(_i):
        jobs.cancel(job.id)

    class SwallowingOrchestrator(FakeOrchestrator):
        def plan(self, project_id=None, on_event=None, **payload):
            try:
                super().plan(project_id=project_id, on_event=on_event, **payload)
            except Exception:  # noqa: BLE001 - exactly what the agents do
                on_event(ProgressEvent(phase="error", status="failed",
                                       message="swallowed", project_id=project_id))

        run_full = plan
        render = plan
        re_run_phase = plan

    orch = SwallowingOrchestrator(events=4, on_each=swallow_everything)
    assert worker.run_job(claimed, orchestrator=orch) == "cancelled"
    assert "error" not in [e["phase"] for e in jobs.events_since(job.id, 0)]


# ---- the API -----------------------------------------------------------------

@pytest.fixture
def api(isolated_dirs, monkeypatch):
    from backend import app as app_module
    from backend.routes import pipeline as pipeline_routes
    from state_manager.state_manager import StateManager
    from state_manager.storage import VersionStore
    monkeypatch.setattr(pipeline_routes, "sm",
                        StateManager(VersionStore(isolated_dirs / "state.db")))
    return TestClient(app_module.app)


def test_starting_a_run_queues_it_rather_than_running_it(api):
    """The request returns in milliseconds; the render happens on a worker."""
    started = time.monotonic()
    res = api.post("/api/pipeline/run", json={"prompt": "A kite over a quiet town"})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "queued" and body["job_id"].startswith("job_")
    assert time.monotonic() - started < 2.0

    job = jobs.get(body["job_id"])
    assert job.status == "queued" and job.kind == "run_full"
    assert job.payload["prompt"] == "A kite over a quiet town"


def test_jobs_api_lists_filters_and_cancels(api):
    res = api.post("/api/pipeline/run", json={"prompt": "A kite over a quiet town"}).json()
    job_id = res["job_id"]

    listed = api.get("/api/jobs/", params={"project_id": res["project_id"]}).json()
    assert [j["id"] for j in listed] == [job_id]
    assert api.get("/api/jobs/", params={"status": "failed"}).json() == []

    assert api.post(f"/api/jobs/{job_id}/cancel").json()["status"] == "cancelled"
    assert api.get(f"/api/jobs/{job_id}").json()["status"] == "cancelled"
    assert api.get("/api/jobs/nope").status_code == 404


def test_status_reports_the_job_even_after_a_restart(api):
    res = api.post("/api/pipeline/run", json={"prompt": "A kite over a quiet town"}).json()
    jobs.append_event(res["job_id"], res["project_id"], phase="story",
                      status="started", message="writing the script", progress=0.2)

    db.dispose_all()

    status = api.get(f"/api/pipeline/status/{res['project_id']}").json()
    assert status["job_id"] == res["job_id"]
    assert status["phase"] == "story" and status["message"] == "writing the script"
    assert status["recent_events"][-1]["progress"] == pytest.approx(0.2)


def test_ready_reports_the_database(api):
    body = api.get("/ready").json()
    assert body["status"] == "ok" and body["database"] == "sqlite"
