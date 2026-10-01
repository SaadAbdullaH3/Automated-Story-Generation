"""Voice endpoints — list the engines, and hear one before committing to it.

Rendering a whole film is minutes of work; choosing the voice shouldn't be a
guess, so the UI can play a one-line sample of any engine and voice first.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from mcp.tool_executor import ToolExecutor
from shared import assets, constants, voices
from shared.utils.logging import get_logger

router = APIRouter()
log = get_logger("api.voices")
_tools = ToolExecutor()

# Samples are identical for identical inputs, so they are rendered once and
# kept. They live under the asset root the UI already serves. The directory is
# resolved per call, not at import, so it follows constants.OUTPUTS_DIR.
PREVIEW_SUBDIR = "_voice_previews"


def preview_dir() -> Path:
    return constants.OUTPUTS_DIR / PREVIEW_SUBDIR


class PreviewRequest(BaseModel):
    engine: str
    voice: str = ""
    text: str = Field(default=voices.SAMPLE_TEXT, max_length=voices.MAX_SAMPLE_CHARS)


@router.get("/")
def list_voices():
    """Every engine, whether it works on this machine, and its voices."""
    return {"default": voices.default_engine(), "engines": voices.catalogue()}


@router.post("/preview")
def preview(req: PreviewRequest):
    """Render a sample line and return a URL the browser can play."""
    if not voices.is_known_voice(req.engine, req.voice):
        raise HTTPException(400, f"unknown voice {req.voice!r} for engine {req.engine!r}")
    engine = voices.get(req.engine)
    reason = voices.unavailable_reason(engine)
    if reason:
        raise HTTPException(409, f"{engine.label} is not available here: {reason}")

    text = (req.text or voices.SAMPLE_TEXT).strip()
    digest = hashlib.sha1(f"{req.engine}|{req.voice}|{text}".encode()).hexdigest()[:12]
    directory = preview_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stem = directory / f"{req.engine}_{digest}"

    cached = next((p for p in (stem.with_suffix(".wav"), stem.with_suffix(".mp3"))
                   if p.exists() and p.stat().st_size > 0), None)
    if cached is None:
        res = _tools.execute("audio.tts", text=text, out_path=str(stem.with_suffix(".wav")),
                             engine=req.engine, voice=req.voice)
        if not res.success:
            raise HTTPException(502, f"{engine.label} could not speak: {res.error}")
        cached = Path(res.data)
        served_by = res.metadata.get("engine", req.engine)
        if served_by != req.engine:
            # The tool falls back rather than failing; say so instead of
            # letting someone pick a voice they aren't actually hearing.
            log.warning("%s fell back to %s for the preview", req.engine, served_by)
            return {"url": _asset_url(cached), "engine": served_by,
                    "requested_engine": req.engine, "voice": req.voice,
                    "fell_back": True}

    return {"url": _asset_url(cached), "engine": req.engine, "voice": req.voice,
            "fell_back": False}


def _asset_url(path: Path) -> str:
    return assets.asset_url(path) or f"/assets/{PREVIEW_SUBDIR}/{path.name}"
