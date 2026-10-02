"""HTTP endpoints to launch and re-run the main pipeline.

Starting a run enqueues a job and returns immediately; a worker picks it up.
The response carries the job id so the caller can follow, cancel or retry it.
"""
from __future__ import annotations
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import jobs
from auth import accounts
from auth.deps import require_project, require_user
from auth.accounts import User
from shared import voices
from agents.orchestrator.storyboard_view import cast, image_budget, scene_cards
from shared.assets import asset_url
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


def _accepted(kind: str, project_id: str, status: str, owner: User,
              max_attempts: int = 2, **payload) -> RunResponse:
    payload = {k: v for k, v in payload.items() if v is not None}
    # Record the owner before the job exists, so a worker can't finish a render
    # into a project nobody is responsible for.
    accounts.register_project(project_id, owner.id)
    job = jobs.enqueue(kind, project_id, payload, max_attempts=max_attempts)
    log.info("queued %s %s for %s", kind, job.id, project_id)
    return RunResponse(project_id=project_id, job_id=job.id, status=status,
                       websocket=f"/ws/progress/{project_id}")


@router.post("/run", response_model=RunResponse)
def start_run(req: RunRequest, user: User = Depends(require_user)):
    """Queue a full pipeline run; progress streams over /ws/progress/{project_id}."""
    _check_engine(req.tts_engine)
    return _accepted(
        "run_full", new_project_id(), "queued", user,
        prompt=req.prompt, target_duration_s=req.target_duration_s,
        scene_count=req.scene_count, with_bgm=req.with_bgm,
        with_subtitles=req.with_subtitles, subtitle_language=req.subtitle_language,
        tts_engine=req.tts_engine,
    )


@router.post("/rerun", response_model=RunResponse)
def rerun_phase(req: PhaseRerunRequest, user: User = Depends(require_user)):
    if req.phase not in ("story", "audio", "video"):
        raise HTTPException(400, f"unknown phase {req.phase}")
    # The project id arrives in the body, so the check happens here rather
    # than through the path dependency.
    if not accounts.may_access(user, req.project_id) or not sm.latest(req.project_id):
        raise HTTPException(404, f"project {req.project_id} not found")
    return _accepted("rerun_phase", req.project_id, "queued", user, phase=req.phase)


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
def start_plan(req: PlanRequest, user: User = Depends(require_user)):
    """Write the script + preview images only; render is a separate, approved step."""
    return _accepted(
        "plan", new_project_id(), "planning", user,
        prompt=req.prompt, target_duration_s=req.target_duration_s,
        scene_count=req.scene_count, with_preview=req.with_preview,
    )


@router.get("/storyboard/{project_id}", dependencies=[Depends(require_project)])
def get_storyboard(project_id: str, engine: Optional[str] = None):
    """The storyboard, as cards.

    `engine` names the voice engine the creator is about to render with, so
    each line shows the voice that will actually speak it.
    """
    if engine and voices.get(engine) is None:
        raise HTTPException(400, f"unknown voice engine {engine!r}")
    state = sm.latest(project_id)
    if not state or not state.storyboard:
        raise HTTPException(404, f"no storyboard for {project_id}")
    board = state.storyboard.model_dump(mode="json")
    # The same cards the live plan streamed, so a reload looks identical.
    cards = {c["scene_id"]: c for c in scene_cards(state.script, engine)}
    for frame in board["frames"]:
        if frame.get("preview_path"):
            frame["preview_url"] = asset_url(frame["preview_path"])
        card = cards.get(frame["scene_id"], {})
        frame["tone"] = card.get("tone", "")
        frame["move"] = card.get("move", "")
        frame["lines"] = card.get("lines", [])
    board["cast"] = cast(state.script, engine)
    board["images"] = image_budget(state.script)
    board["stage"] = state.stage
    board["version"] = state.version
    return board


@router.patch("/storyboard/{project_id}/{scene_id}",
              dependencies=[Depends(require_project)])
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
def start_render(project_id: str, req: RenderRequest,
                 user: User = Depends(require_project)):
    """Approve a storyboard and render the film."""
    state = sm.latest(project_id)
    if not state or not state.script:
        raise HTTPException(404, f"no storyboard to render for {project_id}")
    _check_engine(req.tts_engine)
    return _accepted(
        "render", project_id, "rendering", user,
        with_bgm=req.with_bgm, with_subtitles=req.with_subtitles,
        subtitle_language=req.subtitle_language, burn_subtitles=req.burn_subtitles,
        tts_engine=req.tts_engine,
    )


@router.get("/subtitles/{project_id}", dependencies=[Depends(require_project)])
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
                           "url": asset_url(vtt),
                           "burned_in": lang == state.video.burned_subtitle_language})
    return tracks


@router.get("/film/{project_id}", dependencies=[Depends(require_project)])
def film(project_id: str):
    """Everything the player needs, as URLs rather than file paths.

    The state endpoint returns raw paths (Windows ones, on this machine), and
    the old page split those strings in the browser to guess an asset URL —
    which stops working the moment assets are served from a bucket.
    """
    state = sm.latest(project_id)
    if not state or not state.video or not state.video.final_video_path:
        raise HTTPException(404, f"no film yet for {project_id}")
    video = state.video
    speed = video.speed_factor or 1.0
    manifest = state.audio.manifest if state.audio else None
    timings = {s.scene_id: s for s in (manifest.scenes if manifest else [])}
    previews = {f.scene_id: f.preview_path
                for f in (state.storyboard.frames if state.storyboard else [])}
    stills = {f.scene_id: f.image_path for f in (video.frames or [])}

    chapters = []
    for scene in (state.script.scenes if state.script else []):
        t = timings.get(scene.scene_id)
        chapters.append({
            "scene_id": scene.scene_id,
            "index": scene.index,
            "title": scene.title,
            "tone": getattr(scene, "tone", "") or "",
            # A sped-up film plays its timeline faster, so the chapter marks move.
            "start_ms": round(t.start_ms / speed) if t else None,
            "end_ms": round(t.end_ms / speed) if t else None,
            "poster_url": asset_url(previews.get(scene.scene_id)
                                    or stills.get(scene.scene_id)),
        })
    return {
        "project_id": project_id,
        "title": state.script.story.title if state.script else "",
        "logline": state.script.story.logline if state.script else "",
        "video_url": asset_url(video.final_video_path),
        "duration_ms": video.duration_ms,
        "width": video.width, "height": video.height, "fps": video.fps,
        "version": state.version,
        "chapters": chapters,
        "subtitles": subtitle_tracks(project_id),
    }


@router.get("/languages")
def subtitle_languages():
    """Subtitle languages the pipeline can translate + embed."""
    return supported_names()


@router.get("/state/{project_id}", dependencies=[Depends(require_project)])
def get_state(project_id: str):
    state = sm.latest(project_id)
    if not state:
        raise HTTPException(404, f"project {project_id} not found")
    return state.model_dump(mode="json")


@router.get("/status/{project_id}", dependencies=[Depends(require_project)])
def get_status(project_id: str):
    """Lightweight status — job state, phase progress and the last event."""
    return progress.snapshot(project_id) or {"project_id": project_id, "status": "unknown"}
