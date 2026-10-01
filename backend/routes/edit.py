"""Phase 5 endpoints — natural-language edit + intent classification."""
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

router = APIRouter()
log = get_logger("api.edit")
agent = EditAgent()
sm = StateManager()


class EditRequest(BaseModel):
    project_id: str
    query: str = Field(..., min_length=1)


class ClassifyRequest(BaseModel):
    query: str
    project_id: Optional[str] = None


def _require_owned(project_id: str, user: User) -> None:
    """The project id is in the body here, so the check is explicit."""
    if not accounts.may_access(user, project_id):
        raise HTTPException(404, f"project {project_id} not found")


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
    _require_owned(req.project_id, user)
    # The edit is attributed to the signed-in account, not to whatever the
    # request body claimed.
    cmd = EditCommand(project_id=req.project_id, query=req.query, user_id=user.id)
    result = agent.edit(cmd)
    if not result.success:
        raise HTTPException(400, result.error or "edit failed")
    return result.model_dump(mode="json")


@router.get("/log/{project_id}", dependencies=[Depends(require_project)])
def edit_log(project_id: str):
    return sm.edit_history(project_id)
