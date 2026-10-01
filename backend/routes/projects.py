"""List + browse projects."""
from __future__ import annotations

from fastapi import APIRouter

from shared.assets import asset_url
from state_manager.state_manager import StateManager

router = APIRouter()
sm = StateManager()


@router.get("/")
def list_projects():
    project_ids = sm.list_projects()
    out = []
    for pid in project_ids:
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
