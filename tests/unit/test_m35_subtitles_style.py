"""M3.5 — subtitles players actually show, and images that match the story."""
from __future__ import annotations
import json
import subprocess
from pathlib import Path

import pytest

from agents.story_agent.planner import template_script
from agents.story_agent.visual_style import portrait_style, scene_style, style_for
from mcp.tool_executor import ToolExecutor
from mcp.tools.video_tools.subtitle_tool import to_webvtt


def _streams(path: str, kind: str) -> list:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=index,codec_type:stream_tags=language", "-of", "json", str(path)],
        capture_output=True, text=True, check=True)
    return [s for s in json.loads(probe.stdout)["streams"] if s["codec_type"] == kind]


# ---- subtitles -------------------------------------------------------------

def test_chosen_language_is_burned_in_and_others_stay_switchable(small_project,
                                                                 fake_translation):
    state, _ = small_project(subtitle_language="Urdu")
    video = state.video
    assert video.burned_subtitle_language == "Urdu"
    # The burned language is not also a soft track (that would double up on screen).
    soft = [s["tags"]["language"] for s in _streams(video.final_video_path, "subtitle")]
    assert "urd" not in soft and "eng" in soft
    assert video.subtitle_languages == ["Urdu", "English"]


def test_burn_in_can_be_turned_off(small_project):
    state, _ = small_project(subtitle_language="English", burn_subtitles=False)
    assert state.video.burned_subtitle_language is None
    assert [s["tags"]["language"] for s in
            _streams(state.video.final_video_path, "subtitle")] == ["eng"]


def test_sidecar_files_are_written_for_players_and_browsers(small_project, fake_translation):
    state, _ = small_project(subtitle_language="Urdu")
    proj = Path(state.video.final_video_path).parent
    for lang in ("urdu", "english"):
        assert (proj / "subtitles" / f"{lang}.srt").read_text(encoding="utf-8").strip()
        assert (proj / "subtitles" / f"{lang}.vtt").read_text(
            encoding="utf-8").startswith("WEBVTT")
    # VLC loads a sidecar named after the video file.
    assert (proj / "final_output_multilang.ur.srt").exists()
    assert (proj / "final_output_multilang.en.srt").exists()


def test_webvtt_format():
    vtt = to_webvtt([{"start_ms": 1500, "end_ms": 3250, "text": "hello"}])
    assert vtt.splitlines()[0] == "WEBVTT"
    assert "00:00:01.500 --> 00:00:03.250" in vtt      # dots, not commas


def test_burned_subtitles_use_a_font_that_has_the_script(tmp_path):
    """Latin-only fonts drop Urdu glyphs silently. Which font has them depends
    on the machine — Windows has Segoe UI, the container has Noto."""
    from shared import fonts
    img = tmp_path / "f.png"
    tools = ToolExecutor()
    tools.execute("vision.generate_image", prompt="x", out_path=str(img),
                  width=320, height=180)
    clip = tmp_path / "c.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(img),
                    "-t", "2", "-r", "12", "-pix_fmt", "yuv420p", str(clip)], check=True)
    res = tools.execute("video.subtitle", in_path=str(clip), out_path=str(tmp_path / "o.mp4"),
                        lines=[{"start_ms": 0, "end_ms": 1500, "text": "ہیلو"}],
                        language="Urdu")
    assert res.success, res.error
    assert res.metadata["font"] == fonts.font_for("Urdu")
    assert res.metadata["font"] in fonts.WIDE_SCRIPT_FONTS


def test_subtitle_tracks_endpoint(small_project, fake_translation):
    from backend.app import app
    from backend.routes import pipeline as routes
    from tests.conftest import signed_in_client

    state, sm = small_project(subtitle_language="Urdu")
    client = signed_in_client(app)
    routes.sm = sm
    tracks = client.get(f"/api/pipeline/subtitles/{state.project_id}").json()
    by_lang = {t["language"]: t for t in tracks}
    assert by_lang["Urdu"]["burned_in"] is True         # the player shouldn't re-add it
    assert by_lang["English"]["burned_in"] is False
    assert by_lang["English"]["url"].endswith("/subtitles/english.vtt")
    assert by_lang["English"]["code"] == "en"


# ---- visual style ----------------------------------------------------------

@pytest.mark.parametrize("prompt,expect_in_style,not_in_style", [
    ("A young astronaut discovers a hidden ocean on Mars", "science-fiction", "anime"),
    ("A ghost haunts an empty school at night", "horror", "anime"),
    ("A dragon befriends a village child", "fantasy", "photograph"),
])
def test_style_follows_the_story(prompt, expect_in_style, not_in_style):
    script = template_script("p", prompt, target_duration_s=24, scene_count=3)
    style, negative = scene_style(script.story, script.scenes[0].tone)
    assert expect_in_style in style
    assert not_in_style in negative          # the wrong look is explicitly excluded


def test_the_story_can_name_its_own_look():
    script = template_script("p", "A robot learns to paint", target_duration_s=24, scene_count=2)
    script.story.visual_style = "charcoal sketch on grey paper, single red accent"
    style, _ = style_for(script.story)
    assert style.startswith("charcoal sketch on grey paper")


def test_env_override_wins(monkeypatch):
    script = template_script("p", "A ghost story", target_duration_s=24, scene_count=2)
    monkeypatch.setenv("VIDEO_STYLE", "1970s technicolor film still")
    assert style_for(script.story)[0].startswith("1970s technicolor film still")


def test_scene_tone_and_portrait_framing_reach_the_prompt():
    script = template_script("p", "A ghost haunts a school", target_duration_s=24, scene_count=2)
    tense, _ = scene_style(script.story, "tense")
    assert "hard shadows" in tense
    assert "character portrait" in portrait_style(script.story)[0]


def test_images_are_requested_in_the_films_style(isolated_dirs, monkeypatch):
    from agents.story_agent import StoryAgent
    from agents.video_agent import VideoAgent
    from shared.schemas.pipeline import PipelineState

    calls = []
    real = ToolExecutor.execute

    def spy(self, tool, **kwargs):
        if tool == "vision.generate_image":
            calls.append(kwargs)
        return real(self, tool, **kwargs)

    monkeypatch.setattr(ToolExecutor, "execute", spy)
    state = PipelineState(project_id="t_style", user_prompt="A ghost haunts a school at night")
    StoryAgent().run(state, target_duration_s=24, scene_count=2)
    VideoAgent().generate_shot_bank(state.project_id, state.script.scenes[0], 64, 36,
                                    story=state.script.story)
    assert calls and "horror" in calls[0]["style"]
    assert "anime" not in calls[0]["prompt"]          # no hardcoded look any more


# ---- open-source voices ----------------------------------------------------

def test_voices_are_named_per_engine():
    """Each engine has its own voice names; a character must get the right kind."""
    from agents.audio_agent.agent import AudioAgent
    from shared.schemas.story import Character

    hero = Character(id="char_a", name="Mira", role="protagonist", description="d",
                     visual_description="v", voice_gender="female", voice_age="young")
    narrator = Character(id="char_n", name="Narrator", role="narrator", description="d",
                         visual_description="v")
    assert AudioAgent.voice_for(hero, "kokoro").startswith(("af_", "bf_"))
    assert AudioAgent.voice_for(hero, "edge").startswith("en-")
    assert AudioAgent.voice_for(narrator, "kokoro") == "bm_george"


def test_kokoro_says_how_to_install_itself_when_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("KOKORO_MODEL", raising=False)
    monkeypatch.delenv("KOKORO_VOICES", raising=False)
    from mcp.tools.audio_tools.tts_tool import TtsTool
    with pytest.raises(RuntimeError, match="get_kokoro"):
        TtsTool()._kokoro("hello", tmp_path / "a.wav")


def test_kokoro_failure_falls_back_to_another_engine(tmp_path, monkeypatch):
    """A missing model must not cost the user their film."""
    from mcp.tools.audio_tools import tts_tool
    monkeypatch.delenv("KOKORO_MODEL", raising=False)
    monkeypatch.setattr(tts_tool.TtsTool, "_edge_tts",
                        lambda self, text, out, voice="", **kw: Path(
                            str(out.with_suffix(".mp3"))).write_bytes(b"ID3") or
                        out.with_suffix(".mp3"))
    res = ToolExecutor().execute("audio.tts", text="hello", engine="kokoro",
                                 out_path=str(tmp_path / "x.mp3"))
    assert res.success and res.metadata["engine"] == "edge"


def test_voice_edits_use_the_engines_own_alternates():
    from agents.audio_agent.agent import AudioAgent
    from shared.schemas.audio import VoiceConfig
    from shared.schemas.story import Character

    hero = Character(id="char_a", name="Mira", role="protagonist", description="d",
                     visual_description="v", voice_gender="female")
    cfg = VoiceConfig(character_id="char_a", engine="kokoro", voice_id="af_heart")
    AudioAgent.apply_voice_params(cfg, {"voice": "alternate"}, hero)
    assert cfg.voice_id != "af_heart" and cfg.voice_id.startswith(("af_", "bf_"))
