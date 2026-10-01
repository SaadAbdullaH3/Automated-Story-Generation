"""M1 — backend fixes: live progress, Windows paths, languages, phase events."""
from __future__ import annotations
import asyncio
import threading
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

import jobs
from backend.app import app
from backend.services import progress


def test_progress_events_from_worker_thread_arrive_promptly(isolated_dirs):
    """A consumer already waiting on the stream must see an event pushed from a
    worker thread within a beat — the original bug surfaced events only at the
    next 30 s WebSocket heartbeat. Since M4 the two sides can also be different
    processes, so the event travels through the database rather than a queue."""
    job = jobs.enqueue("plan", "t_ws", {"prompt": "x"})

    def late_push():
        time.sleep(0.3)
        jobs.append_event(job.id, "t_ws", phase="story", status="started",
                          message="writing", progress=0.1)

    async def scenario():
        stream = progress.stream("t_ws")
        started = time.monotonic()
        threading.Thread(target=late_push).start()
        async for envelope in stream:
            if envelope["type"] == "event":
                return envelope["data"], time.monotonic() - started
        raise AssertionError("stream ended without an event")

    event, elapsed = asyncio.run(asyncio.wait_for(scenario(), timeout=5.0))
    assert event["phase"] == "story"
    assert elapsed < 1.0


def test_projects_video_url_handles_windows_paths(monkeypatch):
    from backend.routes import projects
    state = SimpleNamespace(
        script=None, user_prompt="p", version=2, updated_at="now",
        video=SimpleNamespace(final_video_path=r"C:\data\outputs\pid\final_output_multilang.mp4"),
    )
    fake_sm = SimpleNamespace(list_projects=lambda: ["pid"], latest=lambda pid: state)
    monkeypatch.setattr(projects, "sm", fake_sm)
    rows = TestClient(app).get("/api/projects/").json()
    assert rows[0]["video_url"] == "/assets/pid/final_output_multilang.mp4"


def test_languages_endpoint_matches_shared_table():
    from shared.languages import supported_names
    langs = TestClient(app).get("/api/pipeline/languages").json()
    assert langs == supported_names()
    assert "Urdu" in langs and "Japanese" in langs


def test_pipeline_emits_started_and_complete_per_phase(isolated_dirs):
    from agents.orchestrator import PipelineOrchestrator
    from state_manager.state_manager import StateManager
    from state_manager.storage import SqliteStorage
    from tests.conftest import silence_tts

    orch = PipelineOrchestrator(state_manager=StateManager(SqliteStorage(isolated_dirs / "db")))
    silence_tts(orch.audio.tools)
    events = []
    orch.run_full("A kite flies over a quiet town", on_event=events.append,
                  target_duration_s=20, scene_count=2, with_bgm=False, with_subtitles=False)
    seen = [(e.phase, e.status) for e in events]
    for phase in ("story", "audio", "video"):
        assert (phase, "started") in seen and (phase, "complete") in seen
    assert seen[-1] == ("complete", "complete")
    progress = [e.progress for e in events]
    assert progress == sorted(progress)
