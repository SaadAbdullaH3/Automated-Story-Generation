"""M1 — edit agent fixes: voices, filters, subtitles, speed, snapshots, revert."""
from __future__ import annotations
import filecmp
from pathlib import Path

import pytest

from agents.edit_agent import EditAgent
from agents.edit_agent.intent_classifier import classify
from agents.edit_agent.planner import plan
from mcp.tool_executor import ToolExecutor
from mcp.tools.audio_tools.tts_tool import edge_prosody
from shared.schemas.edit import EditCommand, EditIntent
from state_manager.snapshot import referenced_files
from tests.conftest import silence_tts


def _edit_agent(sm):
    agent = EditAgent(sm)
    calls = silence_tts(agent.executor.audio.tools)
    return agent, calls


# ---- voice ---------------------------------------------------------------

def test_edge_prosody_mapping():
    assert edge_prosody(175, 0, 1.0) == {"rate": "+0%", "pitch": "+0Hz", "volume": "+0%"}
    assert edge_prosody(140, -5, 0.7) == {"rate": "-20%", "pitch": "-5Hz", "volume": "-30%"}


def test_edge_tts_runs_via_python_with_prosody(tmp_path, monkeypatch):
    import subprocess
    import sys
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        out = next(a.split("=", 1)[1] for a in cmd if a.startswith("--write-media="))
        Path(out).write_bytes(b"ID3fake")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    res = ToolExecutor().execute("audio.tts", text="-starts with a dash", engine="edge",
                                 out_path=str(tmp_path / "x.mp3"), voice="en-US-AriaNeural",
                                 rate=140, pitch=-5, volume=0.7)
    assert res.success, res.error
    cmd = captured["cmd"]
    assert cmd[:3] == [sys.executable, "-m", "edge_tts"]
    assert "--voice=en-US-AriaNeural" in cmd and "--rate=-20%" in cmd
    assert "--text=-starts with a dash" in cmd


def test_voice_edit_keeps_character_voice_and_applies_tone(small_project):
    state, sm = small_project()
    agent, calls = _edit_agent(sm)
    aria_voice = next(v.voice_id for v in state.audio.voice_configs
                      if v.character_id == "char_protagonist")

    result = agent.edit(EditCommand(project_id=state.project_id,
                                    query="change aria's voice tone to whispered"))
    assert result.success, result.error
    assert result.intent.scope == "character:char_protagonist"
    assert calls, "no lines were re-recorded"
    assert all(c["voice"] == aria_voice for c in calls)
    assert all(c["rate"] == 140 and c["volume"] == 0.7 for c in calls)
    # Only Aria's lines were re-recorded.
    aria_lines = [s for s in state.audio.manifest.segments if s.character_id == "char_protagonist"]
    assert len(calls) == len(aria_lines)


# ---- filters -------------------------------------------------------------

def test_style_presets_apply_and_unknown_filters_fail(tmp_path):
    img = tmp_path / "a.png"
    tools = ToolExecutor()
    tools.execute("vision.generate_image", prompt="x", out_path=str(img), width=64, height=36)
    ok = tools.execute("vision.edit_image", in_path=str(img), out_path=str(tmp_path / "b.png"),
                       filters=["noir"])
    assert ok.success and ok.metadata["applied"] == ["grayscale", "contrast"]
    bad = tools.execute("vision.edit_image", in_path=str(img), out_path=str(tmp_path / "c.png"),
                        filters=["sparkly"])
    assert not bad.success and "unknown filter" in bad.error


@pytest.mark.parametrize("query,expected", [
    ("apply a cold thriller style", "cold_thriller"),
    ("give it a cinematic filter", "cinematic"),
    ("apply the school filter", None),          # "cool" must not match inside "school"
])
def test_filter_names_match_whole_words(query, expected):
    assert classify(query).parameters.get("filter") == expected


def test_scene_filter_changes_only_that_scene(small_project):
    state, sm = small_project()
    agent, _ = _edit_agent(sm)
    before = {f.scene_id: f.plan_signature for f in state.video.frames}
    result = agent.edit(EditCommand(project_id=state.project_id, query="make scene 2 darker"))
    assert result.success, result.error
    after = sm.latest(state.project_id)
    changed = {f.scene_id for f in after.video.frames if f.plan_signature != before[f.scene_id]}
    assert changed == {"scene_2"}
    assert all(p.endswith("__scene_2.png") for p in after.video.frames[1].portrait_overrides.values())


# ---- planner -------------------------------------------------------------

def test_slow_down_2x_halves_speed():
    steps = plan(EditIntent(intent="slow_down", target="video", parameters={"factor": 2.0}))
    assert steps[0].params["factor"] == 0.5


def test_character_design_regenerates_portraits():
    steps = plan(EditIntent(intent="change_character_design", target="video_frame"))
    assert [s.name for s in steps] == ["regenerate_portraits"]


# ---- subtitles + speed survive edits; snapshots capture everything ----------

def test_edits_keep_multilang_subtitles_and_speed(small_project, fake_translation):
    state, sm = small_project(subtitle_language="French")
    agent, _ = _edit_agent(sm)

    r = agent.edit(EditCommand(project_id=state.project_id, query="speed up 1.25x"))
    assert r.success, r.error
    s = sm.latest(state.project_id)
    assert s.video.speed_factor == 1.25
    assert s.video.subtitle_languages == ["French", "English"]

    r = agent.edit(EditCommand(project_id=state.project_id, query="apply vintage filter"))
    assert r.success, r.error
    s = sm.latest(state.project_id)
    assert s.video.speed_factor == 1.25                       # speed kept
    assert s.video.subtitle_languages == ["French", "English"]  # tracks kept
    assert fake_translation == ["French"]                     # translation cached

    r = agent.edit(EditCommand(project_id=state.project_id, query="remove the subtitles"))
    assert r.success, r.error
    s = sm.latest(state.project_id)
    assert s.video.has_subtitles is False and s.video.subtitle_languages == []
    assert Path(s.video.final_video_path).name == "final_output_speed.mp4"


def test_snapshot_contains_every_referenced_file(small_project):
    state, sm = small_project()
    agent, _ = _edit_agent(sm)
    assert agent.edit(EditCommand(project_id=state.project_id, query="noir style")).success
    latest = sm.latest(state.project_id)
    snap = sm.history(state.project_id)[-1]["asset_paths"]
    names = {Path(p).name for p in snap}
    for path in referenced_files(latest):
        assert Path(path).name in names, f"{path} missing from snapshot"


def test_revert_restores_previous_video(small_project):
    state, sm = small_project()
    v1_video = Path(state.video.final_video_path)
    v1_copy = v1_video.with_name("v1_copy.mp4")
    v1_copy.write_bytes(v1_video.read_bytes())

    agent, _ = _edit_agent(sm)
    assert agent.edit(EditCommand(project_id=state.project_id, query="noir style")).success
    assert not filecmp.cmp(v1_video, v1_copy, shallow=False)   # edit changed the film

    reverted = agent.revert(state.project_id, 1)
    assert filecmp.cmp(reverted.video.final_video_path, v1_copy, shallow=False)
