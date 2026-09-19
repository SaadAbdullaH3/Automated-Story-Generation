"""List + browse projects."""
from __future__ import annotations
from pathlib import PureWindowsPath  # splits on both "\" and "/"

from fastapi import APIRouter

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
            "video_url": (
                f"/assets/{pid}/{PureWindowsPath(state.video.final_video_path).name}"
                if state.video and state.video.final_video_path else None
            ),
        })
    return out
