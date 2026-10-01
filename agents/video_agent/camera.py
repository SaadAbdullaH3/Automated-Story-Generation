"""Camera moves, and the grade that goes over them.

Pans used to be banned here. `zoompan` positions the crop window at integer
pixels, so a move of a fraction of a pixel per frame rounds inconsistently and
the picture shivers — measurably, not subjectively: a 1280x720 pan rendered the
old way wobbles with a standard deviation of 0.49 px and worst-case jumps of
0.63 px between frames, against a measurement floor of 0.000 on a locked-off
shot.

The fix is to do the move at several times the output size and scale down
afterwards, so a whole-pixel error upstream becomes a fraction of a pixel in
the delivered frame. Measured on the same shot:

    supersample   std dev   worst jump   render (3 s shot)
    1.6x (old)    0.494 px    0.631 px        1.1 s
    2x            0.361 px    0.583 px        1.5 s
    3x            0.312 px    0.397 px        2.8 s
    4x            0.242 px    0.365 px        5.1 s

3x is the default: everything is comfortably sub-pixel and the cost is about
1.7 s a shot. SUPERSAMPLE overrides it.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional

# How much bigger than the output the move is computed at.
DEFAULT_SUPERSAMPLE = 3.0
# ... but never beyond this many pixels per frame. 3x of 720p is 8.3 MP, which
# a laptop handles; 3x of 1080p would be 18.7 MP per frame in the filter graph
# and several of those at once is how ffmpeg gets killed mid-render.
MAX_SUPERSAMPLED_PIXELS = 8_500_000
# How far a pan travels, as a fraction of the frame. Beyond this the crop gets
# tight enough to show the image's own softness.
PAN_TRAVEL = 0.14
PUSH_RANGE = 0.16          # 1.00 -> 1.16 over the shot


def supersample() -> float:
    try:
        value = float(os.getenv("SUPERSAMPLE", "") or DEFAULT_SUPERSAMPLE)
    except ValueError:
        return DEFAULT_SUPERSAMPLE
    return min(4.0, max(1.0, value))


def supersample_for(width: int, height: int,
                    factor: Optional[float] = None) -> float:
    """The factor to actually use at this output size, within the pixel cap."""
    wanted = factor if factor is not None else supersample()
    pixels = max(1, width * height)
    allowed = (MAX_SUPERSAMPLED_PIXELS / pixels) ** 0.5
    return max(1.0, min(wanted, allowed))


@dataclass(frozen=True)
class Move:
    name: str
    description: str


MOVES: Dict[str, Move] = {
    "push_in": Move("push_in", "slow push towards the subject — builds pressure"),
    "pull_out": Move("pull_out", "pull back to reveal where we are"),
    "pan_left": Move("pan_left", "drift left across the frame"),
    "pan_right": Move("pan_right", "drift right across the frame"),
    "tilt_up": Move("tilt_up", "rise — scale, hope, the sky"),
    "tilt_down": Move("tilt_down", "settle downwards — weight, defeat"),
    "drift": Move("drift", "slow diagonal float, barely noticeable"),
    "static_hold": Move("static_hold", "locked off — lets a performance land"),
}

# Which move suits which moment. These keys are the project's tone vocabulary
# (agents/story_agent/visual_style.TONE_HINTS); a test keeps the two in step,
# because a tone with no entry here silently falls back to a generic move.
TONE_MOVES: Dict[str, List[str]] = {
    "tense": ["push_in", "push_in", "static_hold"],
    "uneasy": ["push_in", "drift", "tilt_down"],
    "somber": ["pull_out", "tilt_down", "static_hold"],
    "melancholic": ["pull_out", "drift", "tilt_down"],
    "hopeful": ["tilt_up", "pull_out", "drift"],
    "joyful": ["pan_right", "tilt_up", "pull_out"],
    "wonder": ["pull_out", "tilt_up", "drift"],
    "curious": ["pan_left", "drift", "push_in"],
    "neutral": ["drift", "push_in", "static_hold"],
}

# A writing model will not restrict itself to that list, so near-misses map on
# rather than falling through to the generic default.
TONE_SYNONYMS: Dict[str, str] = {
    "anxious": "uneasy", "fearful": "uneasy", "afraid": "uneasy",
    "nervous": "uneasy", "ominous": "uneasy", "mysterious": "curious",
    "suspenseful": "tense", "urgent": "tense", "angry": "tense",
    "action": "tense", "dramatic": "tense",
    "sad": "melancholic", "grief": "melancholic", "wistful": "melancholic",
    "lonely": "melancholic", "bittersweet": "melancholic",
    "grim": "somber", "bleak": "somber", "solemn": "somber",
    "happy": "joyful", "triumphant": "joyful", "playful": "joyful",
    "warm": "joyful", "celebratory": "joyful",
    "awe": "wonder", "majestic": "wonder", "epic": "wonder",
    "calm": "neutral", "peaceful": "neutral", "quiet": "neutral",
    "reflective": "melancholic", "inquisitive": "curious",
}

ESTABLISHING_MOVES = ["pull_out", "pan_right", "drift", "tilt_up"]
# A face delivering a line: let the performance carry it.
DIALOGUE_MOVES = ["static_hold", "push_in", "static_hold"]


def canonical_tone(tone: str) -> str:
    """Map whatever the writer called it onto a tone with camera moves."""
    key = (tone or "").strip().lower()
    if key in TONE_MOVES:
        return key
    return TONE_SYNONYMS.get(key, "")


def move_for(shot_kind: str, tone: str = "", index: int = 0) -> str:
    """Pick a camera move that suits the shot and the moment.

    Round-robin over a fixed list is what this replaced: the move now comes
    from what the shot is doing, so a tense close-up pushes in and an
    establishing wide pulls back.
    """
    kind = (shot_kind or "").lower()
    if kind in ("lip_sync", "character", "dialogue"):
        options = DIALOGUE_MOVES
    elif kind in ("establishing", "wide"):
        options = ESTABLISHING_MOVES
    else:
        options = TONE_MOVES.get(canonical_tone(tone), [])
        if not options:
            options = ["drift", "push_in", "static_hold", "pan_right"]
    return options[index % len(options)]


def motion_filter(move: str, frames: int, width: int, height: int,
                  fps: int = 24, factor: Optional[float] = None) -> str:
    """The ffmpeg filter chain for one camera move, supersampled.

    Returns the whole chain: scale up, move, scale back down.
    """
    f = max(1, frames)
    factor = supersample_for(width, height, factor)
    # Even dimensions: libx264 refuses an odd one.
    sw, sh = int(width * factor) // 2 * 2, int(height * factor) // 2 * 2

    # Everything is cropped out of a frame this much larger than the window,
    # which is what leaves room to travel.
    zoom = 1.0 / (1.0 - PAN_TRAVEL)
    held = f"{zoom:.5f}"
    centre_x, centre_y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"

    # `on` is the output frame index, so these are linear over the shot.
    sweep = f"(iw-iw/zoom)*on/{max(1, f - 1)}"
    sweep_back = f"(iw-iw/zoom)*(1-on/{max(1, f - 1)})"
    rise = f"(ih-ih/zoom)*(1-on/{max(1, f - 1)})"
    fall = f"(ih-ih/zoom)*on/{max(1, f - 1)}"

    if move == "push_in":
        z = f"min(zoom+{PUSH_RANGE / f:.6f},{1.0 + PUSH_RANGE:.3f})"
        inner = f"zoompan=z='{z}':x='{centre_x}':y='{centre_y}'"
    elif move == "pull_out":
        z = (f"if(eq(on,0),{1.0 + PUSH_RANGE:.3f},"
             f"max(zoom-{PUSH_RANGE / f:.6f},1.0))")
        inner = f"zoompan=z='{z}':x='{centre_x}':y='{centre_y}'"
    elif move == "pan_right":
        inner = f"zoompan=z={held}:x='{sweep}':y='{centre_y}'"
    elif move == "pan_left":
        inner = f"zoompan=z={held}:x='{sweep_back}':y='{centre_y}'"
    elif move == "tilt_up":
        inner = f"zoompan=z={held}:x='{centre_x}':y='{rise}'"
    elif move == "tilt_down":
        inner = f"zoompan=z={held}:x='{centre_x}':y='{fall}'"
    elif move == "drift":
        # Diagonal, and only across a third of the available travel, so it
        # reads as a float rather than a move.
        inner = (f"zoompan=z={held}:x='(iw-iw/zoom)*(0.33+0.34*on/{max(1, f - 1)})':"
                 f"y='(ih-ih/zoom)*(0.33+0.34*on/{max(1, f - 1)})'")
    else:   # static_hold and anything unrecognised
        inner = f"zoompan=z={held}:x='{centre_x}':y='{centre_y}'"

    return (
        f"scale=w={sw}:h={sh}:force_original_aspect_ratio=increase,crop={sw}:{sh},"
        f"{inner}:d={f}:s={sw}x{sh}:fps={fps},"
        # Lanczos averages the upstream whole-pixel step down to a fraction
        # of an output pixel — this is the line that makes pans usable.
        f"scale={width}:{height}:flags=lanczos"
    )


# ---- grading -----------------------------------------------------------------

@dataclass(frozen=True)
class Grade:
    name: str
    filters: str


# Looks, keyed by what the story said it was. `visual_style` from phase 1 is
# matched against these, so the grade follows the film rather than a default.
GRADES: Dict[str, Grade] = {
    "teal_orange": Grade(
        "teal_orange",
        "colorbalance=rs=-0.06:bs=0.10:rm=0.04:bm=-0.02:rh=0.08:bh=-0.06,"
        "eq=contrast=1.10:saturation=1.10"),
    "cold": Grade(
        "cold",
        "colorbalance=rs=-0.08:bs=0.12:rm=-0.04:bm=0.06,"
        "eq=contrast=1.08:saturation=0.95"),
    "warm": Grade(
        "warm",
        "colorbalance=rs=0.08:bs=-0.06:rm=0.05:bm=-0.04,"
        "eq=contrast=1.05:saturation=1.12"),
    "noir": Grade(
        "noir",
        "eq=contrast=1.28:saturation=0.18:brightness=-0.02,"
        "curves=all='0/0 0.3/0.22 0.7/0.8 1/1'"),
    "bleach": Grade(
        "bleach",
        "eq=contrast=1.22:saturation=0.55,curves=all='0/0.03 0.5/0.52 1/0.97'"),
    "neutral": Grade("neutral", "eq=contrast=1.04:saturation=1.06"),
}

# Words in the story's own visual_style that imply a look.
_STYLE_WORDS = [
    (("noir", "monochrome", "black-and-white", "black and white"), "noir"),
    (("teal", "orange", "blockbuster", "anamorphic"), "teal_orange"),
    (("cold", "blue", "icy", "clinical", "sterile", "winter"), "cold"),
    (("warm", "golden", "amber", "sunlit", "nostalgic", "sepia"), "warm"),
    (("gritty", "bleak", "desaturated", "washed", "documentary"), "bleach"),
]
_TONE_GRADES = {
    "tense": "cold", "uneasy": "cold", "curious": "cold",
    "somber": "bleach", "melancholic": "bleach",
    "hopeful": "warm", "joyful": "warm", "wonder": "teal_orange",
}


def grade_for(visual_style: str = "", tone: str = "") -> Grade:
    """The colour treatment for a shot: the story's own look first, then tone."""
    text = (visual_style or "").lower()
    for words, name in _STYLE_WORDS:
        if any(word in text for word in words):
            return GRADES[name]
    return GRADES.get(_TONE_GRADES.get(canonical_tone(tone), ""), GRADES["neutral"])


def post_chain(visual_style: str = "", tone: str = "", add_grain: bool = True,
               add_vignette: bool = True, letterbox: bool = False,
               width: int = 0, height: int = 0) -> str:
    """Vignette, grain, grade — the pass that makes stills look photographed."""
    parts: List[str] = []
    if add_vignette:
        parts.append("vignette=PI/5")
    if add_grain:
        # Spatial only. Temporal noise regenerates every frame and the eye
        # reads that as the whole picture shimmering.
        parts.append("noise=alls=2:allf=u")
    parts.append(grade_for(visual_style, tone).filters)
    if letterbox and width and height:
        bar = int(height * 0.055) // 2 * 2       # even, or libx264 complains
        if bar > 0:
            parts.append(f"drawbox=x=0:y=0:w={width}:h={bar}:color=black:t=fill")
            parts.append(f"drawbox=x=0:y={height - bar}:w={width}:h={bar}:"
                         f"color=black:t=fill")
    parts.append("format=yuv420p")
    return ",".join(parts)
