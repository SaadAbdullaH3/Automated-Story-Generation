"""M6 — real motion is a provider chain, not an `if os.getenv(...)` in an agent."""
from __future__ import annotations

import textwrap

import pytest

from agents.video_agent.agent import (motion_provider, premium_lipsync_available,
                                      premium_motion_available)
from mcp.tools.vision_tools.text_to_video_tool import TextToVideoTool
from shared import providers


@pytest.fixture
def chain(tmp_path, monkeypatch):
    """Point the provider layer at a throwaway config."""
    def use(yaml_text: str, **env):
        path = tmp_path / "providers.yaml"
        path.write_text(textwrap.dedent(yaml_text), encoding="utf-8")
        monkeypatch.setenv("PROVIDERS_FILE", str(path))
        for key in ("FAL_KEY", "FAL_API_KEY", "REPLICATE_API_TOKEN", "HF_TOKEN",
                    "HUGGINGFACE_API_KEY", "GEMINI_API_KEY", "VIDEO_BUDGET_OK"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        providers.load(force=True)
    yield use
    providers.load(force=True)


BOTH_ROLES = """
    version: 1
    roles:
      video:
        - provider: gemini_veo
          model: veo-3.1-fast-generate-preview
          requires: [GEMINI_API_KEY, VIDEO_BUDGET_OK]
        - provider: fal
          requires: [FAL_KEY|FAL_API_KEY]
        - provider: ffmpeg
      lipsync:
        - provider: fal
          requires: [FAL_KEY|FAL_API_KEY]
        - provider: none
    """


def test_with_no_keys_the_offline_animator_is_the_tier(chain):
    chain(BOTH_ROLES)
    assert motion_provider() == "ffmpeg"
    assert premium_motion_available() is False
    assert premium_lipsync_available() is False


def test_a_key_promotes_the_tier_without_touching_agent_code(chain):
    chain(BOTH_ROLES, FAL_KEY="x")
    assert motion_provider() == "fal"
    assert premium_motion_available() is True
    assert premium_lipsync_available() is True


def test_a_gemini_key_alone_does_not_switch_on_a_per_second_bill(chain):
    """Veo is billed per second of video and is not in the free tier. Holding
    a Gemini key for the free text models must not start charging for video."""
    chain(BOTH_ROLES, GEMINI_API_KEY="x")
    assert motion_provider() == "ffmpeg"

    chain(BOTH_ROLES, GEMINI_API_KEY="x", VIDEO_BUDGET_OK="1")
    assert motion_provider() == "gemini_veo"


def test_veo_refuses_to_run_without_the_budget_flag(tmp_path, monkeypatch):
    """Belt and braces: the provider itself checks, not only the chain."""
    monkeypatch.delenv("VIDEO_BUDGET_OK", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    with pytest.raises(RuntimeError, match="billed per second"):
        TextToVideoTool()._gemini_veo(
            prompt="a lighthouse", image_path=None, out=tmp_path / "v.mp4",
            duration_s=4, width=1280, height=720)


def test_the_tool_walks_the_chain_and_says_what_it_tried(chain, tmp_path, monkeypatch):
    chain(BOTH_ROLES, FAL_KEY="x")
    attempts = []

    def explode(**kwargs):
        attempts.append(kwargs.get("model") or "fal")
        raise RuntimeError("provider is down")

    monkeypatch.setattr(TextToVideoTool, "_fal", lambda self, **kw: explode(**kw))
    res = TextToVideoTool().run(prompt="a lighthouse at dusk",
                                out_path=str(tmp_path / "v.mp4"))
    assert res.success is False
    assert "fal" in res.error          # names what was tried, not a generic message
    assert attempts, "the configured provider was never called"


def test_the_chain_stops_at_ffmpeg_rather_than_calling_it(chain, tmp_path):
    """`ffmpeg` in the chain means 'fall back to the offline animator', which
    is the caller's job — the tool must not try to run it as a provider."""
    chain(BOTH_ROLES)
    res = TextToVideoTool().run(prompt="x", out_path=str(tmp_path / "v.mp4"))
    assert res.success is False
    assert "tried:" not in (res.error or "")


def test_a_successful_provider_reports_which_one_served_it(chain, tmp_path,
                                                           monkeypatch):
    chain(BOTH_ROLES, FAL_KEY="x")

    def fake(self, **kwargs):
        kwargs["out"].write_bytes(b"clip")

    monkeypatch.setattr(TextToVideoTool, "_fal", fake)
    res = TextToVideoTool().run(prompt="x", out_path=str(tmp_path / "v.mp4"))
    assert res.success is True
    assert res.metadata["provider"] == "fal"


def test_the_shipped_config_keeps_the_offline_animator_last():
    """Whatever else is configured, a render must never fail for want of a
    paid video provider."""
    providers.load(force=True)
    # specs() is every entry; chain() is only the ones usable right now.
    video = [s.provider for s in providers.load().specs("video")]
    assert video[-1] == "ffmpeg"
    # Free options come first: a provider that charges per clip must not be
    # the default on a project that runs at zero spend.
    assert video.index("fal") < video.index("gemini_veo")
    assert video.index("huggingface") < video.index("replicate")
    assert [s.provider for s in providers.load().specs("lipsync")][-1] == "none"
    # And with no keys on this machine, that last entry is what is in use.
    assert [s.provider for s in providers.chain("video")] == ["ffmpeg"]
