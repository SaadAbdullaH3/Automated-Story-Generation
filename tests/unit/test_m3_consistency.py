"""M3 — a character keeps the same face until someone asks for a new design."""
from __future__ import annotations

import pytest

from agents.story_agent.appearance import (
    build_appearance_lock, character_seed, lock_appearances,
)
from agents.story_agent.planner import template_script
from mcp.tool_executor import ToolExecutor
from shared.schemas.story import Character


def _character(**kwargs) -> Character:
    base = dict(id="char_x", name="Mira", role="protagonist",
                description="A cartographer who never sleeps.",
                visual_description="a tall cartographer", voice_age="young")
    return Character(**{**base, **kwargs})


# ---- the lock itself -------------------------------------------------------

def test_vague_descriptions_get_stable_details():
    c = _character()
    lock = build_appearance_lock(c)
    assert "Mira" in lock and "a tall cartographer" in lock
    assert any(word in lock for word in ("hair", "bald"))     # filled in
    assert "eyes" in lock
    assert build_appearance_lock(c) == lock                   # deterministic


def test_details_the_writer_gave_are_not_overridden():
    c = _character(visual_description="short silver hair, sharp green eyes, "
                                      "a long navy coat, a scar on her chin")
    lock = build_appearance_lock(c)
    assert "short silver hair" in lock and "green eyes" in lock
    # Nothing invented on top of what was already described.
    for invented in ("copper hair", "amber eyes", "moss-green coat", "round wire glasses"):
        assert invented not in lock


def test_different_characters_get_different_looks():
    looks = {build_appearance_lock(_character(id=f"char_{i}", name=n))
             for i, n in enumerate(["Mira", "Jonah", "Wren", "Idris"])}
    assert len(looks) == 4


def test_a_salt_rerolls_the_look_and_the_seed():
    c = _character()
    assert build_appearance_lock(c, "v2") != build_appearance_lock(c)
    assert character_seed(c, "v2") != character_seed(c)


def test_locking_a_script_is_idempotent():
    script = template_script("p", "A librarian maps a shifting city",
                             target_duration_s=24, scene_count=3)
    lock_appearances(script)
    first = [(c.appearance_lock, c.image_seed) for c in script.characters.characters]
    lock_appearances(script)
    assert [(c.appearance_lock, c.image_seed) for c in script.characters.characters] == first
    assert all(seed is not None for _, seed in first)


# ---- how it reaches the images ---------------------------------------------

@pytest.fixture
def capture_images(monkeypatch):
    """Record every image request the video agent makes."""
    calls = []
    real = ToolExecutor.execute

    def spy(self, tool, **kwargs):
        if tool == "vision.generate_image":
            calls.append(kwargs)
        return real(self, tool, **kwargs)

    monkeypatch.setattr(ToolExecutor, "execute", spy)
    return calls


def test_portraits_use_the_locked_look_and_seed(isolated_dirs, capture_images):
    from agents.story_agent import StoryAgent
    from agents.video_agent import VideoAgent
    from shared.schemas.pipeline import PipelineState

    state = PipelineState(project_id="t_lock", user_prompt="A librarian maps a city")
    StoryAgent().run(state, target_duration_s=24, scene_count=2)
    hero = next(c for c in state.script.characters.characters if c.role == "protagonist")

    agent = VideoAgent()
    agent.generate_portrait(state.project_id, hero, 64, 36)
    first = capture_images[-1]
    assert first["seed"] == hero.image_seed
    assert hero.appearance_lock in first["prompt"]

    # Re-rendering later asks for exactly the same picture.
    capture_images.clear()
    agent.generate_portrait(state.project_id, hero, 64, 36)
    assert capture_images[-1]["prompt"] == first["prompt"]
    assert capture_images[-1]["seed"] == first["seed"]


def test_change_character_design_sticks_across_later_renders(small_project, capture_images):
    from agents.edit_agent import EditAgent
    from shared.schemas.edit import EditCommand
    from tests.conftest import silence_tts

    state, sm = small_project()
    hero = next(c for c in state.script.characters.characters if c.role == "protagonist")
    before_seed, before_lock = hero.image_seed, hero.appearance_lock

    agent = EditAgent(sm)
    silence_tts(agent.executor.audio.tools)
    result = agent.edit(EditCommand(project_id=state.project_id,
                                    query="change character design"))
    assert result.success, result.error

    edited = sm.latest(state.project_id)
    new_hero = next(c for c in edited.script.characters.characters if c.id == hero.id)
    assert new_hero.image_seed != before_seed          # a new design was asked for
    assert new_hero.appearance_lock != before_lock

    # A later re-render keeps the NEW design instead of reverting to the old one.
    capture_images.clear()
    from agents.video_agent import VideoAgent
    VideoAgent().generate_portrait(edited.project_id, new_hero, 64, 36)
    assert capture_images[-1]["seed"] == new_hero.image_seed
    assert new_hero.appearance_lock in capture_images[-1]["prompt"]
