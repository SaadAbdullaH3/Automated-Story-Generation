"""Template-based story generator — used when the configured LLM is the mock provider.

Produces a deterministic but plausible 4-scene short film from any input prompt.
The template is intentionally generic so it works for any genre.
"""
from __future__ import annotations
import hashlib
import random
import re
from typing import List

from shared.schemas.story import (
    Character, CharacterRoster, DialogueLine, Scene, ScriptOutput, StoryOutput,
)
from shared.timeline import (
    LINE_GAP_MS, MIN_LINE_MS, SCENE_PREROLL_MS, SCENE_TAIL_MS, estimate_line_ms,
)


_GENRE_HINTS = {
    "scifi": ("sci-fi", ["wonder", "discovery", "humanity"]),
    "space": ("sci-fi", ["wonder", "isolation", "frontier"]),
    "astronaut": ("sci-fi", ["isolation", "discovery", "courage"]),
    "ocean": ("adventure", ["mystery", "exploration"]),
    "forest": ("fantasy", ["mystery", "nature"]),
    "dragon": ("fantasy", ["bravery", "destiny"]),
    "robot": ("sci-fi", ["identity", "humanity"]),
    "ghost": ("horror", ["fear", "memory"]),
    "love": ("romance", ["connection", "longing"]),
    "war": ("drama", ["sacrifice", "loss"]),
    "detective": ("mystery", ["truth", "justice"]),
}


def _detect_genre(prompt: str) -> tuple[str, List[str]]:
    p = prompt.lower()
    for k, (g, themes) in _GENRE_HINTS.items():
        if k in p:
            return g, themes
    return "drama", ["change", "discovery"]


def _seeded_rng(prompt: str) -> random.Random:
    seed = int(hashlib.md5(prompt.encode()).hexdigest()[:8], 16)
    return random.Random(seed)


def _title_from_prompt(prompt: str) -> str:
    words = [w for w in re.findall(r"\w+", prompt) if len(w) > 3][:5]
    if not words:
        return "An Untitled Short"
    return " ".join(w.capitalize() for w in words)


def template_script(project_id: str, prompt: str,
                    target_duration_s: int = 45,
                    scene_count: int = 4) -> ScriptOutput:
    genre, themes = _detect_genre(prompt)
    rng = _seeded_rng(prompt)
    title = _title_from_prompt(prompt)
    logline = (f"In a {genre} world, {prompt.strip().rstrip('.')}, "
               f"unfolding in {scene_count} short acts.")

    characters = CharacterRoster(characters=[
        Character(
            id="char_narrator",
            name="Narrator",
            role="narrator",
            description="A calm, knowing observer who frames the journey.",
            visual_description="silhouette of an older figure, soft side-light, simple cloak",
            voice_style="warm, measured",
            voice_gender="neutral",
            voice_age="adult",
        ),
        Character(
            id="char_protagonist",
            name="Aria",
            role="protagonist",
            description="Curious and resilient, drawn forward by an inner question.",
            visual_description="young adventurer, weathered jacket, focused expression, "
                               "short brown hair, hazel eyes",
            voice_style="determined, hopeful",
            voice_gender="female",
            voice_age="young",
        ),
        Character(
            id="char_supporting",
            name="Kai",
            role="supporting",
            description="Pragmatic friend whose doubts give voice to the audience.",
            visual_description="lean figure, round glasses, dark coat, kind eyes",
            voice_style="thoughtful, slightly skeptical",
            voice_gender="male",
            voice_age="young",
        ),
    ])

    base_arc = [
        ("Opening",       "neutral",   "ambient",    "wide establishing shot, soft natural light",  "fade"),
        ("Inciting",      "curious",   "mysterious", "medium shot, cool palette, gentle motion",     "fade"),
        ("Discovery",     "wonder",    "ethereal",   "low-angle reveal, beams of light, awe",        "fade"),
        ("Complication",  "uneasy",    "tense",      "handheld, dutch angle, off-balance framing",   "cut"),
        ("Climax",        "tense",     "epic",       "close-up, dramatic backlight, high contrast",  "cut"),
        ("Aftermath",     "somber",    "melancholy", "wide static, faded colour, quiet stillness",   "fade"),
        ("Resolution",    "reflective","ethereal",   "wide aerial pull-back, golden-hour lighting",  "fade"),
        ("Epilogue",      "hopeful",   "uplifting",  "slow dolly forward, warm rim light",           "fade"),
    ]
    # Choose scene_count beats from base_arc, always keeping Opening + Resolution.
    n = max(2, min(scene_count, len(base_arc)))
    if n == len(base_arc):
        arc = base_arc
    else:
        # Always include first (Opening) and last (Resolution); evenly sample in between.
        middle = base_arc[1:-1]
        if n - 2 > 0:
            step = max(1, len(middle) // (n - 2))
            picked = middle[::step][: n - 2]
            arc = [base_arc[0]] + picked + [base_arc[-1]]
        else:
            arc = [base_arc[0], base_arc[-1]]

    # ---- per-scene speech budget so the film lands near target_duration_s --
    # Each scene spends fixed time on its establishing pre-roll, gaps between
    # lines and a tail hold (see shared/timeline.py); the rest is speech.
    target_ms = target_duration_s * 1000
    per_scene_ms = target_ms / n
    per_scene_speech_ms = per_scene_ms - SCENE_PREROLL_MS - SCENE_TAIL_MS

    scenes: List[Scene] = []
    for i, (label, tone, mood, camera, transition) in enumerate(arc):
        scene_id = f"scene_{i+1}"
        beat = _scene_beat(label, prompt, rng)

        # Base dialogue — narrator opens, protagonist responds, supporting reacts —
        # then extra back-and-forth, keeping only lines that fit the budget.
        narrator = ("char_narrator",    beat["narration"], "reflective")
        aria     = ("char_protagonist", beat["aria"],      tone)
        kai      = ("char_supporting",  beat["kai"],       "concerned")
        # The narrator frames the first and last scenes; middle scenes open with
        # the characters, so short films (one line per scene) still let them speak.
        if i == 0 or i == n - 1:
            base = [narrator, aria, kai]
        elif i % 2 == 1:
            base = [aria, kai, narrator]
        else:
            base = [kai, aria, narrator]
        extras = [(c, t, e) for c, t, e, _ in _extra_exchanges(label, prompt, rng)]
        # Extras may repeat for very long targets.
        candidates = base + extras * 3

        line_specs: List[tuple] = []
        used_ms = 0
        for char_id, text, emo in candidates:
            est = estimate_line_ms(text)
            cost = est + (LINE_GAP_MS if line_specs else 0)
            # Take the line if the scene has none yet, or if it brings the
            # scene closer to its budget than stopping here would.
            if not line_specs or abs(used_ms + cost - per_scene_speech_ms) \
                    < abs(used_ms - per_scene_speech_ms):
                line_specs.append((char_id, text, emo, est))
                used_ms += cost
            if used_ms >= per_scene_speech_ms - MIN_LINE_MS / 2:
                break

        lines: List[DialogueLine] = []
        for j, (char_id, text, emo, dur_ms) in enumerate(line_specs):
            lines.append(DialogueLine(
                line_id=f"{scene_id}_l{j+1}",
                character_id=char_id,
                text=text,
                emotion=emo,
                duration_ms=dur_ms,
            ))

        scenes.append(Scene(
            scene_id=scene_id,
            index=i,
            title=f"{label}: {beat['title']}",
            setting=beat["setting"],
            tone=tone,
            visual_prompt=(
                f"{beat['visual']}, {camera}, cinematic, highly detailed, dramatic lighting"
            ),
            camera=camera,
            duration_ms=used_ms + SCENE_PREROLL_MS + SCENE_TAIL_MS,
            dialogue=lines,
            music_mood=mood,
            transition_in=transition,
        ))

    story = StoryOutput(
        project_id=project_id,
        title=title,
        logline=logline,
        synopsis=(f"An exploration inspired by '{prompt}'. "
                  f"Across {n} acts, our protagonist Aria — joined by Kai — "
                  "confronts the question implied by the prompt and emerges changed."),
        genre=genre,
        themes=themes,
        arc="three-act+epilogue",
        target_duration_s=target_duration_s,
    )
    return ScriptOutput(story=story, characters=characters, scenes=scenes)


def _extra_exchanges(label: str, prompt: str, rng: random.Random) -> list:
    """Bank of additional dialogue turns used to scale a scene to its budget.

    Returns a list of (character_id, text, emotion, duration_ms) tuples.
    Each entry is ~3-5s of speech.
    """
    p = prompt.strip().rstrip(".")
    common = [
        ("char_narrator",    "Time slowed, the way it always does in moments that matter.",                 "reflective",   4500),
        ("char_protagonist", "I've been waiting for something like this my whole life.",                    "wonder",       4000),
        ("char_supporting",  "I want to believe you. I just don't want to lose you to it.",                 "uncertain",    4500),
        ("char_narrator",    "There are choices that change us before we make them.",                       "thoughtful",   4500),
        ("char_protagonist", "Whatever this is, I'm not turning back. Not now.",                            "determined",   3800),
        ("char_supporting",  "Then promise me one thing — that you'll come back the same.",                 "pleading",     4500),
        ("char_narrator",    "And so they pressed on, into the heart of what they could not yet name.",     "ominous",      5000),
        ("char_protagonist", "Do you hear it too? The way the silence is shaped, like it's holding its breath.", "curious",  4500),
        ("char_supporting",  "I hear it. And I don't think we should pretend we don't.",                    "serious",      4500),
        ("char_narrator",    "Stories like this never start where you think they do.",                      "wise",         4000),
        ("char_protagonist", "Every step away from home feels heavier — and lighter — at the same time.",   "conflicted",   4800),
        ("char_supporting",  "We knew it would cost something. We just didn't know what.",                  "resigned",     4500),
    ]
    rng.shuffle(common)
    return common


def _scene_beat(label: str, prompt: str, rng: random.Random) -> dict:
    p = prompt.strip().rstrip(".")
    if label == "Opening":
        return {
            "title": "A world before the spark",
            "setting": "the place where the story begins, calm and ordinary",
            "visual": f"{p}, opening shot, peaceful, establishing the world",
            "narration": f"Every story begins in stillness. Here is where ours starts: {p}.",
            "aria": "Something is about to change. I can feel it.",
            "kai": "Nothing has happened yet. Maybe it never will.",
        }
    if label == "Inciting":
        return {
            "title": "The first sign",
            "setting": "the moment the ordinary breaks",
            "visual": f"{p}, mysterious detail revealed, hint of the unknown",
            "narration": "And then — quietly, almost gently — the world shifted.",
            "aria": "Did you see that? It wasn't there a second ago.",
            "kai": "We should turn back. We don't know what this is.",
        }
    if label == "Discovery":
        return {
            "title": "The thing itself",
            "setting": "the place where what was hidden is finally revealed",
            "visual": f"{p}, awe-struck reveal, beams of light, monumental scale",
            "narration": "And there it was — exactly as the old stories had said it would be.",
            "aria": "I can't believe what I'm looking at. It's actually real.",
            "kai": "I owe you an apology. I shouldn't have doubted.",
        }
    if label == "Complication":
        return {
            "title": "When the ground shifted",
            "setting": "the moment when nothing goes the way it was meant to",
            "visual": f"{p}, sudden disturbance, chaos breaking through, danger emerging",
            "narration": "But every gift comes with a price, and the price had begun to come due.",
            "aria": "Something is wrong. We need to move — now.",
            "kai": "I told you we shouldn't have come this deep.",
        }
    if label == "Climax":
        return {
            "title": "The choice",
            "setting": "at the edge of the unknown, no path back",
            "visual": f"{p}, climactic confrontation, dramatic lighting, peak tension",
            "narration": "There are moments that ask everything of us. This was one.",
            "aria": "If I don't try now, I never will. I have to know.",
            "kai": "Then I'm with you. Whatever this costs.",
        }
    if label == "Aftermath":
        return {
            "title": "What was left",
            "setting": "the silent place after the storm has passed",
            "visual": f"{p}, quiet aftermath, dust settling, soft scattered light",
            "narration": "When the noise faded, what remained was the truth they had been running from.",
            "aria": "I don't know who I am after this.",
            "kai": "Then we figure it out. Together. One step at a time.",
        }
    if label == "Epilogue":
        return {
            "title": "Where the road kept going",
            "setting": "a new horizon, a new beginning",
            "visual": f"{p}, sunrise on a new world, warm hopeful tones, journey continuing",
            "narration": "Every ending is also the start of a story someone else will tell.",
            "aria": "I think I'm finally ready for what comes next.",
            "kai": "Then let's go find it.",
        }
    return {
        "title": "What we became",
        "setting": "after, looking back at where we came from",
        "visual": f"{p}, peaceful aftermath, golden hour, hopeful resolution",
        "narration": "And so the story ends — not with an answer, but with a changed way of asking.",
        "aria": "I'll never see it the same way again.",
        "kai": "Neither will I.",
    }
