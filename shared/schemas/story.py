"""Phase 1 schemas — story, characters, and scene-by-scene script."""
from __future__ import annotations
from typing import List, Optional, Literal
from pydantic import BaseModel, Field, field_validator


class Character(BaseModel):
    """A named character with voice and visual identity."""
    id: str = Field(..., description="stable identifier, e.g. 'char_narrator'")
    name: str
    role: Literal["protagonist", "antagonist", "supporting", "narrator"] = "supporting"
    description: str = Field(..., description="one-sentence personality summary")
    visual_description: str = Field(
        ..., description="appearance details for image generation"
    )
    voice_style: str = Field(
        default="neutral",
        description="warm/whispered/deep/cheerful/anxious/etc — drives TTS",
    )
    voice_gender: Literal["male", "female", "neutral"] = "neutral"
    voice_age: Literal["child", "young", "adult", "elderly"] = "adult"
    # Locked look: the canonical description + fixed seed every image prompt uses,
    # so the character doesn't change face between shots (agents/story_agent/appearance.py).
    appearance_lock: str = ""
    image_seed: Optional[int] = None


class CharacterRoster(BaseModel):
    """Full cast for the short film."""
    characters: List[Character]

    def get(self, char_id: str) -> Optional[Character]:
        return next((c for c in self.characters if c.id == char_id), None)


class DialogueLine(BaseModel):
    """A single spoken line within a scene."""
    line_id: str
    character_id: str = Field(..., description="must match a Character.id")
    text: str
    emotion: str = Field(default="neutral", description="happy/sad/angry/curious/...")
    duration_ms: int = Field(default=2500, ge=500)


class Scene(BaseModel):
    """One narrative scene — visual + dialogue + timing."""
    scene_id: str
    index: int = Field(..., ge=0)
    title: str
    setting: str = Field(..., description="where + when")
    tone: str = Field(default="neutral")
    visual_prompt: str = Field(
        ..., description="prompt-engineered description for image generation"
    )
    camera: str = Field(
        default="medium shot, eye level",
        description="cinematography guidance: close-up / wide / aerial / etc",
    )
    duration_ms: int = Field(default=5000, ge=1000)
    dialogue: List[DialogueLine] = Field(default_factory=list)
    music_mood: str = Field(default="ambient", description="mood for BGM selection")
    transition_in: str = Field(default="fade", description="cut/fade/slide/zoom")

    @field_validator("dialogue")
    @classmethod
    def _check_durations(cls, v: List[DialogueLine]) -> List[DialogueLine]:
        return v


class StoryOutput(BaseModel):
    """Top-level narrative produced by the story agent."""
    project_id: str
    title: str
    logline: str = Field(..., description="one-line pitch")
    synopsis: str = Field(..., description="paragraph summary")
    genre: str = Field(default="drama")
    themes: List[str] = Field(default_factory=list)
    arc: str = Field(
        default="three-act",
        description="three-act / hero-journey / loop / experimental",
    )
    target_duration_s: int = Field(default=45, ge=15, le=600)


class ScriptOutput(BaseModel):
    """Complete pipeline-1 output: story + roster + scenes."""
    story: StoryOutput
    characters: CharacterRoster
    scenes: List[Scene]

    def total_duration_ms(self) -> int:
        return sum(s.duration_ms for s in self.scenes)
