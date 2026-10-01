"""M4 — choosing a voice engine, and hearing it before the render."""
from __future__ import annotations

import sys
import types

import pytest
from fastapi.testclient import TestClient

import jobs
from mcp.base_tool import ToolResult
from shared import voices
from tests.conftest import signed_in_client


@pytest.fixture
def api(isolated_dirs, monkeypatch):
    from backend import app as app_module
    from backend.routes import pipeline as pipeline_routes
    from state_manager.state_manager import StateManager
    from state_manager.storage import VersionStore
    monkeypatch.setattr(pipeline_routes, "sm",
                        StateManager(VersionStore(isolated_dirs / "state.db")))
    return signed_in_client(app_module.app)


@pytest.fixture
def kokoro_installed(tmp_path, monkeypatch):
    """Pretend the model files and the package are present."""
    model, voice_file = tmp_path / "kokoro.onnx", tmp_path / "voices.bin"
    model.write_bytes(b"x")
    voice_file.write_bytes(b"x")
    monkeypatch.setenv("KOKORO_MODEL", str(model))
    monkeypatch.setenv("KOKORO_VOICES", str(voice_file))
    monkeypatch.setitem(sys.modules, "kokoro_onnx", types.ModuleType("kokoro_onnx"))
    return model


# ---- the catalogue ----------------------------------------------------------

def test_an_engine_that_cannot_run_here_says_how_to_fix_it(monkeypatch):
    monkeypatch.delenv("KOKORO_MODEL", raising=False)
    monkeypatch.delenv("KOKORO_VOICES", raising=False)
    kokoro = next(e for e in voices.catalogue() if e["name"] == "kokoro")
    assert kokoro["available"] is False
    assert "get_kokoro.py" in kokoro["unavailable_reason"]
    # edge-tts needs nothing installed, so it stays offerable.
    edge = next(e for e in voices.catalogue() if e["name"] == "edge")
    assert edge["available"] is True and edge["needs_network"] is True


def test_kokoro_becomes_available_once_its_files_are_there(kokoro_installed):
    kokoro = next(e for e in voices.catalogue() if e["name"] == "kokoro")
    assert kokoro["available"] is True and kokoro["unavailable_reason"] is None
    assert kokoro["open_source"] is True
    assert {v["id"] for v in kokoro["voices"]} >= {"af_heart", "bm_george"}


def test_every_offered_voice_is_one_the_engine_knows():
    """The preview endpoint only renders voices from this table, so the table
    and the audio agent's pools must not drift apart."""
    from agents.audio_agent.agent import KOKORO_POOL, KOKORO_VOICES, VOICE_POOL
    offered = {e.name: {v.id for v in e.voices} for e in voices.ENGINES}
    used_kokoro = set(KOKORO_VOICES.values()).union(*KOKORO_POOL.values())
    used_edge = set().union(*VOICE_POOL.values())
    assert used_kokoro <= offered["kokoro"]
    assert used_edge <= offered["edge"]


def test_voices_endpoint_lists_engines_and_the_default(api):
    body = api.get("/api/voices/").json()
    assert body["default"] in {e["name"] for e in body["engines"]}
    assert [e["name"] for e in body["engines"]][:2] == ["kokoro", "edge"]


# ---- previewing --------------------------------------------------------------

def _fake_tts(monkeypatch, served="edge"):
    """Replace the TTS tool with one that writes a stub file."""
    from backend.routes import voices as voice_routes
    calls = []

    def execute(tool, **kwargs):
        calls.append(kwargs)
        out = kwargs["out_path"]
        with open(out, "wb") as fh:
            fh.write(b"RIFFfake")
        return ToolResult(success=True, data=out, metadata={"engine": served})

    monkeypatch.setattr(voice_routes._tools, "execute", execute)
    return calls


def test_preview_renders_a_sample_then_serves_it_from_cache(api, monkeypatch):
    calls = _fake_tts(monkeypatch, served="edge")
    body = api.post("/api/voices/preview",
                    json={"engine": "edge", "voice": "en-US-AriaNeural"}).json()

    assert body["url"].startswith("/assets/_voice_previews/")
    assert body["fell_back"] is False
    assert calls[0]["voice"] == "en-US-AriaNeural"
    assert calls[0]["text"] == voices.SAMPLE_TEXT

    again = api.post("/api/voices/preview",
                     json={"engine": "edge", "voice": "en-US-AriaNeural"}).json()
    assert again["url"] == body["url"]
    assert len(calls) == 1          # rendered once, not twice


def test_previews_land_under_the_configured_output_directory(api, monkeypatch,
                                                             isolated_dirs):
    """Bound at import, the preview directory ignored the configured root and
    leaked files into the repository during tests."""
    from shared import constants
    _fake_tts(monkeypatch)
    body = api.post("/api/voices/preview",
                    json={"engine": "edge", "voice": "en-US-GuyNeural"}).json()
    written = list((constants.OUTPUTS_DIR / "_voice_previews").glob("edge_*"))
    assert len(written) == 1
    assert body["url"].endswith(written[0].name)


def test_preview_admits_when_the_engine_fell_back(api, monkeypatch, kokoro_installed):
    """Silently serving an edge-tts sample as 'Kokoro' would make the chooser lie."""
    _fake_tts(monkeypatch, served="edge")
    body = api.post("/api/voices/preview",
                    json={"engine": "kokoro", "voice": "af_heart"}).json()
    assert body["fell_back"] is True
    assert body["engine"] == "edge" and body["requested_engine"] == "kokoro"


def test_preview_refuses_voices_it_does_not_know(api, monkeypatch):
    _fake_tts(monkeypatch)
    assert api.post("/api/voices/preview",
                    json={"engine": "edge", "voice": "../../etc/passwd"}).status_code == 400
    assert api.post("/api/voices/preview",
                    json={"engine": "nope", "voice": ""}).status_code == 400


def test_preview_of_an_unavailable_engine_explains_instead_of_falling_back(api, monkeypatch):
    monkeypatch.delenv("KOKORO_MODEL", raising=False)
    monkeypatch.delenv("KOKORO_VOICES", raising=False)
    res = api.post("/api/voices/preview", json={"engine": "kokoro", "voice": "af_heart"})
    assert res.status_code == 409 and "get_kokoro.py" in res.json()["detail"]


# ---- choosing it for a film ---------------------------------------------------

def test_the_chosen_engine_is_queued_with_the_run(api, kokoro_installed):
    res = api.post("/api/pipeline/run", json={"prompt": "A kite over a quiet town",
                                              "tts_engine": "kokoro"}).json()
    assert jobs.get(res["job_id"]).payload["tts_engine"] == "kokoro"


def test_an_unusable_engine_is_refused_before_anything_is_queued(api, monkeypatch):
    monkeypatch.delenv("KOKORO_MODEL", raising=False)
    monkeypatch.delenv("KOKORO_VOICES", raising=False)
    res = api.post("/api/pipeline/run", json={"prompt": "A kite over a quiet town",
                                              "tts_engine": "kokoro"})
    assert res.status_code == 409
    assert jobs.list_jobs(limit=5) == []


def test_the_chosen_engine_reaches_phase_2(isolated_dirs, monkeypatch):
    from agents.orchestrator import PipelineOrchestrator
    from state_manager.state_manager import StateManager
    from state_manager.storage import VersionStore

    orch = PipelineOrchestrator(
        state_manager=StateManager(VersionStore(isolated_dirs / "state.db")))
    seen = {}

    class Stop(Exception):
        pass

    def fake_audio_run(state, with_bgm=True, tts_engine=None):
        seen["tts_engine"] = tts_engine
        raise Stop("far enough")

    monkeypatch.setattr(orch.audio, "run", fake_audio_run)
    with pytest.raises(Stop):
        orch.run_full("A kite over a quiet town", target_duration_s=20,
                      scene_count=2, tts_engine="kokoro")
    assert seen["tts_engine"] == "kokoro"
