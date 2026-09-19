"""Phase 2 schemas — TTS configs, audio segments, and timing manifests."""
from __future__ import annotations
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class VoiceConfig(BaseModel):
    """Per-character TTS configuration."""
    character_id: str
    engine: Literal["gtts", "pyttsx3", "elevenlabs", "edge", "silent", "mock"] = "gtts"
    voice_id: Optional[str] = None
    language: str = "en"
    tld: str = Field(default="com", description="gTTS regional accent: com/co.uk/com.au/...")
    rate: int = Field(default=175, ge=80, le=300, description="words per minute")
    pitch: int = Field(default=0, ge=-50, le=50)
    volume: float = Field(default=1.0, ge=0.0, le=2.0)
    tone: str = Field(default="neutral")


class AudioSegment(BaseModel):
    """A rendered audio clip with timing metadata."""
    segment_id: str
    scene_id: str
    line_id: Optional[str] = None
    character_id: Optional[str] = None
    file_path: str
    kind: Literal["dialogue", "bgm", "sfx"] = "dialogue"
    start_ms: int = Field(..., ge=0)
    end_ms: int = Field(..., ge=0)
    duration_ms: int = Field(..., ge=0)
    text: Optional[str] = None


class SceneTiming(BaseModel):
    """Where a scene sits on the film's timeline (see shared/timeline.py)."""
    scene_id: str
    start_ms: int = Field(..., ge=0)
    end_ms: int = Field(..., ge=0)

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


class TimingManifest(BaseModel):
    """All audio segments with absolute timeline positions.

    This is the film's single timeline: the master audio places each segment
    at `start_ms`, video cuts to the same boundaries, and subtitles use them.
    """
    project_id: str
    total_duration_ms: int
    sample_rate: int = 22050
    segments: List[AudioSegment] = Field(default_factory=list)
    scenes: List[SceneTiming] = Field(default_factory=list)

    def for_scene(self, scene_id: str) -> List[AudioSegment]:
        return [s for s in self.segments if s.scene_id == scene_id]

    def scene(self, scene_id: str) -> Optional[SceneTiming]:
        return next((s for s in self.scenes if s.scene_id == scene_id), None)


class AudioOutput(BaseModel):
    """Top-level Phase 2 output."""
    voice_configs: List[VoiceConfig]
    manifest: TimingManifest
    bgm_track: Optional[str] = Field(default=None, description="path to mixed BGM file")
    master_track: Optional[str] = Field(default=None, description="path to mixed master")
    bgm_enabled: bool = Field(default=True, description="whether BGM is mixed into the master")
