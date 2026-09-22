"""Top-level pipeline state container — passed forward and versioned."""
from __future__ import annotations
from typing import Optional, List, Literal, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field

from .story import ScriptOutput
from .audio import AudioOutput
from .storyboard import Storyboard
from .video import VideoOutput


PhaseStatus = Literal["pending", "running", "complete", "failed", "skipped"]
# draft      -> nothing generated yet
# storyboard -> script + preview images, waiting for approval
# rendered   -> the full film exists
Stage = Literal["draft", "storyboard", "rendered"]


class PhaseState(BaseModel):
    """Status block for a single phase."""
    name: str
    status: PhaseStatus = "pending"
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None
    artifact_paths: List[str] = Field(default_factory=list)


class PipelineState(BaseModel):
    """The shared JSON state object passed between all phases."""
    project_id: str
    version: int = 1
    user_prompt: str
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())

    stage: Stage = "draft"
    script: Optional[ScriptOutput] = None
    storyboard: Optional[Storyboard] = None
    audio: Optional[AudioOutput] = None
    video: Optional[VideoOutput] = None

    phase1: PhaseState = Field(default_factory=lambda: PhaseState(name="story"))
    phase2: PhaseState = Field(default_factory=lambda: PhaseState(name="audio"))
    phase3: PhaseState = Field(default_factory=lambda: PhaseState(name="video"))

    metadata: Dict[str, Any] = Field(default_factory=dict)

    def touch(self) -> None:
        self.updated_at = datetime.utcnow().isoformat()


class PipelineVersion(BaseModel):
    """A single snapshot in the version history."""
    version: int
    project_id: str
    created_at: str
    description: str = ""
    state_path: str
    asset_paths: List[str] = Field(default_factory=list)
    parent_version: Optional[int] = None
    edit_intent: Optional[Dict[str, Any]] = None
