"""pytest configuration: ensure project root is on sys.path."""
import os
import sys
from pathlib import Path

import pytest

# Project root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Force mock LLM provider for deterministic tests.
os.environ.setdefault("LLM_PROVIDER", "mock")
# Disable Pollinations.ai network call by default in tests so they're offline-safe.
os.environ.setdefault("POLLINATIONS_DISABLE", "1")
# Only the languages a test asks for get subtitle tracks.
os.environ.pop("SUBTITLE_EXTRA_LANGUAGES", None)

# Register all MCP tools.
import mcp.tools  # noqa: F401, E402


@pytest.fixture
def isolated_dirs(tmp_path, monkeypatch):
    """Point outputs, snapshots and the version DB at a temp dir."""
    import shared.constants as constants
    monkeypatch.setattr(constants, "OUTPUTS_DIR", tmp_path / "out")
    monkeypatch.setattr(constants, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(constants, "DB_PATH", tmp_path / "state.db")
    constants.OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    constants.STATE_DIR.mkdir(parents=True, exist_ok=True)
    return tmp_path


def silence_tts(tools) -> list:
    """Patch a ToolExecutor so TTS renders silent audio offline.

    Returns a list that records the kwargs of every audio.tts call.
    """
    calls: list = []
    real = tools.execute

    def patched(tool, **kwargs):
        if tool == "audio.tts":
            calls.append(dict(kwargs))
            kwargs["engine"] = "silent"
        return real(tool, **kwargs)

    tools.execute = patched
    return calls


@pytest.fixture
def fake_translation(monkeypatch):
    """Offline translator: prefixes each line with the target language."""
    from mcp.tools.llm_tools.translate_tool import TranslateTool
    calls: list = []

    def fake(self, lines, lang):
        calls.append(lang)
        return [f"[{lang}] {line}" for line in lines]

    monkeypatch.setattr(TranslateTool, "_mymemory", fake)
    return calls


@pytest.fixture
def small_project(isolated_dirs):
    """Factory: build a small fully-rendered project (silent audio, 320x180 @ 12 fps)."""
    from agents.audio_agent import AudioAgent
    from agents.story_agent.planner import template_script
    from agents.video_agent import VideoAgent
    from shared.schemas.pipeline import PipelineState
    from state_manager.snapshot import referenced_files
    from state_manager.state_manager import StateManager
    from state_manager.storage import SqliteStorage

    def build(project_id="t_small", duration_s=24, scenes=3, subtitle_language="English",
              with_subtitles=True, with_bgm=False):
        sm = StateManager(SqliteStorage(isolated_dirs / "state.db"))
        state = PipelineState(project_id=project_id, user_prompt="A robot learns to paint")
        state.script = template_script(project_id, state.user_prompt,
                                       target_duration_s=duration_s, scene_count=scenes)
        audio = AudioAgent()
        silence_tts(audio.tools)
        audio.run(state, with_bgm=with_bgm)
        VideoAgent().run(state, with_subtitles=with_subtitles,
                         subtitle_language=subtitle_language,
                         width=320, height=180, fps=12,
                         use_text_to_video=False, use_lip_sync=False, cinematic_post=False)
        sm.snapshot(state, asset_paths=referenced_files(state), description="initial")
        return state, sm

    return build
