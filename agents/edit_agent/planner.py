"""Edit planner — turn an EditIntent into a sequence of executor steps.

We keep this very small: an intent maps to one (sometimes two) executor calls.
A LangGraph implementation would expose this as a graph node; the spec asks
for stateful multi-turn editing so we keep `plan` pure and let the agent loop
own the state.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List

from shared.schemas.edit import EditIntent

from .vocabulary import ASK_FOR, EDITS, UNCLEAR_MESSAGE, missing


@dataclass
class EditStep:
    name: str            # e.g. "rerun_audio", "apply_filter", "regenerate_scene"
    target: str          # phase target (audio/video_frame/video/script)
    scope: str = "global"
    params: Dict[str, Any] = field(default_factory=dict)


def plan(intent: EditIntent) -> List[EditStep]:
    """Steps for an edit the editor knows how to make — and only those.

    A request it can't name, or one missing what it needs ("change the tone"
    — to what?), raises with what to say instead. It used to fall through to
    re-recording or recutting the film unchanged and report success.
    """
    name = intent.intent
    if name not in EDITS:
        raise ValueError(UNCLEAR_MESSAGE)
    params = dict(intent.parameters or {})
    lacking = missing(name, params)
    if lacking:
        raise ValueError(ASK_FOR.get(lacking[0], f"the edit needs a {lacking[0]}"))
    target = EDITS[name].target
    scope = intent.scope or "global"

    if target == "script":
        return [
            EditStep("regenerate_script", "script", scope, params),
            EditStep("rerun_audio", "audio", "global"),
            EditStep("rerun_video", "video", "global"),
        ]

    if target == "audio":
        if name in ("change_voice_tone", "change_voice", "regenerate_audio", "adjust_volume"):
            return [
                EditStep("rerun_audio", "audio", scope, params),
                EditStep("recompose_video", "video", "global"),
            ]
        if name in ("add_background_music",):
            return [
                EditStep("regenerate_bgm", "audio", scope, params),
                EditStep("recompose_video", "video", "global"),
            ]
        if name == "remove_background_music":
            return [
                EditStep("disable_bgm", "audio", "global", params),
                EditStep("recompose_video", "video", "global"),
            ]
        return [EditStep("rerun_audio", "audio", scope, params),
                EditStep("recompose_video", "video", "global")]

    if target == "video_frame":
        # These handlers recompose the video themselves.
        if name in ("apply_filter", "adjust_scene_aesthetic"):
            return [EditStep("apply_filter", "video_frame", scope, params)]
        if name == "change_character_design":
            return [EditStep("regenerate_portraits", "video_frame", scope, params)]
        return [EditStep("regenerate_scene", "video_frame", scope, params)]

    # target == "video"
    if name == "remove_subtitles":
        return [EditStep("recompose_video", "video", "global", {"subtitles": False})]
    if name == "add_subtitles":
        return [EditStep("recompose_video", "video", "global", {"subtitles": True})]
    if name in ("speed_up", "slow_down"):
        factor = float(params.get("factor", 1.5 if name == "speed_up" else 0.75))
        # "slow down 2x" means half speed, not double.
        if (name == "slow_down") == (factor > 1):
            factor = 1 / factor
        return [EditStep("change_speed", "video", scope, {**params, "factor": factor})]
    return [EditStep("recompose_video", "video", "global", params)]
