"""Edit-intent classification.

Two paths:
1. LLM-backed structured-output classifier (Gemini/OpenAI/Claude) when an API
   key is configured. Returns a validated EditIntent.
2. A deterministic keyword + semantic fallback that runs offline. Tests
   exercise this path so the project always has reliable intent detection.
"""
from __future__ import annotations
import re
from typing import Dict, List, Tuple

from mcp.tools.llm_tools.llm_client import get_llm_client
from mcp.tools.vision_tools.image_edit_tool import list_filter_names
from shared.schemas.edit import EditIntent
from shared.utils.logging import get_logger

from .vocabulary import (EDITS, UNCLEAR, EditDraft, describe_for_model, draft_parameters,
                         missing, normalise_scope)

log = get_logger("intent_classifier")


# (keyword regex, intent, target, parameters_extractor)
_RULES: List[Tuple[re.Pattern, str, str, str]] = [
    # Audio --------------------------------------------------------------
    # Inflections count: "voices", "narrator", "whispered", "deeper", "warmer".
    (re.compile(r"\b(voices?|tone|speak\w*|narrat\w*)\b.*"
                r"\b(tone|whisper\w*|deep(er)?|cheer\w*|warm(er)?|angry|softer|louder)\b", re.I),
     "change_voice_tone", "audio", "tone"),
    (re.compile(r"\b(change|set|make).*\bvoices?\b", re.I),
     "change_voice", "audio", "voice"),
    (re.compile(r"\b(louder|quieter|volume|softer)\b", re.I),
     "adjust_volume", "audio", "volume"),
    (re.compile(r"\b(add|put|include).*\b(background music|bgm|soundtrack|music)\b", re.I),
     "add_background_music", "audio", "mood"),
    (re.compile(r"\b(remove|delete|drop).*\b(background music|bgm|music)\b", re.I),
     "remove_background_music", "audio", ""),
    (re.compile(r"\bregenerate.*\b(audio|voice|tts|narration)\b", re.I),
     "regenerate_audio", "audio", ""),
    # Video frame --------------------------------------------------------
    (re.compile(r"\b(make|set).*\b(scene|image|shot)\b.*\b(dark|darker|brighter|moody|gloom)\b", re.I),
     "adjust_scene_aesthetic", "video_frame", "aesthetic"),
    (re.compile(r"\b(change|update|alter).*\bcharacter\b.*\b(design|appearance|look)\b", re.I),
     "change_character_design", "video_frame", "character"),
    (re.compile(r"\b(regenerate|redo|remake).*\b(scene|image|frame)\b", re.I),
     "regenerate_scene", "video_frame", "scene"),
    (re.compile(r"\bapply.*\b(filter|style)\b", re.I),
     "apply_filter", "video_frame", "filter"),
    (re.compile(r"\b(grayscale|sepia|vintage|noir|cinematic|dreamy|warm|cool|pastel)\b", re.I),
     "apply_filter", "video_frame", "filter_name"),
    # Video --------------------------------------------------------------
    (re.compile(r"\b(remove|delete|drop|hide).*\bsubtitle", re.I),
     "remove_subtitles", "video", ""),
    (re.compile(r"\b(add|enable|turn on).*\bsubtitle", re.I),
     "add_subtitles", "video", ""),
    (re.compile(r"\b(speed up|faster|quicken)\b", re.I),
     "speed_up", "video", "factor"),
    (re.compile(r"\b(slow down|slower)\b", re.I),
     "slow_down", "video", "factor"),
    (re.compile(r"\b(recompose|recompile|rebuild|rerender|re-render).*\bvideo\b", re.I),
     "recompose_video", "video", ""),
    # Script -------------------------------------------------------------
    (re.compile(r"\b(regenerate|rewrite|redo).*\b(script|story|plot|dialogue)\b", re.I),
     "regenerate_script", "script", ""),
    (re.compile(r"\b(change|alter|update).*\b(genre|tone|mood)\b", re.I),
     "change_genre", "script", "genre"),
]


# Every filter / style preset the image edit tool can apply, longest first so
# e.g. "cold_thriller" wins over "cool". Kept in sync with the tool itself.
_FILTER_NAMES = sorted(list_filter_names(), key=lambda n: (-len(n), n))

_TONES = {
    "whisper": "whispered", "whispered": "whispered",
    "deep": "deep", "warm": "warm", "cheer": "cheerful",
    "angry": "angry", "sad": "sad", "anxious": "anxious",
    "soft": "soft", "loud": "loud",
}

_MOODS = {
    "ambient", "tense", "joyful", "mysterious", "epic", "sad",
    "ominous", "ethereal", "energetic", "neutral",
}


SYSTEM_PROMPT = """You turn a creator's request to change their short animated film
into one edit the editor can carry out. Only the intents and values listed are
possible; never invent new ones. If the request matches none, use "unclear".
Always reply with one valid JSON object."""


CLASSIFICATION_PROMPT = """Request: "{query}"

Scenes: {scenes}
Characters: {chars}

Fill in this form as one JSON object with exactly the keys intent, scope, tone,
volume, mood, filter, aesthetic, factor, genre, reasoning.

{form}
scope — "global", "scene:<scene id>" or "character:<character id>": the part of
the film the request is about.
reasoning — one short sentence.
"""


class IntentClassifier:
    def __init__(self):
        self.llm = get_llm_client("edit_intent")

    # ---- public API ------------------------------------------------------

    def classify(self, query: str, scenes: List[str] | None = None,
                 characters: List[str] | None = None,
                 character_names: Dict[str, str] | None = None,
                 scene_titles: Dict[str, str] | None = None) -> EditIntent:
        """`characters` are ids (char_protagonist); `character_names` maps id -> name
        (Aria) so users can refer to characters the way the story does."""
        names = character_names or {}
        scenes, characters = scenes or [], characters or []
        keyword = self._keyword_classify(query, scenes, characters, names)
        if self.llm.provider == "mock":
            return keyword
        try:
            intent = self._llm_classify(query, scenes, characters, names, scene_titles or {})
        except Exception as e:  # noqa: BLE001
            log.warning("LLM intent classification failed (%s) — using keyword fallback", e)
            return keyword
        if intent.intent == UNCLEAR or missing(intent.intent, intent.parameters):
            # The keyword rules sometimes catch what the model gave up on.
            if keyword.intent in EDITS and not missing(keyword.intent, keyword.parameters):
                log.info("model answered %s; keyword rules found %s", intent.intent,
                         keyword.intent)
                return keyword
        return intent

    # ---- LLM path --------------------------------------------------------

    def _llm_classify(self, query: str, scenes: List[str], characters: List[str],
                      names: Dict[str, str], titles: Dict[str, str]) -> EditIntent:
        chars = ", ".join(f"{c} ({names[c]})" if c in names else c for c in characters)
        prompt = CLASSIFICATION_PROMPT.format(
            query=query,
            scenes=", ".join(f"{s} ({titles[s]})" if s in titles else s for s in scenes)
            or "scene_1, scene_2, scene_3, scene_4",
            chars=chars or "char_narrator, char_protagonist, char_supporting",
            form=describe_for_model(),
        )
        draft = self.llm.generate_structured(
            prompt=prompt,
            schema=EditDraft,
            system=SYSTEM_PROMPT,
            temperature=0.1,
        )
        return self.from_draft(draft, scenes, characters, names)

    @staticmethod
    def from_draft(draft: EditDraft, scenes: List[str], characters: List[str],
                   names: Dict[str, str]) -> EditIntent:
        """The model's form, in the names the planner and executor read."""
        if draft.intent == UNCLEAR:
            return EditIntent(intent=UNCLEAR, target="video", scope="global",
                              confidence=0.3, reasoning=draft.reasoning)
        return EditIntent(
            intent=draft.intent,
            target=EDITS[draft.intent].target,
            scope=normalise_scope(draft.scope, scenes, characters, names),
            parameters=draft_parameters(draft),
            confidence=0.85,
            reasoning=draft.reasoning,
        )

    # ---- keyword path ----------------------------------------------------

    def _keyword_classify(self, query: str, scenes: List[str],
                          characters: List[str], names: Dict[str, str] | None = None
                          ) -> EditIntent:
        q = query.lower().strip()

        # Match a scene mention if present.
        scope = "global"
        scene_match = re.search(r"scene[_\s]*(\d+)", q)
        if scene_match:
            scope = f"scene:scene_{scene_match.group(1)}"
        else:
            for s in scenes:
                if s.lower() in q:
                    scope = f"scene:{s}"
                    break

        # Match a character mention (by name, e.g. "Aria", or id, e.g. "protagonist").
        char_scope = ""
        for c in characters:
            aliases = {c.replace("char_", "").lower(), (names or {}).get(c, "").lower()}
            if any(a and re.search(rf"\b{re.escape(a)}\b", q) for a in aliases):
                char_scope = f"character:{c}"
                break
        if not scene_match and char_scope:
            scope = char_scope

        # Apply rule-based intent matches.
        for pattern, intent, target, param_kind in _RULES:
            if pattern.search(q):
                params = self._extract_params(q, param_kind)
                if char_scope and target == "audio":
                    scope = char_scope
                return EditIntent(
                    intent=intent,
                    target=target,
                    scope=scope,
                    parameters=params,
                    confidence=0.78,
                    reasoning=f"keyword match on '{pattern.pattern[:50]}'",
                )

        # Generic fallback: try to detect target by topic words.
        target = "video"
        if any(w in q for w in ("voice", "narration", "tts", "music", "audio", "louder", "quieter")):
            target = "audio"
        elif any(w in q for w in ("scene", "image", "frame", "filter", "darker", "brighter")):
            target = "video_frame"
        elif any(w in q for w in ("script", "story", "plot", "dialogue")):
            target = "script"
        return EditIntent(
            intent="generic_edit",
            target=target,
            scope=scope,
            parameters={"raw_query": query},
            confidence=0.4,
            reasoning="no keyword rule matched; falling back to topic heuristic",
        )

    @staticmethod
    def _extract_params(q: str, kind: str) -> Dict[str, str | float]:
        params: Dict[str, str | float] = {}
        if kind == "tone":
            for k, v in _TONES.items():
                if k in q:
                    params["tone"] = v
                    break
        elif kind in ("filter", "filter_name"):
            for fn in _FILTER_NAMES:
                if re.search(rf"\b{re.escape(fn).replace('_', '[ _]')}\b", q):
                    params["filter"] = fn
                    break
        elif kind == "aesthetic":
            for w in ("dark", "darker", "moody", "gloom", "brighter", "warm", "cool"):
                if w in q:
                    params["aesthetic"] = "darker" if w in ("dark", "moody", "gloom") else w
                    break
        elif kind == "mood":
            for m in _MOODS:
                if m in q:
                    params["mood"] = m
                    break
            params.setdefault("mood", "ambient")
        elif kind == "factor":
            m = re.search(r"(\d+(?:\.\d+)?)\s*x", q)
            params["factor"] = float(m.group(1)) if m else (1.5 if "speed" in q else 0.75)
        elif kind == "volume":
            params["volume"] = 1.3 if "louder" in q else 0.7
        elif kind == "voice":
            params["voice"] = "alternate"
        elif kind == "character":
            params["character_change"] = q
        elif kind == "genre":
            for g in ("comedy", "horror", "drama", "romance", "fantasy", "sci-fi", "scifi", "mystery"):
                if g in q:
                    params["genre"] = g
                    break
        return params


# Convenience function for tests.
def classify(query: str, scenes=None, characters=None, character_names=None,
             scene_titles=None) -> EditIntent:
    return IntentClassifier().classify(query, scenes, characters, character_names, scene_titles)
