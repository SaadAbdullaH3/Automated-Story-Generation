"""Storyboard — the cheap draft you approve before paying for a full render.

Planning costs one small image per scene and a few seconds of LLM time.
Rendering costs every image, every voice line and all the video work, so the
storyboard is where a story gets fixed, not the finished film.
"""
from __future__ import annotations
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class StoryboardLine(BaseModel):
    """One spoken line, as shown on a storyboard card."""
    line_id: str
    character_id: str
    character_name: str = ""
    text: str


class StoryboardFrame(BaseModel):
    """One scene as it appears on the storyboard."""
    scene_id: str
    index: int
    title: str
    setting: str
    visual_prompt: str
    preview_path: Optional[str] = None
    dialogue: List[StoryboardLine] = Field(default_factory=list)
    estimated_ms: int = 0


class Storyboard(BaseModel):
    project_id: str
    prompt: str
    title: str = ""
    logline: str = ""
    frames: List[StoryboardFrame] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    approved_at: Optional[str] = None
    preview_width: int = 512
    preview_height: int = 288

    @property
    def approved(self) -> bool:
        return self.approved_at is not None

    def frame(self, scene_id: str) -> Optional[StoryboardFrame]:
        return next((f for f in self.frames if f.scene_id == scene_id), None)

    def estimated_duration_ms(self) -> int:
        return sum(f.estimated_ms for f in self.frames)

    def touch(self) -> None:
        self.updated_at = datetime.utcnow().isoformat()
