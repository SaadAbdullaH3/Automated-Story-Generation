"""HTTP endpoints to launch and re-run the main pipeline.

Starting a run enqueues a job and returns immediately; a worker picks it up.
The response carries the job id so the caller can follow, cancel or retry it.
"""
from __future__ import annotations
from pathlib import PureWindowsPath   # splits on both "\" and "/"
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import jobs
from shared import voices
from shared.languages import iso639_1, supported_names
from shared.utils.files import project_dir
from shared.utils.ids import new_project_id
from shared.utils.logging import get_logger
from state_manager.state_manager import StateManager

from ..services import pipeline_service, progress

router = APIRouter()
log = get_logger("api.pipeline")
sm = StateManager()


class RunRequest(BaseModel):
    prompt: str = Field(..., min_length=4)
    target_duration_s: int = 45
    scene_count: int = 4
    with_bgm: bool = True
    with_subtitles: bool = True
    subtitle_language: str = "English"
    tts_engine: Optional[str] = None   # None = the provider chain's choice


class RunResponse(BaseModel):
    project_id: str
    job_id: str
    status: str
    websocket: str


class PhaseRerunRequest(BaseModel):
    project_id: str
    phase: str  # "story" | "audio" | "video"


def _check_engine(name: Optional[str]) -> None:
    """Reject an unusable voice engine now, not three minutes into a render."""
    if not name:
        return
    engine = voices.get(name)
    if engine is None:
        raise HTTPException(400, f"unknown voice engine {name!r}")
    reason = voices.unavailable_reason(engine)
    if reason:
        raise HTTPException(409, f"{engine.label} is not available here: {reason}")


def _accepted(kind: str, project_id: str, status: str, **payload) -> RunResponse:
    payload = {k: v for k, v in payload.items() if v is not None}
    job = jobs.enqueue(kind, project_id, payload)
    log.info("queued %s %s for %s", kind, job.id, project_id)
    return RunResponse(project_id=project_id, job_id=job.id, status=status,
                       websocket=f"/ws/progress/{project_id}")


@router.post("/run", response_model=RunResponse)
def start_run(req: RunRequest):
    """Queue a full pipeline run; progress streams over /ws/progress/{project_id}."""
    _check_engine(req.tts_engine)
    return _accepted(
        "run_full", new_project_id(), "queued",
        prompt=req.prompt, target_duration_s=req.target_duration_s,
        scene_count=req.scene_count, with_bgm=req.with_bgm,
        with_subtitles=req.with_subtitles, subtitle_language=req.subtitle_language,
        tts_engine=req.tts_engine,
    )


@router.post("/rerun", response_model=RunResponse)
def rerun_phase(req: PhaseRerunRequest):
    if req.phase not in ("story", "audio", "video"):
        raise HTTPException(400, f"unknown phase {req.phase}")
    if not sm.latest(req.project_id):
        raise HTTPException(404, f"project {req.project_id} not found")
    return _accepted("rerun_phase", req.project_id, "queued", phase=req.phase)


class PlanRequest(BaseModel):
    prompt: str = Field(..., min_length=4)
    target_duration_s: int = 45
    scene_count: int = 4
    with_preview: bool = True


class SceneEdit(BaseModel):
    title: Optional[str] = None
    setting: Optional[str] = None
    visual_prompt: Optional[str] = None
    dialogue: Optional[Dict[str, str]] = None   # line_id -> new text


class RenderRequest(BaseModel):
    with_bgm: bool = True
    with_subtitles: bool = True
    subtitle_language: str = "English"
    burn_subtitles: bool = True
    tts_engine: Optional[str] = None


@router.post("/plan", response_model=RunResponse)
def start_plan(req: PlanRequest):
    """Write the script + preview images only; render is a separate, approved step."""
    return _accepted(
        "plan", new_project_id(), "planning",
        prompt=req.prompt, target_duration_s=req.target_duration_s,
        scene_count=req.scene_count, with_preview=req.with_preview,
    )


@router.get("/storyboard/{project_id}")
def get_storyboard(project_id: str):
    state = sm.latest(project_id)
    if not state or not state.storyboard:
        raise HTTPException(404, f"no storyboard for {project_id}")
    board = state.storyboard.model_dump(mode="json")
    # Previews are served from /assets/<project_id>/...
    for frame in board["frames"]:
        if frame.get("preview_path"):
            frame["preview_url"] = (f"/assets/{project_id}/video/storyboard/"
                                    f"{PureWindowsPath(frame['preview_path']).name}")
    board["stage"] = state.stage
    board["version"] = state.version
    return board


@router.patch("/storyboard/{project_id}/{scene_id}")
def edit_storyboard(project_id: str, scene_id: str, edit: SceneEdit):
    """Edit one scene before rendering; redraws its preview if the visuals changed."""
    try:
        pipeline_service.orchestrator().update_storyboard(
            project_id, scene_id, title=edit.title, setting=edit.setting,
            visual_prompt=edit.visual_prompt, dialogue=edit.dialogue,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    return get_storyboard(project_id)


@router.post("/render/{project_id}", response_model=RunResponse)
def start_render(project_id: str, req: RenderRequest):
    """Approve a storyboard and render the film."""
    state = sm.latest(project_id)
    if not state or not state.script:
        raise HTTPException(404, f"no storyboard to render for {project_id}")
    _check_engine(req.tts_engine)
    return _accepted(
        "render", project_id, "rendering",
        with_bgm=req.with_bgm, with_subtitles=req.with_subtitles,
        subtitle_language=req.subtitle_language, burn_subtitles=req.burn_subtitles,
        tts_engine=req.tts_engine,
    )


@router.get("/subtitles/{project_id}")
def subtitle_tracks(project_id: str):
    """WebVTT tracks for the browser player (MP4 soft subs are invisible there)."""
    state = sm.latest(project_id)
    if not state or not state.video:
        raise HTTPException(404, f"no video for {project_id}")
    tracks = []
    for lang in state.video.subtitle_languages or []:
        vtt = project_dir(project_id) / "subtitles" / f"{lang.lower()}.vtt"
        if vtt.exists():
            tracks.append({"language": lang, "code": iso639_1(lang),
                           "url": f"/assets/{project_id}/subtitles/{vtt.name}",
                           "burned_in": lang == state.video.burned_subtitle_language})
    return tracks


@router.get("/languages")
def subtitle_languages():
    """Subtitle languages the pipeline can translate + embed."""
    return supported_names()


@router.get("/state/{project_id}")
def get_state(project_id: str):
    state = sm.latest(project_id)
    if not state:
        raise HTTPException(404, f"project {project_id} not found")
    return state.model_dump(mode="json")


@router.get("/status/{project_id}")
def get_status(project_id: str):
    """Lightweight status — job state, phase progress and the last event."""
    return progress.snapshot(project_id) or {"project_id": project_id, "status": "unknown"}
