"""Shared Pydantic schemas — the contract between all pipeline phases."""
from .story import Character, Scene, DialogueLine, StoryOutput, ScriptOutput, CharacterRoster
from .audio import VoiceConfig, AudioSegment, SceneTiming, TimingManifest, AudioOutput
from .video import VisualPrompt, SceneFrame, VideoOutput, Shot, CharacterPortrait
from .pipeline import PipelineState, PhaseStatus, PipelineVersion
from .edit import EditIntent, EditTarget, EditCommand, EditResult

__all__ = [
    "Character",
    "Scene",
    "DialogueLine",
    "StoryOutput",
    "ScriptOutput",
    "CharacterRoster",
    "VoiceConfig",
    "AudioSegment",
    "SceneTiming",
    "TimingManifest",
    "AudioOutput",
    "VisualPrompt",
    "SceneFrame",
    "Shot",
    "CharacterPortrait",
    "VideoOutput",
    "PipelineState",
    "PhaseStatus",
    "PipelineVersion",
    "EditIntent",
    "EditTarget",
    "EditCommand",
    "EditResult",
]
