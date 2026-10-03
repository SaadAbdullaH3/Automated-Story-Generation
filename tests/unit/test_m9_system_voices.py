"""M9 — "System voices" (pyttsx3) can't take the server down.

On the server, previewing System voices after a Kokoro sample killed the API:
Kokoro loads a bundled espeak into the process, pyttsx3 then started that copy
with no data path, and espeak answers a missing data file with exit(1). Without
Kokoro first, pyttsx3 found no espeak at all and returned silence as a success.
"""
from __future__ import annotations
import os
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

from shared import voices

ROOT = Path(__file__).resolve().parents[2]


def _on_linux(monkeypatch):
    # Only the module under test sees Linux; pytest itself must not.
    monkeypatch.setattr(voices, "sys", types.SimpleNamespace(platform="linux"))


@pytest.fixture
def linux_without_espeak(monkeypatch):
    _on_linux(monkeypatch)
    monkeypatch.setattr(voices.ctypes.util, "find_library", lambda _name: None)


def test_system_voices_are_unavailable_without_a_speech_engine(linux_without_espeak):
    reason = voices.unavailable_reason(voices.get("pyttsx3"))
    assert reason and "espeak" in reason


def test_system_voices_are_available_with_one(monkeypatch):
    _on_linux(monkeypatch)
    monkeypatch.setattr(voices.ctypes.util, "find_library",
                        lambda name: "libespeak-ng.so.1" if name == "espeak-ng" else None)
    assert voices.unavailable_reason(voices.get("pyttsx3")) is None


def test_pyttsx3_is_never_started_without_one(linux_without_espeak, monkeypatch, tmp_path):
    from mcp.tools.audio_tools.tts_tool import TtsTool

    def init(*_a, **_k):
        raise AssertionError("pyttsx3.init() ran on a machine with no espeak")

    monkeypatch.setitem(sys.modules, "pyttsx3", types.SimpleNamespace(init=init))
    res = TtsTool().run(text="Hello there.", out_path=str(tmp_path / "line.wav"),
                        engine="pyttsx3")
    assert res.metadata["engine"] == "silent"          # said so, rather than passing it off


# Stands in for kokoro_onnx.Kokoro: starts espeak exactly the way it does,
# without needing its 325 MB model.
SCRIPT = textwrap.dedent('''
    import sys, types
    from pathlib import Path
    import numpy as np

    class Kokoro:
        def __init__(self, model, voices):
            import espeakng_loader
            from phonemizer.backend.espeak.wrapper import EspeakWrapper
            EspeakWrapper.set_library(espeakng_loader.get_library_path())
            EspeakWrapper.set_data_path(espeakng_loader.get_data_path())

        def create(self, text, voice, speed, lang):
            from phonemizer.backend import EspeakBackend
            EspeakBackend(lang).phonemize([text])
            return np.zeros(2400, dtype="float32"), 24000

    sys.modules["kokoro_onnx"] = types.SimpleNamespace(Kokoro=Kokoro)
    if sys.argv[2] == "with-system-espeak":
        # As on a Linux machine that has espeak installed: pyttsx3 is allowed
        # to start, and picks up the copy Kokoro already loaded.
        import ctypes.util
        ctypes.util.find_library = lambda name: "libespeak-ng.so.1" if name == "espeak-ng" else None
    from mcp.tools.audio_tools.tts_tool import TtsTool
    tool, tmp = TtsTool(), Path(sys.argv[1])
    for engine in ("kokoro", "pyttsx3"):
        r = tool.run(text="The tide is coming in.", out_path=str(tmp / (engine + ".wav")),
                     engine=engine)
        print(engine, "->", r.metadata.get("engine"), flush=True)
    print("still alive", flush=True)
''')


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="espeak's exit(1) is the Linux path")
@pytest.mark.parametrize("machine", ["no-system-espeak", "with-system-espeak"])
def test_a_kokoro_voice_then_system_voices_leaves_the_process_alive(tmp_path, machine):
    pytest.importorskip("espeakng_loader")
    pytest.importorskip("phonemizer")
    for name in ("model.onnx", "voices.bin"):
        (tmp_path / name).write_bytes(b"stand-in")
    env = {**os.environ, "PIPELINE_SKIP_DOTENV": "1",
           "KOKORO_MODEL": str(tmp_path / "model.onnx"),
           "KOKORO_VOICES": str(tmp_path / "voices.bin")}
    env.pop("ESPEAK_DATA_PATH", None)
    proc = subprocess.run([sys.executable, "-c", SCRIPT, str(tmp_path), machine], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr[-800:]
    assert "kokoro -> kokoro" in proc.stdout
    assert "still alive" in proc.stdout
