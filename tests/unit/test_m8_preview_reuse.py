"""M8 — the storyboard preview becomes the wide shot instead of being discarded."""
from __future__ import annotations

from pathlib import Path

import pytest

from agents.story_agent.planner import template_script
from agents.video_agent import VideoAgent
from shared.schemas.pipeline import PipelineState


@pytest.fixture
def planned(isolated_dirs):
    """A project with a storyboard, as `plan` leaves it."""
    state = PipelineState(project_id="t_reuse", user_prompt="A kite over a town")
    state.script = template_script("t_reuse", state.user_prompt,
                                   target_duration_s=20, scene_count=2)
    agent = VideoAgent()
    agent.generate_storyboard(state, width=320, height=180)
    return state, agent


def test_a_preview_is_drawn_at_the_size_the_render_needs(planned):
    """A 512x288 thumbnail could never be the wide shot; it had to be redrawn."""
    from shared.schemas.storyboard import Storyboard
    board = Storyboard(project_id="x", prompt="y")
    assert (board.preview_width, board.preview_height) == (1280, 720)


def test_the_wide_shot_reuses_the_preview_rather_than_paying_twice(planned, monkeypatch):
    state, agent = planned
    scene = state.script.scenes[0]
    frame = state.storyboard.frame(scene.scene_id)
    assert frame.preview_path and frame.preview_signature

    generated = []
    real = agent.tools.execute

    def counting(tool, **kwargs):
        if tool == "vision.generate_image":
            generated.append(kwargs.get("out_path"))
        return real(tool, **kwargs)

    monkeypatch.setattr(agent.tools, "execute", counting)

    reusable = agent.reusable_preview(state, scene, 320, 180)
    assert reusable == frame.preview_path

    bank = agent.generate_shot_bank("t_reuse", scene, 320, 180,
                                    story=state.script.story,
                                    reusable_preview=reusable)
    # Three framings, but only the two that are genuinely different images.
    assert len(bank) == 3
    assert len(generated) == 2, "the wide shot should not have been generated"
    assert Path(bank[0]).exists() and Path(bank[0]).stat().st_size > 0


def test_editing_a_scene_stops_the_preview_being_reused(planned):
    """A stale preview must not be served as the wide shot of a changed scene."""
    state, agent = planned
    scene = state.script.scenes[0]
    assert agent.reusable_preview(state, scene, 320, 180) is not None

    scene.visual_prompt = "something else entirely"
    assert agent.reusable_preview(state, scene, 320, 180) is None


def test_a_different_render_size_stops_the_reuse(planned):
    state, agent = planned
    scene = state.script.scenes[0]
    assert agent.reusable_preview(state, scene, 320, 180) is not None
    assert agent.reusable_preview(state, scene, 1280, 720) is None


def test_a_missing_preview_file_is_not_offered(planned):
    state, agent = planned
    scene = state.script.scenes[0]
    Path(state.storyboard.frame(scene.scene_id).preview_path).unlink()
    assert agent.reusable_preview(state, scene, 320, 180) is None


def test_a_reroll_still_draws_a_fresh_wide_shot(planned, monkeypatch):
    """`seed_salt` is how an edit asks for a different image; reuse must not
    quietly hand back the old one."""
    state, agent = planned
    scene = state.script.scenes[0]
    generated = []
    real = agent.tools.execute

    def counting(tool, **kwargs):
        if tool == "vision.generate_image":
            generated.append(kwargs.get("out_path"))
        return real(tool, **kwargs)

    monkeypatch.setattr(agent.tools, "execute", counting)
    agent.generate_shot_bank("t_reuse", scene, 320, 180, seed_salt="v2",
                             story=state.script.story,
                             reusable_preview=state.storyboard.frame(
                                 scene.scene_id).preview_path)
    assert len(generated) == 3
