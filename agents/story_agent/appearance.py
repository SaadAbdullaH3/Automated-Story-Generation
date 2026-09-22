"""Appearance locks — one fixed description per character, used everywhere.

Image models redraw from scratch every time, so "young adventurer" yields a
different person in every shot. Each character therefore gets:

* an **appearance lock**: their own description, topped up with stable details
  (hair, wardrobe, eyes, palette) for whatever the writer left vague, and
* an **image seed**: fixed per character, so the same prompt redraws the same face.

Both are stored on the character, so re-renders and edits keep the same look
until someone deliberately asks for a new design.
"""
from __future__ import annotations
import hashlib
from typing import List

from shared.schemas.story import Character, ScriptOutput

# Stable detail pools. A character's own hash picks from them, so the choice is
# deterministic per character and varied across a cast.
HAIR = ["short dark hair", "long copper hair", "cropped silver hair", "black braided hair",
        "wavy auburn hair", "tied-back blond hair", "curly chestnut hair"]
EYES = ["dark brown eyes", "grey-green eyes", "hazel eyes", "deep blue eyes",
        "amber eyes", "near-black eyes"]
WARDROBE = ["a worn moss-green coat", "a patched indigo jacket", "a long charcoal overcoat",
            "a faded rust-red scarf and jacket", "a plain linen shirt and leather straps",
            "a deep teal raincoat"]
MARK = ["a small scar through one eyebrow", "round wire glasses", "a beaded ear cuff",
        "freckles across the nose", "a crooked half-smile", "ink-stained fingers"]

AGE_WORDS = {"child": "a child", "young": "a young adult",
             "adult": "an adult", "elderly": "an elderly person"}

# If the writer already described one of these, we don't invent a competing detail.
KEYWORDS = {
    "hair": ("hair", "bald", "braid", "ponytail"),
    "eyes": ("eye", "eyes", "gaze"),
    "wardrobe": ("coat", "jacket", "shirt", "dress", "cloak", "uniform", "robe",
                 "scarf", "armour", "armor", "suit"),
    "mark": ("scar", "glasses", "freckle", "tattoo", "mark", "beard", "mustache"),
}


def _pick(pool: List[str], character: Character, salt: str) -> str:
    digest = hashlib.md5(f"{character.id}|{character.name}|{salt}".encode()).hexdigest()
    return pool[int(digest[:8], 16) % len(pool)]


def build_appearance_lock(character: Character, salt: str = "") -> str:
    """The character's canonical look: their description plus any missing basics.

    A `salt` re-rolls the invented details — that's what "change character
    design" does, deliberately.
    """
    described = character.visual_description.lower()
    parts = [f"{character.name}, {AGE_WORDS.get(character.voice_age, 'an adult')}",
             character.visual_description.strip().rstrip(".")]
    for field, pool in (("hair", HAIR), ("eyes", EYES),
                        ("wardrobe", WARDROBE), ("mark", MARK)):
        if not any(word in described for word in KEYWORDS[field]):
            parts.append(_pick(pool, character, f"{field}|{salt}"))
    return ", ".join(p for p in parts if p)


def character_seed(character: Character, salt: str = "") -> int:
    """Stable per-character image seed (a salt gives a deliberate new design)."""
    digest = hashlib.md5(f"{character.id}|{character.name}|{salt}".encode()).hexdigest()
    return int(digest[:8], 16) % 2**31


def lock_appearances(script: ScriptOutput, salt: str = "") -> ScriptOutput:
    """Fill in every character's appearance lock and image seed (idempotent)."""
    for character in script.characters.characters:
        if not character.appearance_lock or salt:
            character.appearance_lock = build_appearance_lock(character, salt)
        if character.image_seed is None or salt:
            character.image_seed = character_seed(character, salt)
    return script
