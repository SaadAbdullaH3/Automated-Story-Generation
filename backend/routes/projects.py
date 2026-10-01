"""List + browse projects."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from auth import accounts
from auth.accounts import User
from auth.deps import require_user
from shared.assets import asset_url
from state_manager.state_manager import StateManager

router = APIRouter()
sm = StateManager()


@router.get("/")
def list_projects(user: User = Depends(require_user)):
    """Only this account's projects. An admin sees every one, including the
    ones the CLI made before anybody had an account."""
    visible = accounts.projects_for(user)      # None = admin, no restriction
    out = []
    for pid in sm.list_projects():
        if visible is not None and pid not in visible:
            continue
        state = sm.latest(pid)
        if not state:
            continue
        out.append({
            "project_id": pid,
            "title": state.script.story.title if state.script else "(untitled)",
            "prompt": state.user_prompt,
            "version": state.version,
            "updated_at": state.updated_at,
            "video_url": asset_url(
                state.video.final_video_path if state.video else None),
        })
    return out
