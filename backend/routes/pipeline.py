"""HTTP endpoints to launch and re-run the main pipeline."""
from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from shared.languages import supported_names
from shared.utils.ids import new_project_id
from shared.utils.logging import get_logger
from state_manager.state_manager import StateManager

from ..services import run_registry, pipeline_service

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


class RunResponse(BaseModel):
    project_id: str
    status: str
    websocket: str


class PhaseRerunRequest(BaseModel):
    project_id: str
    phase: str  # "story" | "audio" | "video"


@router.post("/run", response_model=RunResponse)
def start_run(req: RunRequest, background: BackgroundTasks):
    """Start a full pipeline run; progress streams over /ws/progress/{project_id}."""
    project_id = new_project_id()
    run_registry.create(project_id)
    background.add_task(
        pipeline_service.run_full_async,
        prompt=req.prompt,
        project_id=project_id,
        target_duration_s=req.target_duration_s,
        scene_count=req.scene_count,
        with_bgm=req.with_bgm,
        with_subtitles=req.with_subtitles,
        subtitle_language=req.subtitle_language,
    )
    return RunResponse(
        project_id=project_id,
        status="running",
        websocket=f"/ws/progress/{project_id}",
    )


@router.post("/rerun", response_model=RunResponse)
def rerun_phase(req: PhaseRerunRequest, background: BackgroundTasks):
    if req.phase not in ("story", "audio", "video"):
        raise HTTPException(400, f"unknown phase {req.phase}")
    if not sm.latest(req.project_id):
        raise HTTPException(404, f"project {req.project_id} not found")
    run_registry.create(req.project_id)
    background.add_task(
        pipeline_service.rerun_phase_async,
        project_id=req.project_id, phase=req.phase,
    )
    return RunResponse(
        project_id=req.project_id,
        status="running",
        websocket=f"/ws/progress/{req.project_id}",
    )


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
    """Lightweight status — phase progress + last event."""
    snapshot = run_registry.snapshot(project_id)
    return snapshot or {"project_id": project_id, "status": "unknown"}
