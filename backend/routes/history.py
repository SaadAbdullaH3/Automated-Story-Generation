"""Version history + revert endpoints."""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException

from agents.edit_agent import EditAgent
from auth.deps import require_project
from state_manager.state_manager import StateManager
from state_manager.history import format_history

router = APIRouter()
sm = StateManager()
agent = EditAgent(sm)


@router.get("/{project_id}", dependencies=[Depends(require_project)])
def list_history(project_id: str):
    rows = sm.history(project_id)
    if not rows:
        raise HTTPException(404, f"no history for {project_id}")
    return format_history(rows)


@router.post("/{project_id}/revert/{version}", dependencies=[Depends(require_project)])
def revert(project_id: str, version: int):
    try:
        state = agent.revert(project_id, version)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"reverted_to": version, "new_state": state.model_dump(mode="json")}
