"""Say what an edit was understood as, in the words a creator would use.

Shown before any work starts, so a misread request is visible at once
("new pictures · scene 2" when they meant the music) rather than after a
minute of rendering the wrong thing.
"""
from __future__ import annotations
from typing import Any, Mapping

from agents.audio_agent.agent import TONE_ALIASES

# What each executor step is doing, for progress messages.
STEP_WORDS = {
    "rerun_audio": "Recording the voices again",
    "regenerate_bgm": "Scoring new music",
    "disable_bgm": "Taking the music out",
    "apply_filter": "Regrading the pictures",
    "regenerate_scene": "Drawing the scene again",
    "regenerate_portraits": "Redesigning the characters",
    "recompose_video": "Cutting the film again",
    "change_speed": "Changing the pace",
    "regenerate_script": "Rewriting the script",
    "rerun_video": "Drawing the shots and cutting the film",
}


def _what(intent: Mapping[str, Any]) -> str:
    name = intent.get("intent", "")
    p = intent.get("parameters") or {}
    if name == "change_voice_tone":
        if not p.get("tone"):
            return "a different delivery"
        # The tone the voices will get, not the word the model used for it.
        tone = str(p["tone"]).strip().lower()
        return f"{TONE_ALIASES.get(tone, tone)} voices"
    if name == "change_voice":
        return "different voices"
    if name == "adjust_volume":
        return "louder voices" if float(p.get("volume", 1)) > 1 else "quieter voices"
    if name == "add_background_music":
        return f"{p.get('mood', 'new')} music"
    if name == "remove_background_music":
        return "no music"
    if name in ("apply_filter", "adjust_scene_aesthetic"):
        look = p.get("filter") or p.get("filter_name") or p.get("aesthetic") or "new"
        return f"a {str(look).replace('_', ' ')} look"
    if name == "regenerate_scene":
        return "new pictures"
    if name == "change_character_design":
        return "a new character design"
    if name in ("remove_subtitles", "add_subtitles"):
        return "subtitles " + ("off" if name == "remove_subtitles" else "on")
    if name in ("speed_up", "slow_down"):
        return "faster" if name == "speed_up" else "slower"
    if name in ("regenerate_script", "change_genre"):
        return f"a rewritten {p['genre']} script" if p.get("genre") else "a rewritten script"
    if name == "regenerate_audio":
        return "the voices recorded again"
    return name.replace("_", " ") or "a change"


def _where(scope: str, names: Mapping[str, str]) -> str:
    if scope.startswith("scene:"):
        return "scene " + scope.split(":", 1)[1].rsplit("_", 1)[-1]
    if scope.startswith("character:"):
        cid = scope.split(":", 1)[1]
        return names.get(cid) or cid.replace("char_", "")
    return "the whole film"


def describe(intent: Mapping[str, Any], names: Mapping[str, str] | None = None) -> str:
    """'whispered voices · scene 2', 'a noir look · the whole film'."""
    return f"{_what(intent)} · {_where(intent.get('scope') or 'global', names or {})}"
