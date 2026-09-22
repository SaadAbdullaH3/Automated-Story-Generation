"""M1 — image provider output is validated, sized, and saved in the right format."""
from __future__ import annotations
from io import BytesIO

import pytest
from PIL import Image

from mcp.tool_executor import ToolExecutor


class _Resp:
    def __init__(self, content: bytes, ctype: str, status: int = 200):
        self.content = content
        self.headers = {"content-type": ctype}
        self.status_code = status
        self.text = content.decode("latin-1", errors="ignore")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _jpeg(w: int, h: int, comment: bytes = b"") -> bytes:
    buf = BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(buf, format="JPEG")
    data = buf.getvalue()
    # Pollinations puts its generation params (JSON) in the file header.
    return data[:2] + b"\xff\xfe" + (len(comment) + 2).to_bytes(2, "big") + comment + data[2:] \
        if comment else data


@pytest.fixture
def pollinations(monkeypatch):
    """Chain starting at Pollinations, with the placeholder still behind it."""
    import requests
    monkeypatch.delenv("PROVIDER_IMAGE", raising=False)
    for name in ("POLLINATIONS_API_KEY", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN",
                 "LOCAL_SD", "SD_API_URL", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    calls = []

    def install(resp):
        def fake_get(url, headers=None, timeout=None):
            calls.append({"url": url, "headers": headers or {}})
            return resp
        monkeypatch.setattr(requests, "get", fake_get)
        return calls
    return install


def test_legacy_jpeg_is_saved_as_png_at_requested_size(tmp_path, pollinations):
    pollinations(_Resp(_jpeg(1024, 576, b'{"model":"sana","width":1024}'), "image/jpeg"))
    out = tmp_path / "frame.png"
    res = ToolExecutor().execute("vision.generate_image", prompt="a castle",
                                 out_path=str(out), width=1280, height=720)
    assert res.success, res.error
    img = Image.open(out)
    assert img.format == "PNG" and img.size == (1280, 720)
    assert res.metadata["endpoint"] == "legacy"
    assert res.metadata["served_model"] == "sana"
    assert res.metadata["native_size"] == [1024, 576]


def test_error_page_is_never_saved_as_an_image(tmp_path, pollinations):
    pollinations(_Resp(b"<html>rate limited</html>", "text/html"))
    res = ToolExecutor().execute("vision.generate_image", prompt="a castle",
                                 out_path=str(tmp_path / "f.png"), width=320, height=180)
    assert res.success
    assert res.metadata["provider"] == "placeholder"   # fell through to the next provider
    assert Image.open(res.data).size == (320, 180)


def test_api_key_uses_authenticated_endpoint(tmp_path, pollinations, monkeypatch):
    monkeypatch.setenv("POLLINATIONS_API_KEY", "sk_test")
    calls = pollinations(_Resp(_jpeg(320, 180), "image/jpeg"))
    res = ToolExecutor().execute("vision.generate_image", prompt="a castle",
                                 out_path=str(tmp_path / "f.png"), width=320, height=180)
    assert res.success and res.metadata["endpoint"] == "gen"
    assert calls[0]["url"].startswith("https://gen.pollinations.ai/image/")
    assert "model=tongyi-mai%2Fz-image-turbo" in calls[0]["url"]
    assert calls[0]["headers"]["Authorization"] == "Bearer sk_test"
