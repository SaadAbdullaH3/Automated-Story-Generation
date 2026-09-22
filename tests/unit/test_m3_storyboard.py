"""M3 — plan a storyboard, edit it, then render the approved version."""
from __future__ import annotations
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agents.orchestrator import PipelineOrchestrator
from state_manager.state_manager import StateManager
from state_manager.storage import SqliteStorage
from tests.conftest import silence_tts


@pytest.fixture
def orchestrator(isolated_dirs):
    sm = StateManager(SqliteStorage(isolated_dirs / "state.db"))
    orch = PipelineOrchestrator(state_manager=sm)
    silence_tts(orch.audio.tools)
    return orch


def _plan(orch, **kwargs):
    return orch.plan("A night librarian finds the books rearranging into a map",
                     target_duration_s=24, scene_count=3,
                     preview_width=64, preview_height=36, **kwargs)


# ---- planning --------------------------------------------------------------

def test_plan_writes_a_script_and_one_preview_per_scene(orchestrator):
    state = _plan(orchestrator)
    board = state.storyboard
    assert state.stage == "storyboard"
    assert state.video is None and state.audio is None      # nothing expensive ran
    assert len(board.frames) == len(state.script.scenes) == 3
    for frame, scene in zip(board.frames, state.script.scenes):
        assert frame.scene_id == scene.scene_id
        assert Path(frame.preview_path).exists()
        assert [ln.text for ln in frame.dialogue] == [d.text for d in scene.dialogue]
        assert frame.dialogue[0].character_name                # names, not just ids
    assert board.estimated_duration_ms() > 0
    assert board.approved is False

    history = orchestrator.sm.history(state.project_id)
    assert history[-1]["description"] == "storyboard"


def test_plan_can_skip_previews(orchestrator):
    state = _plan(orchestrator, with_preview=False)
    assert state.script is not None and state.storyboard is None


# ---- editing ---------------------------------------------------------------

def test_editing_visuals_redraws_only_that_preview(orchestrator):
    state = _plan(orchestrator)
    pid = state.project_id
    stamps = {f.scene_id: Path(f.preview_path).stat().st_mtime_ns
              for f in state.storyboard.frames}

    updated = orchestrator.update_storyboard(
        pid, "scene_2", visual_prompt="a vaulted library at 3am, shelves sliding into a map")

    frame = updated.storyboard.frame("scene_2")
    assert frame.visual_prompt.startswith("a vaulted library")
    assert Path(frame.preview_path).stat().st_mtime_ns != stamps["scene_2"]
    for other in ("scene_1", "scene_3"):
        assert Path(updated.storyboard.frame(other).preview_path).stat().st_mtime_ns \
            == stamps[other]
    # The script itself changed, so the render will use the new visuals.
    scene = next(s for s in updated.script.scenes if s.scene_id == "scene_2")
    assert scene.visual_prompt == frame.visual_prompt


def test_editing_dialogue_updates_the_script_and_its_estimate(orchestrator):
    state = _plan(orchestrator)
    scene = next(s for s in state.script.scenes if s.dialogue)
    line = scene.dialogue[0]
    before = scene.duration_ms
    stamps = {f.scene_id: Path(f.preview_path).stat().st_mtime_ns
              for f in state.storyboard.frames}

    long_line = "The shelves moved again, and this time they spelled a street I walked as a child."
    updated = orchestrator.update_storyboard(
        state.project_id, scene.scene_id, dialogue={line.line_id: long_line})

    new_scene = next(s for s in updated.script.scenes if s.scene_id == scene.scene_id)
    assert new_scene.dialogue[0].text == long_line
    assert new_scene.duration_ms != before                   # re-estimated
    assert updated.storyboard.frame(scene.scene_id).dialogue[0].text == long_line
    # A text-only edit must not spend images on redrawing previews.
    assert {f.scene_id: Path(f.preview_path).stat().st_mtime_ns
            for f in updated.storyboard.frames} == stamps


@pytest.mark.parametrize("kwargs,message", [
    ({"scene_id": "scene_99"}, "unknown scene"),
    ({"scene_id": "scene_1", "dialogue": {"nope": "hi"}}, "unknown line"),
])
def test_bad_edits_are_rejected(orchestrator, kwargs, message):
    state = _plan(orchestrator)
    scene_id = kwargs.pop("scene_id")
    with pytest.raises(ValueError, match=message):
        orchestrator.update_storyboard(state.project_id, scene_id, **kwargs)


# ---- rendering -------------------------------------------------------------

def test_render_turns_an_approved_storyboard_into_a_film(orchestrator):
    state = _plan(orchestrator)
    rendered = orchestrator.render(state.project_id, with_bgm=False, with_subtitles=False)

    assert rendered.stage == "rendered"
    assert rendered.storyboard.approved is True
    assert Path(rendered.video.final_video_path).exists()
    assert rendered.audio is not None
    # The film follows the storyboard's scenes.
    assert [f.scene_id for f in rendered.video.frames] == \
        [f.scene_id for f in rendered.storyboard.frames]
    assert orchestrator.sm.history(state.project_id)[-1]["description"] == \
        "rendered from storyboard"


def test_render_needs_a_storyboard(orchestrator):
    with pytest.raises(ValueError, match="no storyboard"):
        orchestrator.render("not_a_project")


def test_edits_before_rendering_reach_the_film(orchestrator):
    state = _plan(orchestrator)
    scene = next(s for s in state.script.scenes if s.dialogue)
    orchestrator.update_storyboard(state.project_id, scene.scene_id,
                                   dialogue={scene.dialogue[0].line_id: "Rewritten for the film."})
    rendered = orchestrator.render(state.project_id, with_bgm=False, with_subtitles=False)
    spoken = [s.text for s in rendered.audio.manifest.segments]
    assert "Rewritten for the film." in spoken


# ---- HTTP API --------------------------------------------------------------

def test_storyboard_api_round_trip(isolated_dirs, monkeypatch):
    """plan -> GET storyboard -> PATCH a scene -> render, over HTTP."""
    from backend import app as app_module
    from backend.routes import pipeline as pipeline_routes
    from backend.services import pipeline_service

    sm = StateManager(SqliteStorage(isolated_dirs / "state.db"))
    orch = PipelineOrchestrator(state_manager=sm)
    silence_tts(orch.audio.tools)
    monkeypatch.setattr(pipeline_service, "_orchestrator", orch)
    monkeypatch.setattr(pipeline_routes, "sm", sm)

    client = TestClient(app_module.app)
    started = client.post("/api/pipeline/plan", json={
        "prompt": "A night librarian finds the books rearranging into a map",
        "target_duration_s": 24, "scene_count": 2,
    })
    assert started.status_code == 200
    pid = started.json()["project_id"]

    board = client.get(f"/api/pipeline/storyboard/{pid}").json()
    assert board["stage"] == "storyboard" and len(board["frames"]) == 2
    assert board["frames"][0]["preview_url"].startswith(f"/assets/{pid}/video/storyboard/")

    scene_id = board["frames"][0]["scene_id"]
    patched = client.patch(f"/api/pipeline/storyboard/{pid}/{scene_id}",
                           json={"title": "Opening: the moving shelves"})
    assert patched.status_code == 200
    assert patched.json()["frames"][0]["title"] == "Opening: the moving shelves"

    rendered = client.post(f"/api/pipeline/render/{pid}",
                           json={"with_bgm": False, "with_subtitles": False})
    assert rendered.status_code == 200
    state = sm.latest(pid)
    assert state.stage == "rendered" and Path(state.video.final_video_path).exists()


def test_storyboard_api_404s_for_unknown_projects(isolated_dirs):
    from backend.app import app
    client = TestClient(app)
    assert client.get("/api/pipeline/storyboard/nope").status_code == 404
    assert client.post("/api/pipeline/render/nope", json={}).status_code == 404
    assert client.patch("/api/pipeline/storyboard/nope/scene_1", json={}).status_code == 404


# ---- benchmark harness ------------------------------------------------------

def test_benchmark_runs_offline_and_reports(tmp_path, monkeypatch, isolated_dirs):
    """The harness must produce comparable numbers without any network."""
    import importlib.util
    import json
    import sys

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("benchmark", root / "scripts" / "benchmark.py")
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)

    monkeypatch.setattr(bench, "ROOT", tmp_path)          # write reports into the temp dir
    monkeypatch.setattr(bench, "_offline", lambda: None)  # conftest already forces offline
    monkeypatch.setenv("PROVIDER_TTS", "silent")
    monkeypatch.setattr(sys, "argv", [
        "benchmark.py", "--offline", "--prompts", "1", "--duration", "20", "--scenes", "2",
        "--no-subs", "--width", "160", "--height", "90", "--fps", "12",
        "--out", str(tmp_path / "report.md"),
    ])
    assert bench.main() == 0                              # non-zero would mean out of sync

    report = json.loads(next((tmp_path / "data" / "benchmarks").glob("*.json"))
                        .read_text(encoding="utf-8"))
    run = report["runs"][0]
    assert run["in_sync"] and run["frames_actual"] == run["frames_expected"]
    assert run["lines_on_a_cut"] == run["lines"]
    assert run["fallback_images"] == 0                    # the chosen provider served everything
    assert run["duration_error_pct"] < 25
    assert report["totals"]["all_in_sync"] is True
    assert "| prompt |" in (tmp_path / "report.md").read_text(encoding="utf-8")
