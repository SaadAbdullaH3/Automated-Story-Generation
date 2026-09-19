"""The film's single timeline — shared by audio, video, and subtitles.

Phase 2 decides *when* every dialogue line plays; Phase 3 cuts pictures to
those same boundaries, and subtitles read the same numbers. Keeping one
timeline is what keeps voices, faces, and captions in sync.

Per scene:

    |-- pre-roll --|-- line 1 --|gap|-- line 2 --|gap| ... |-- line N --|-- tail --|
    (establishing shot)

Video converts *absolute* millisecond boundaries to frames by rounding each
boundary (never by rounding durations), so rounding error can't accumulate:
every cut lands within half a frame of its audio position.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

SCENE_PREROLL_MS = 1500   # establishing shot before a scene's first line
LINE_GAP_MS = 250         # breathing room between consecutive lines
SCENE_TAIL_MS = 600       # hold after a scene's last line
EMPTY_SCENE_MS = 4000     # length of a scene with no dialogue (if the script gives none)

# Crossfade lengths. Every clip except the last in a chain is rendered this
# much longer so the overlap doesn't shorten the film (see xfade_frames()).
SHOT_XFADE_MS = 200       # between shots inside a scene
SCENE_XFADE_MS = 400      # between scenes

# Speaking-rate estimate used for planning before TTS exists.
WORDS_PER_SECOND = 2.6
MIN_LINE_MS = 1200


@dataclass(frozen=True)
class LineSlot:
    scene_id: str
    line_id: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class SceneSlot:
    scene_id: str
    start_ms: int
    end_ms: int


def estimate_line_ms(text: str) -> int:
    """Rough spoken length of a line, used before real TTS durations exist."""
    words = max(1, len(text.split()))
    return max(MIN_LINE_MS, int(words / WORDS_PER_SECOND * 1000))


def scene_overhead_ms(line_count: int) -> int:
    """Non-speech time a scene adds around its lines."""
    if line_count == 0:
        return 0
    return SCENE_PREROLL_MS + SCENE_TAIL_MS + LINE_GAP_MS * (line_count - 1)


def build_timeline(
    scenes: Sequence[Tuple[str, Sequence[Tuple[str, int]], int]],
) -> Tuple[List[SceneSlot], List[LineSlot], int]:
    """Lay scenes and lines out on one absolute timeline.

    `scenes` is a list of (scene_id, [(line_id, duration_ms), ...], empty_scene_ms).
    `empty_scene_ms` is only used when a scene has no lines.
    Returns (scene_slots, line_slots, total_ms).
    """
    cursor = 0
    scene_slots: List[SceneSlot] = []
    line_slots: List[LineSlot] = []
    for scene_id, lines, empty_ms in scenes:
        start = cursor
        if not lines:
            cursor += max(1000, empty_ms or EMPTY_SCENE_MS)
        else:
            cursor += SCENE_PREROLL_MS
            for i, (line_id, dur) in enumerate(lines):
                if i > 0:
                    cursor += LINE_GAP_MS
                line_slots.append(LineSlot(scene_id, line_id, cursor, cursor + dur))
                cursor += dur
            cursor += SCENE_TAIL_MS
        scene_slots.append(SceneSlot(scene_id, start, cursor))
    return scene_slots, line_slots, cursor


def ms_to_frame(ms: float, fps: int) -> int:
    """Absolute time -> nearest frame index."""
    return int(round(ms * fps / 1000.0))


def span_frames(start_ms: float, end_ms: float, fps: int) -> int:
    """Frame count for [start, end) using absolute-boundary rounding."""
    return ms_to_frame(end_ms, fps) - ms_to_frame(start_ms, fps)


def xfade_frames(xfade_ms: int, fps: int) -> int:
    """Crossfade length in whole frames (at least one)."""
    return max(1, int(round(xfade_ms * fps / 1000.0)))


def split_span(start_ms: int, end_ms: int, max_ms: int, min_ms: int) -> List[Tuple[int, int]]:
    """Split [start, end) into near-equal pieces no longer than `max_ms`.

    Pieces never drop below `min_ms` (the span is left whole if it can't be
    split that way).
    """
    total = end_ms - start_ms
    if total <= max_ms:
        return [(start_ms, end_ms)]
    n = -(-total // max_ms)  # ceil
    while n > 1 and total / n < min_ms:
        n -= 1
    if n <= 1:
        return [(start_ms, end_ms)]
    bounds = [start_ms + round(total * k / n) for k in range(n + 1)]
    return [(bounds[k], bounds[k + 1]) for k in range(n)]


def scenes_by_id(slots: Sequence[SceneSlot]) -> Dict[str, SceneSlot]:
    return {s.scene_id: s for s in slots}
