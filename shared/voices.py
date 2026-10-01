"""Which voice engines can speak right now, and what each one sounds like.

The chain in `config/providers.yaml` decides the default. This module is what
lets a person override it per film: it describes the engines in human terms,
says which are actually usable on this machine, and names the voices the UI
offers for a preview.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from shared import providers

# A line with a few different sounds in it, short enough to render instantly.
SAMPLE_TEXT = "The lighthouse blinked twice, and the harbour went quiet."
MAX_SAMPLE_CHARS = 200


@dataclass
class Voice:
    id: str
    label: str
    gender: str = "neutral"


@dataclass
class Engine:
    name: str
    label: str
    summary: str
    voices: List[Voice] = field(default_factory=list)
    requires: List[str] = field(default_factory=list)
    needs_network: bool = False
    open_source: bool = False


ENGINES: List[Engine] = [
    Engine(
        name="kokoro",
        label="Kokoro",
        summary="Open-source (Apache-2.0), runs offline on the CPU. No key, no GPU.",
        open_source=True,
        requires=["KOKORO_MODEL", "KOKORO_VOICES"],
        voices=[
            Voice("af_heart", "Heart — US, warm", "female"),
            Voice("af_bella", "Bella — US, bright", "female"),
            Voice("af_nicole", "Nicole — US, young", "female"),
            Voice("bf_emma", "Emma — British", "female"),
            Voice("am_michael", "Michael — US", "male"),
            Voice("am_adam", "Adam — US, dry", "male"),
            Voice("bm_george", "George — British, narrator", "male"),
            Voice("bm_lewis", "Lewis — British, older", "male"),
        ],
    ),
    Engine(
        name="edge",
        label="Edge Neural",
        summary="Microsoft's neural voices. Free and very natural, but online.",
        needs_network=True,
        voices=[
            Voice("en-US-AriaNeural", "Aria — US", "female"),
            Voice("en-US-JennyNeural", "Jenny — US, friendly", "female"),
            Voice("en-GB-SoniaNeural", "Sonia — British", "female"),
            Voice("en-AU-NatashaNeural", "Natasha — Australian", "female"),
            Voice("en-US-AnaNeural", "Ana — US, child", "female"),
            Voice("en-US-GuyNeural", "Guy — US", "male"),
            Voice("en-US-ChristopherNeural", "Christopher — US, narrator", "male"),
            Voice("en-GB-RyanNeural", "Ryan — British", "male"),
            Voice("en-AU-WilliamNeural", "William — Australian", "male"),
        ],
    ),
    Engine(
        name="gtts",
        label="Google Translate TTS",
        summary="Free and online. Clear, but flat — no emotion or pacing control.",
        needs_network=True,
        voices=[Voice("", "Default", "neutral")],
    ),
    Engine(
        name="pyttsx3",
        label="System voices",
        summary="Whatever this computer has installed. Offline, robotic.",
        voices=[Voice("", "System default", "neutral")],
    ),
]

_BY_NAME: Dict[str, Engine] = {e.name: e for e in ENGINES}


def get(name: str) -> Optional[Engine]:
    return _BY_NAME.get((name or "").lower())


def default_engine() -> str:
    """Whatever the provider chain would pick if nobody chose."""
    spec = providers.active("tts")
    return spec.provider if spec else "edge"


def unavailable_reason(engine: Engine) -> Optional[str]:
    """Why this engine can't be used here, or None if it can."""
    if engine.name == "kokoro":
        model, voices = os.getenv("KOKORO_MODEL", ""), os.getenv("KOKORO_VOICES", "")
        if not (model and voices):
            return "set KOKORO_MODEL and KOKORO_VOICES (python scripts/get_kokoro.py)"
        if not (Path(model).exists() and Path(voices).exists()):
            return "model files are missing — run python scripts/get_kokoro.py"
        try:
            import kokoro_onnx  # noqa: F401
        except ImportError:
            return "pip install -r requirements-voices.txt"
    missing = [v for v in engine.requires if not os.getenv(v)]
    if missing:
        return "missing " + ", ".join(missing)
    return None


def catalogue() -> List[Dict]:
    """Every engine, whether it works here, and its voices — for the UI."""
    default = default_engine()
    out = []
    for engine in ENGINES:
        reason = unavailable_reason(engine)
        out.append({
            "name": engine.name,
            "label": engine.label,
            "summary": engine.summary,
            "open_source": engine.open_source,
            "needs_network": engine.needs_network,
            "available": reason is None,
            "unavailable_reason": reason,
            "is_default": engine.name == default,
            "voices": [{"id": v.id, "label": v.label, "gender": v.gender}
                       for v in engine.voices],
        })
    return out


def is_known_voice(engine_name: str, voice_id: str) -> bool:
    """Guard the preview endpoint: only voices this module lists can be rendered."""
    engine = get(engine_name)
    if engine is None:
        return False
    return any(v.id == (voice_id or "") for v in engine.voices)
