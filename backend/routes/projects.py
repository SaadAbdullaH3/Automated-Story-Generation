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
            "poster_url": asset_url(_poster(state)),
            "stage": state.stage,
            "scene_count": len(state.script.scenes) if state.script else 0,
            "duration_ms": state.video.duration_ms if state.video else None,
        })
    return out


def _poster(state):
    """A frame to show for the film: its first storyboard preview, else the
    first scene's wide shot."""
    board = getattr(state, "storyboard", None)
    if board and board.frames and board.frames[0].preview_path:
        return board.frames[0].preview_path
    video = getattr(state, "video", None)
    if video and getattr(video, "frames", None):
        return video.frames[0].image_path
    return None
