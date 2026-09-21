"""M2 — image providers: Cloudflare, chain fallback, config-driven parallelism."""
from __future__ import annotations
import base64
import threading
from io import BytesIO

import pytest
from PIL import Image

from mcp.tool_executor import ToolExecutor
from shared import providers


def _png(w=1024, h=1024) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (w, h), (10, 120, 200)).save(buf, format="PNG")
    return buf.getvalue()


class _Resp:
    def __init__(self, payload, status=200, ctype="application/json"):
        self._payload = payload
        self.status_code = status
        self.headers = {"content-type": ctype}
        self.text = str(payload)[:500]
        self.content = payload if isinstance(payload, bytes) else b""

    def json(self):
        return self._payload


@pytest.fixture
def cloudflare(monkeypatch):
    """Chain: cloudflare first, placeholder behind it."""
    monkeypatch.delenv("PROVIDER_IMAGE", raising=False)
    for name in ("LOCAL_SD", "SD_API_URL", "OPENAI_API_KEY", "POLLINATIONS_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct123")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok456")
    providers.load(force=True)
    calls = []

    def install(response):
        import requests

        def fake_post(url, headers=None, json=None, timeout=None):
            calls.append({"url": url, "headers": headers or {}, "body": json or {}})
            if isinstance(response, Exception):
                raise response
            return response
        monkeypatch.setattr(requests, "post", fake_post)

        def fake_get(url, **kwargs):    # Pollinations sits between CF and placeholder
            raise RuntimeError("pollinations unavailable in this test")
        monkeypatch.setattr(requests, "get", fake_get)
        return calls
    yield install
    providers.load(force=True)


def test_cloudflare_generates_and_fits_the_image(tmp_path, cloudflare):
    calls = cloudflare(_Resp({"success": True,
                              "result": {"image": base64.b64encode(_png()).decode()}}))
    out = tmp_path / "scene.png"
    res = ToolExecutor().execute("vision.generate_image", prompt="a castle at dusk",
                                 out_path=str(out), width=1280, height=720, seed=7)
    assert res.success, res.error
    assert res.metadata["provider"] == "cloudflare"
    assert res.metadata["native_size"] == [1024, 1024]
    assert Image.open(out).size == (1280, 720)          # square source, cover-fitted

    call = calls[0]
    assert call["url"].endswith("/accounts/acct123/ai/run/@cf/black-forest-labs/flux-1-schnell")
    assert call["headers"]["Authorization"] == "Bearer tok456"
    assert call["body"] == {"prompt": "a castle at dusk", "seed": 7, "steps": 8}


def test_cloudflare_failure_falls_through_to_the_next_provider(tmp_path, cloudflare):
    cloudflare(_Resp({"success": False, "errors": [{"message": "quota"}]}))
    res = ToolExecutor().execute("vision.generate_image", prompt="a castle",
                                 out_path=str(tmp_path / "s.png"), width=320, height=180)
    assert res.success and res.metadata["provider"] == "placeholder"
    assert Image.open(res.data).size == (320, 180)


def test_cloudflare_http_error_is_reported_not_saved(tmp_path, cloudflare):
    cloudflare(_Resp({"errors": ["bad token"]}, status=403))
    res = ToolExecutor().execute("vision.generate_image", prompt="a castle",
                                 out_path=str(tmp_path / "s.png"), width=320, height=180)
    assert res.metadata["provider"] == "placeholder"    # never saves the error body


def test_image_concurrency_follows_the_active_provider(monkeypatch):
    monkeypatch.delenv("PROVIDER_IMAGE", raising=False)
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    providers.load(force=True)
    assert providers.concurrency("image") == 1          # pollinations: one at a time
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "a")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "t")
    providers.load(force=True)
    assert providers.concurrency("image") == 4          # cloudflare tolerates parallel calls


def test_pipeline_generates_images_in_parallel(isolated_dirs, monkeypatch):
    """The video agent should fan out image jobs up to the provider's limit."""
    from agents.story_agent.planner import template_script
    from agents.video_agent import VideoAgent
    from shared.schemas.pipeline import PipelineState

    monkeypatch.setattr(providers, "concurrency", lambda role: 4 if role == "image" else 1)
    live = peak = 0
    lock = threading.Lock()
    real = ToolExecutor.execute

    def counting(self, tool, **kwargs):
        nonlocal live, peak
        if tool != "vision.generate_image":
            return real(self, tool, **kwargs)
        with lock:
            live += 1
            peak = max(peak, live)
        try:
            return real(self, tool, **kwargs)
        finally:
            with lock:
                live -= 1

    monkeypatch.setattr(ToolExecutor, "execute", counting)
    state = PipelineState(project_id="t_par", user_prompt="A kite over a town")
    state.script = template_script("t_par", state.user_prompt, target_duration_s=30, scene_count=4)
    agent = VideoAgent()
    agent.run(state, with_subtitles=False, width=64, height=36, fps=6,
              use_text_to_video=False, use_lip_sync=False, cinematic_post=False)
    assert peak > 1, "image generation ran one at a time"
