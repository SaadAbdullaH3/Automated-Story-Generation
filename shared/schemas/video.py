"""Phase 3 schemas — visual prompts, scene frames, and final composition."""
from __future__ import annotations
from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class VisualPrompt(BaseModel):
    """Image-generation request for a single scene."""
    scene_id: str
    prompt: str
    negative_prompt: str = ""
    style: str = Field(default="cinematic, detailed, dramatic lighting")
    aspect_ratio: str = "16:9"
    seed: Optional[int] = None


class CharacterPortrait(BaseModel):
    """A close-up portrait used as a talking-head shot."""
    character_id: str
    image_path: str
    is_lip_synced: bool = False
    talking_head_clip: Optional[str] = None  # if provider produced a real clip


class Shot(BaseModel):
    """One sub-clip inside a scene (a single 'cut')."""
    shot_id: str
    scene_id: str
    kind: Literal["establishing", "character", "lip_sync", "broll"] = "establishing"
    character_id: Optional[str] = None
    image_path: str
    clip_path: Optional[str] = None
    duration_ms: int                      # time this shot owns on the film timeline
    motion: str = "ken_burns_diag"
    audio_path: Optional[str] = None     # the dialogue line whose duration drives this shot
    start_ms: int = 0                     # absolute position on the film timeline
    render_frames: int = 0                # frames actually rendered (includes crossfade overlap)


class SceneFrame(BaseModel):
    """A rendered scene with one or more shots and optional final composite."""
    scene_id: str
    image_path: str                       # establishing image (kept for back-compat)
    clip_path: Optional[str] = None       # final per-scene composite (multi-shot if dialogue, else single)
    width: int = 1280
    height: int = 720
    duration_ms: int
    motion: str = "ken_burns_diag"
    transition_in: str = "fade"
    shots: List[Shot] = Field(default_factory=list)
    start_ms: int = 0
    # Establishing / B-roll images for this scene (wide, detail, alt).
    shot_bank: List[str] = Field(default_factory=list)
    # character_id -> portrait used only in this scene (e.g. after a scene-scoped filter).
    portrait_overrides: Dict[str, str] = Field(default_factory=dict)
    # Fingerprint of the shot plan + source images the clip was rendered from;
    # lets recomposition skip scenes that haven't changed.
    plan_signature: str = ""
    # Optional real-motion clip for the establishing shot (text/image-to-video).
    t2v_clip: Optional[str] = None


class VideoOutput(BaseModel):
    """Top-level Phase 3 output."""
    project_id: str
    frames: List[SceneFrame]
    final_video_path: str
    width: int = 1280
    height: int = 720
    fps: int = 24
    has_subtitles: bool = False
    duration_ms: int = 0
    portraits: List[CharacterPortrait] = Field(default_factory=list)
    used_text_to_video: bool = False
    used_lip_sync: bool = False
    cinematic_post: bool = True           # vignette + grain + grade on every shot
    speed_factor: float = Field(default=1.0, gt=0)
    # Subtitles: default track + every language actually embedded.
    subtitle_language: str = "English"
    subtitle_languages: List[str] = Field(default_factory=list)
    # Translation cache: language -> texts aligned with the dialogue segments.
    # Valid only while `subtitle_source` matches the current English lines.
    subtitle_source: List[str] = Field(default_factory=list)
    subtitle_cache: Dict[str, List[str]] = Field(default_factory=dict)

    def portrait_for(self, character_id: str) -> Optional[CharacterPortrait]:
        return next((p for p in self.portraits if p.character_id == character_id), None)
