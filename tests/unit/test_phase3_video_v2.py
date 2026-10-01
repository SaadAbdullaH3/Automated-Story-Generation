"""Phase 3 v2 — multi-shot composition + lip-sync heuristic tests."""
from __future__ import annotations
from pathlib import Path

from agents.video_agent import VideoAgent
from agents.video_agent.animator import (
    Shot as RenderShot, MOTION_PRESETS, assemble_scene,
    pick_motion_for_index, probe_duration_ms, render_shot,
)
from agents.audio_agent import AudioAgent
from agents.story_agent.planner import template_script
from mcp.tool_executor import ToolExecutor
from shared.schemas.pipeline import PipelineState


def _patch_silent_tts(agent):
    real = agent.tools.execute
    def patched(tool, **kwargs):
        if tool == "audio.tts":
            kwargs["engine"] = "silent"
        return real(tool, **kwargs)
    agent.tools.execute = patched


def test_render_shot_creates_clip(tmp_path):
    img = tmp_path / "src.png"
    ToolExecutor().execute("vision.generate_image", prompt="a dramatic sunset",
                           out_path=str(img), width=480, height=270)
    out = tmp_path / "shot.mp4"
    rs = RenderShot(image_path=str(img), duration_ms=1500, motion="ken_burns_diag")
    p = render_shot(rs, out, width=480, height=270, fps=12,
                    add_grain=False, add_vignette=False)
    assert p.exists()
    dur = probe_duration_ms(p)
    assert dur is not None and 1200 <= dur <= 1800


def test_assemble_scene_crossfades_two_shots(tmp_path):
    img = tmp_path / "src.png"
    ToolExecutor().execute("vision.generate_image", prompt="a quiet shore",
                           out_path=str(img), width=480, height=270)
    shots = []
    for i, motion in enumerate(MOTION_PRESETS[:2]):
        rs = RenderShot(image_path=str(img), duration_ms=1200, motion=motion)
        p = render_shot(rs, tmp_path / f"s{i}.mp4", 480, 270, 12,
                        add_grain=False, add_vignette=False)
        shots.append(p)
    out = tmp_path / "scene.mp4"
    assemble_scene(shots, out, crossfade_ms=200)
    assert out.exists()
    dur = probe_duration_ms(out)
    # Expect ~ 1.2 + 1.2 - 0.2 (xfade overlap) = 2.2s.
    assert dur is not None and 1900 <= dur <= 2500


def test_motion_picker_distinct_for_adjacent_indices():
    a = pick_motion_for_index(0, 0)
    b = pick_motion_for_index(0, 1)
    c = pick_motion_for_index(0, 2)
    assert a != b and b != c


def test_lip_sync_heuristic_creates_clip(tmp_path):
    """Heuristic talking-head fallback runs offline."""
    img = tmp_path / "portrait.png"
    aud = tmp_path / "voice.wav"
    ToolExecutor().execute("vision.generate_image", prompt="character portrait",
                           out_path=str(img), width=480, height=270)
    ToolExecutor().execute("audio.tts", text="hello there my friend",
                           out_path=str(aud), engine="silent")
    out = tmp_path / "talking.mp4"
    res = ToolExecutor().execute(
        "vision.lip_sync",
        image_path=str(img), audio_path=str(aud), out_path=str(out),
        width=480, height=270, fps=12,
    )
    assert res.success, res.error
    assert Path(res.data).exists()
    assert res.metadata["provider"] == "heuristic"


def test_text_to_video_returns_failure_without_keys(tmp_path, monkeypatch):
    """Without any provider key, real motion politely fails and the caller
    falls back to the offline camera moves."""
    for key in ("FAL_KEY", "FAL_API_KEY", "REPLICATE_API_TOKEN",
                "HF_TOKEN", "HUGGINGFACE_API_KEY", "VIDEO_BUDGET_OK"):
        monkeypatch.delenv(key, raising=False)
    res = ToolExecutor().execute(
        "vision.text_to_video", prompt="ocean waves",
        out_path=str(tmp_path / "t2v.mp4"),
    )
    assert res.success is False
    # Since M6 the message points at the chain that decides this.
    assert "no real-motion provider" in (res.error or "").lower()
    assert "providers.yaml" in (res.error or "")


def test_video_agent_v2_produces_multishot_composition(tmp_path, monkeypatch):
    """Full Phase 3 run — verifies multi-shot composition + character portraits."""
    import shared.constants as constants
    monkeypatch.setattr(constants, "OUTPUTS_DIR", tmp_path / "out")
    constants.OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    state = PipelineState(project_id="t_phase3_v2", user_prompt="A whale sings")
    state.script = template_script("t_phase3_v2", state.user_prompt, target_duration_s=16)

    audio = AudioAgent()
    _patch_silent_tts(audio)
    audio.run(state, with_bgm=False)

    video = VideoAgent()
    out = video.run(state, with_subtitles=False, width=320, height=180, fps=12,
                    use_text_to_video=False, use_lip_sync=False,
                    cinematic_post=False)
    assert state.phase3.status == "complete"
    assert Path(out.final_video_path).exists()
    # Multi-shot: each frame should have at least one shot, and dialogue scenes
    # should produce more shots than scenes.
    total_shots = sum(len(f.shots) for f in out.frames)
    assert total_shots >= len(state.script.scenes)
    # Portraits generated for every character in the cast.
    assert len(out.portraits) == len(state.script.characters.characters)
