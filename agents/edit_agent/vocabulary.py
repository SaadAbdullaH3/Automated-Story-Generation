"""The edits the editor can actually carry out, and what each one needs.

A language model asked for "a short snake_case action name" with free-form
parameters invents its own: a real run classified "make the voices in scene 2
whispered" as `whisper_voices` with `{"voice_type": "whisper"}`. Nothing
downstream knows those names, so the edit re-recorded the same lines in the
same voice and reported success. The model now fills in a closed form built
from this table, and anything it cannot map is "unclear" — which fails with a
useful message instead of pretending.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Literal, Mapping, Optional, Tuple

from pydantic import BaseModel, Field

from agents.audio_agent.agent import TONE_PRESETS
from mcp.tools.vision_tools.image_edit_tool import list_filter_names

MOODS = ("ambient", "tense", "joyful", "mysterious", "epic", "sad",
         "ominous", "ethereal", "energetic", "neutral")
TONES = tuple(TONE_PRESETS)
FILTERS = tuple(sorted(list_filter_names()))
AESTHETICS = tuple(a for a in ("darker", "brighter", "warm", "cool") if a in FILTERS)


@dataclass(frozen=True)
class Edit:
    target: str
    needs: Tuple[str, ...]      # parameters it cannot do without
    means: str                  # for the model's prompt


EDITS: Dict[str, Edit] = {
    "change_voice_tone": Edit("audio", ("tone",), "how voices sound (tone)"),
    "change_voice": Edit("audio", (), "swap to different voices"),
    "adjust_volume": Edit("audio", ("volume",), "louder or quieter voices (volume multiplier)"),
    "add_background_music": Edit("audio", (), "add or change background music (mood)"),
    "remove_background_music": Edit("audio", (), "take the music out"),
    "regenerate_audio": Edit("audio", (), "record the same voices again"),
    "apply_filter": Edit("video_frame", ("filter",), "a look over the pictures (filter)"),
    "adjust_scene_aesthetic": Edit("video_frame", ("aesthetic",),
                                   "make pictures darker/brighter/warmer/cooler (aesthetic)"),
    "regenerate_scene": Edit("video_frame", (), "draw new pictures"),
    "change_character_design": Edit("video_frame", (), "redesign how a character looks"),
    "remove_subtitles": Edit("video", (), "subtitles off"),
    "add_subtitles": Edit("video", (), "subtitles on"),
    "speed_up": Edit("video", (), "faster (optional factor)"),
    "slow_down": Edit("video", (), "slower (optional factor)"),
    "recompose_video": Edit("video", (), "cut the film again as it is"),
    "regenerate_script": Edit("script", (), "rewrite the story"),
    "change_genre": Edit("script", (), "rewrite the story in another genre (genre)"),
}

UNCLEAR = "unclear"
UNCLEAR_MESSAGE = ("not sure what to change: name a scene, a voice, the music, "
                   "the look or the pace, e.g. “make scene 2 darker”")

# How to ask for a missing parameter, naming the choices.
ASK_FOR = {
    "tone": "which tone? " + ", ".join(TONES),
    "volume": "louder or quieter?",
    "filter": "which look? " + ", ".join(f for f in FILTERS if f not in ("invert", "blur")),
    "aesthetic": "darker, brighter, warmer or cooler?",
}

IntentName = Literal[tuple(EDITS) + (UNCLEAR,)]  # type: ignore[valid-type]


class EditDraft(BaseModel):
    """What the language model fills in. Every field is something the editor acts on."""
    intent: IntentName  # type: ignore[valid-type]
    # Optional because a model answering "unclear" often leaves it null.
    scope: Optional[str] = Field(default="global",
                                 description="'global', 'scene:<id>' or 'character:<id>'")
    tone: Optional[Literal[TONES]] = None  # type: ignore[valid-type]
    volume: Optional[float] = Field(default=None, description="1.3 louder, 0.7 quieter")
    mood: Optional[Literal[MOODS]] = None  # type: ignore[valid-type]
    filter: Optional[Literal[FILTERS]] = None  # type: ignore[valid-type]
    aesthetic: Optional[Literal[AESTHETICS]] = None  # type: ignore[valid-type]
    factor: Optional[float] = Field(default=None, description="speed multiplier, e.g. 1.5")
    genre: Optional[str] = None
    reasoning: str = ""


def describe_for_model() -> str:
    """The form, as the prompt states it (some providers never see the schema)."""
    lines = [f"- {name}: {edit.means}" for name, edit in EDITS.items()]
    lines.append(f"- {UNCLEAR}: the request doesn't match any of these")
    return "\n".join([
        "intent — exactly one of:", *lines,
        f"tone — one of: {', '.join(TONES)}",
        f"filter — one of: {', '.join(FILTERS)}",
        f"aesthetic — one of: {', '.join(AESTHETICS)}",
        f"mood — one of: {', '.join(MOODS)}",
        "volume — a multiplier (1.3 louder, 0.7 quieter); factor — a speed multiplier",
        "Leave every field that doesn't apply as null.",
    ])


def normalise_scope(scope: str, scenes: Iterable[str], characters: Iterable[str],
                    names: Mapping[str, str]) -> str:
    """Accept the ways a model writes a scope: 'scene:2', 'scene 2', 'character:Marco'."""
    raw = (scope or "").strip()
    if not raw or raw.lower() in ("global", "all", "film", "whole film"):
        return "global"
    kind, _, value = raw.partition(":")
    kind, value = kind.strip().lower(), value.strip()
    scenes, characters = list(scenes), list(characters)
    if not value:                                    # "scene 2", "scene_2"
        m = re.fullmatch(r"scene[_\s]*(\d+)", raw.lower())
        if m:
            kind, value = "scene", m.group(1)
    if kind == "scene":
        if value in scenes:
            return f"scene:{value}"
        if value.isdigit() and f"scene_{value}" in scenes:
            return f"scene:scene_{value}"
        return f"scene:{value}"                      # unknown — the executor says so
    if kind == "character":
        if value in characters:
            return f"character:{value}"
        by_name = {n.lower(): cid for cid, n in names.items()}
        if value.lower() in by_name:
            return f"character:{by_name[value.lower()]}"
        if f"char_{value.lower()}" in characters:
            return f"character:char_{value.lower()}"
        return f"character:{value}"
    return raw


def draft_parameters(draft: EditDraft) -> Dict[str, object]:
    """The parameters the planner and executor read, under the names they read."""
    params: Dict[str, object] = {}
    for key in ("tone", "volume", "mood", "filter", "aesthetic", "factor", "genre"):
        value = getattr(draft, key)
        if value is not None:
            params[key] = value
    if draft.intent == "change_voice":
        params["voice"] = "alternate"
    return params


def missing(intent: str, parameters: Mapping[str, object]) -> List[str]:
    edit = EDITS.get(intent)
    return [p for p in (edit.needs if edit else ()) if parameters.get(p) in (None, "")]
