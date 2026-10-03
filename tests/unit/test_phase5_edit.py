"""Phase 5 — Edit-intent classification, planning, and end-to-end edit + revert.

Spec requires test coverage across at least 10 edit query types — this file
exercises 14 distinct queries against the keyword classifier (offline path).
"""
from __future__ import annotations
import os
from pathlib import Path

import pytest

from agents.edit_agent.intent_classifier import IntentClassifier, classify
from agents.edit_agent.planner import plan
from shared.schemas.edit import EditIntent

# Phrasings a creator actually uses, one or more per kind of edit, classified
# by the offline keyword path.
EDIT_QUERIES = [
    ("Change voice tone to whispered",        "audio",       "change_voice_tone"),
    ("change voice for the narrator",         "audio",       "change_voice"),
    ("make it louder",                        "audio",       "adjust_volume"),
    ("add background music ambient",          "audio",       "add_background_music"),
    ("remove background music",               "audio",       "remove_background_music"),
    ("regenerate the audio",                  "audio",       "regenerate_audio"),
    ("make scene 2 darker",                   "video_frame", "adjust_scene_aesthetic"),
    ("change character design",               "video_frame", "change_character_design"),
    ("regenerate scene 1",                    "video_frame", "regenerate_scene"),
    ("apply a vintage filter",                "video_frame", "apply_filter"),
    ("noir style",                            "video_frame", "apply_filter"),
    ("remove the subtitles",                  "video",       "remove_subtitles"),
    ("add subtitles please",                  "video",       "add_subtitles"),
    ("speed up this scene",                   "video",       "speed_up"),
    ("slow down the video",                   "video",       "slow_down"),
    ("recompose the video",                   "video",       "recompose_video"),
    ("regenerate the script",                 "script",      "regenerate_script"),
    ("change the genre to comedy",            "script",      "change_genre"),
]


@pytest.mark.parametrize("query,target,intent", EDIT_QUERIES)
def test_classifier_query(query, target, intent):
    out = IntentClassifier().classify(query)
    assert isinstance(out, EditIntent)
    assert out.target == target, f"target mismatch for '{query}': {out}"
    assert out.intent == intent, f"intent mismatch for '{query}': {out}"


def test_classifier_extracts_scene_scope():
    out = classify("make scene 3 darker")
    assert out.scope == "scene:scene_3"


def test_classifier_extracts_filter_param():
    out = classify("apply a sepia filter")
    assert out.parameters.get("filter") == "sepia"


def test_classifier_extracts_tone_param():
    out = classify("change voice tone to whispered")
    assert out.parameters.get("tone") == "whispered"


def test_planner_audio_step_chains_recompose():
    intent = EditIntent(intent="change_voice_tone", target="audio", scope="character:char_protagonist",
                        parameters={"tone": "whispered"})
    steps = plan(intent)
    assert steps[0].name == "rerun_audio"
    # Audio edits should always be followed by a video recomposition.
    assert any(s.name == "recompose_video" for s in steps)


def test_planner_script_step_cascades():
    intent = EditIntent(intent="regenerate_script", target="script", scope="global")
    steps = plan(intent)
    names = [s.name for s in steps]
    # Spec requirement: script edits cascade through audio + video.
    assert "regenerate_script" in names
    assert "rerun_audio" in names
    assert "rerun_video" in names


# ---- end-to-end edit + revert (slow but covers state versioning) ------------

def test_edit_and_revert_cycle(tmp_path, monkeypatch):
    import shared.constants as constants
    monkeypatch.setattr(constants, "OUTPUTS_DIR", tmp_path / "out")
    monkeypatch.setattr(constants, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(constants, "DB_PATH", tmp_path / "state.db")
    constants.OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    constants.STATE_DIR.mkdir(parents=True, exist_ok=True)

    # Re-import to pick up the new DB_PATH.
    from state_manager.storage import SqliteStorage
    from state_manager.state_manager import StateManager
    sm = StateManager(SqliteStorage(tmp_path / "state.db"))

    from agents.story_agent.planner import template_script
    from shared.schemas.pipeline import PipelineState

    state = PipelineState(project_id="t_edit", user_prompt="A balloon escapes")
    state.script = template_script(state.project_id, state.user_prompt, target_duration_s=20)
    sm.snapshot(state, asset_paths=[], description="v1")

    # Snapshot v2 (simulating a tweak).
    state.metadata["edit"] = "tweak_1"
    sm.snapshot(state, asset_paths=[], description="v2")

    history = sm.history("t_edit")
    assert len(history) == 2

    # Revert to v1 — should produce v3 (the revert itself).
    restored = sm.revert("t_edit", 1)
    assert restored.metadata.get("reverted_from") == 1
    history = sm.history("t_edit")
    assert len(history) == 3
    assert history[-1]["edit_intent"]["intent"] == "revert"
