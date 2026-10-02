"""M8 — the storyboard streams in, and the preview really is the wide shot."""
from __future__ import annotations

import pytest

from agents.orchestrator import PipelineOrchestrator
from agents.orchestrator.state import RunContext
from shared.schemas.pipeline import PipelineState
from state_manager.state_manager import StateManager
from state_manager.storage import VersionStore


@pytest.fixture
def orch(isolated_dirs):
    return PipelineOrchestrator(
        state_manager=StateManager(VersionStore(isolated_dirs / "state.db")))


def _plan(orch, events):
    return orch.plan("A clockmaker in a flooded city repairs the hours people lose",
                     on_event=events.append, target_duration_s=20, scene_count=2)


# ---- the bug this file was written for ---------------------------------------

def test_a_planned_preview_is_reusable_by_the_render_that_follows(orch):
    """The schema said previews were 1280x720, but `plan()` still defaulted to
    512x288 and overrode it, so in the real plan -> render path the signatures
    never matched and every wide shot was paid for twice. A test that drew the
    storyboard directly at a matching size passed anyway; this one goes through
    `plan` and asks with the size `render` will actually use."""
    state = _plan(orch, [])
    render = RunContext(state=PipelineState(project_id="x", user_prompt="y"))

    assert (state.storyboard.preview_width,
            state.storyboard.preview_height) == (render.width, render.height)
    for scene in state.script.scenes:
        assert orch.video.reusable_preview(state, scene, render.width,
                                           render.height) is not None, scene.scene_id


# ---- streaming -----------------------------------------------------------------

def test_the_script_arrives_before_any_picture(orch):
    """The whole point of the creator layout: every scene, its tone and its
    lines are on screen while the frames are still being drawn."""
    events = []
    _plan(orch, events)
    kinds = [(e.phase, e.status) for e in events]

    script_at = kinds.index(("storyboard", "script"))
    frames_at = [i for i, k in enumerate(kinds) if k == ("storyboard", "frame")]
    assert frames_at, "no frame events"
    assert script_at < min(frames_at)


def test_the_script_event_carries_everything_a_card_shows(orch):
    events = []
    state = _plan(orch, events)
    script = next(e for e in events if (e.phase, e.status) == ("storyboard", "script"))
    cards = script.payload["scenes"]

    assert script.payload["title"] == state.script.story.title
    assert [c["scene_id"] for c in cards] == [s.scene_id for s in state.script.scenes]
    for card, scene in zip(cards, state.script.scenes):
        assert card["title"] == scene.title
        assert card["tone"] == (scene.tone or "")
        assert card["move"]                       # a real camera move, named
        assert len(card["lines"]) == len(scene.dialogue)
        for line in card["lines"]:
            assert line["character"] and line["text"]
            assert "voice" in line


def test_every_scene_gets_exactly_one_frame_event_with_a_url(orch):
    events = []
    state = _plan(orch, events)
    frames = [e for e in events if (e.phase, e.status) == ("storyboard", "frame")]

    assert sorted(e.payload["scene_id"] for e in frames) == \
        sorted(s.scene_id for s in state.script.scenes)
    for e in frames:
        assert e.payload["preview_url"].startswith("/assets/")
    # Progress only ever moves forward, so the bar never jumps back.
    progress = [e.progress for e in events]
    assert progress == sorted(progress)


def test_a_reloaded_storyboard_shows_the_same_cards_as_the_stream(orch, isolated_dirs,
                                                                  monkeypatch):
    """Two paths to the same cards — streamed while drawing, read back on
    reload — must not drift apart."""
    from backend import app as app_module
    from backend.routes import pipeline as pipeline_routes
    from tests.conftest import signed_in_client

    events = []
    state = _plan(orch, events)
    streamed = next(e for e in events
                    if (e.phase, e.status) == ("storyboard", "script")).payload["scenes"]

    monkeypatch.setattr(pipeline_routes, "sm", orch.sm)
    client = signed_in_client(app_module.app)
    board = client.get(f"/api/pipeline/storyboard/{state.project_id}").json()

    for card, frame in zip(streamed, board["frames"]):
        assert frame["tone"] == card["tone"]
        assert frame["move"] == card["move"]
        assert frame["lines"] == card["lines"]
        assert frame["preview_url"].startswith("/assets/")


# ---- tone reaches the camera in a real render ----------------------------------

def test_a_scenes_tone_shapes_its_camera_moves_in_a_real_render(small_project):
    """M6 claimed tone drives the camera, and its test checked
    move_for("detail", "tense") — but the video agent never makes a "detail"
    shot. It only makes establishing, character and lip_sync shots, all of
    which were routed to fixed lists, so in a real render a scene's mood never
    reached the camera. This goes through the actual render and reads back the
    moves the agent chose."""
    from agents.video_agent import camera

    state, _sm = small_project(scenes=3)
    tones = {s.scene_id: camera.canonical_tone(s.tone) for s in state.script.scenes}
    assert len(set(tones.values())) > 1, "the template should vary its tones"

    openings = {}
    for frame in state.video.frames:
        mood = tones[frame.scene_id]
        establishing = [s for s in frame.shots if s.kind == "establishing"]
        assert establishing, frame.scene_id
        for shot in establishing:
            # Every establishing move comes from that scene's mood.
            assert shot.motion in camera.TONE_MOVES[mood], (
                f"{frame.scene_id} ({mood}) opened on {shot.motion}")
        openings[frame.scene_id] = establishing[0].motion

    # And different moods really do open differently.
    assert len(set(openings.values())) > 1, openings
