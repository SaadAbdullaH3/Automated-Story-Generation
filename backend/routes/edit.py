"""Phase 5 endpoints — natural-language edit + intent classification.

An edit writes a new version of the film, so it is a job like a render: it
waits its turn behind the project's other jobs, runs on a worker, and its
progress streams over /ws/progress/{project_id}.
"""
from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from agents.edit_agent import EditAgent
from auth import accounts
from auth.accounts import User
from auth.deps import require_project, require_user
from shared.schemas.edit import EditCommand
from shared.utils.logging import get_logger
from state_manager.state_manager import StateManager

from ..services.pipeline_service import failure_reason, run_and_wait
from .pipeline import RunResponse, _accepted

router = APIRouter()
log = get_logger("api.edit")
agent = EditAgent()
sm = StateManager()


class EditRequest(BaseModel):
    project_id: str
    query: str = Field(..., min_length=1)


class QueueEditRequest(BaseModel):
    query: str = Field(..., min_length=1)


class ClassifyRequest(BaseModel):
    query: str
    project_id: Optional[str] = None


def _require_owned(project_id: str, user: User) -> None:
    """The project id is in the body here, so the check is explicit."""
    if not accounts.may_access(user, project_id):
        raise HTTPException(404, f"project {project_id} not found")


def _require_film(project_id: str) -> None:
    state = sm.latest(project_id)
    if not state:
        raise HTTPException(404, f"project {project_id} not found")
    if not state.video:
        raise HTTPException(409, "render the film before editing it")


@router.post("/classify")
def classify(req: ClassifyRequest, user: User = Depends(require_user)):
    """Classifying reads the project's script to match character names, so it
    is only allowed against a project this account owns."""
    state = None
    if req.project_id:
        _require_owned(req.project_id, user)
        state = sm.latest(req.project_id)
    intent = agent.classify(EditCommand(project_id=req.project_id or "", query=req.query),
                            state=state)
    return intent.model_dump(mode="json")


@router.post("/apply")
def apply_edit(req: EditRequest, user: User = Depends(require_user)):
    """Apply an edit and answer with the result (the classic page waits for it)."""
    _require_owned(req.project_id, user)
    _require_film(req.project_id)
    # The edit is attributed to the signed-in account, not to whatever the
    # request body claimed.
    try:
        job, final = run_and_wait("edit", req.project_id,
                                  {"query": req.query, "user_id": user.id})
    except TimeoutError as e:
        raise HTTPException(504, f"the edit is still running ({e})")
    if job.status != "succeeded":
        raise HTTPException(400, failure_reason(job.error) or "edit failed")
    return final.get("result") or {}


@router.post("/{project_id}", response_model=RunResponse,
             dependencies=[Depends(require_project)])
def queue_edit(project_id: str, req: QueueEditRequest, user: User = Depends(require_user)):
    """Queue an edit and return at once; progress streams like a render's."""
    _require_film(project_id)
    return _accepted("edit", project_id, "queued", user, max_attempts=1,
                     query=req.query, user_id=user.id)


@router.get("/log/{project_id}", dependencies=[Depends(require_project)])
def edit_log(project_id: str):
    return sm.edit_history(project_id)
