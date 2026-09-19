"""M1 — backend fixes: thread-safe progress, Windows paths, languages, phase events."""
from __future__ import annotations
import asyncio
import threading
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.app import app
from backend.services import run_registry


def test_progress_events_from_worker_thread_arrive_promptly():
    """The consumer is already asleep in the event loop when a pipeline thread
    pushes; the old non-thread-safe put only surfaced at the loop's next timer
    (the 30 s WebSocket heartbeat in production)."""
    def late_push():
        time.sleep(0.3)
        run_registry.push_event("t_ws", {"phase": "story", "status": "started"})

    async def scenario():
        q = run_registry.subscribe("t_ws")
        started = time.monotonic()
        threading.Thread(target=late_push).start()
        ev = await asyncio.wait_for(q.get(), timeout=3.0)
        run_registry.unsubscribe("t_ws", q)
        return ev, time.monotonic() - started

    run_registry.create("t_ws")
    ev, elapsed = asyncio.run(scenario())
    assert ev["phase"] == "story"
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
