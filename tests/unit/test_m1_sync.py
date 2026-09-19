"""M1 — audio, video and subtitles share one timeline and stay in sync."""
from __future__ import annotations
import json
import subprocess
from pathlib import Path

from agents.story_agent.planner import template_script
from shared.timeline import (
    LINE_GAP_MS, SCENE_PREROLL_MS, SCENE_TAIL_MS, build_timeline, ms_to_frame,
    span_frames, split_span,
)


def _probe(path, entries="format=duration"):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", entries,
                        "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def _duration_ms(path) -> float:
    return float(_probe(path)["format"]["duration"]) * 1000


def _video_frames(path) -> int:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                        "-show_entries", "stream=nb_read_frames", "-of", "json", str(path)],
                       capture_output=True, text=True, check=True)
    return int(json.loads(r.stdout)["streams"][0]["nb_read_frames"])


# ---- pure timeline math ----------------------------------------------------

def test_build_timeline_layout():
    scenes, lines, total = build_timeline([
        ("s1", [("a", 1000), ("b", 2000)], 0),
        ("s2", [], 3000),
        ("s3", [("c", 500)], 0),
    ])
    assert lines[0].start_ms == SCENE_PREROLL_MS
    assert lines[1].start_ms == SCENE_PREROLL_MS + 1000 + LINE_GAP_MS
    assert scenes[0].end_ms == lines[1].end_ms + SCENE_TAIL_MS
    assert scenes[1].end_ms - scenes[1].start_ms == 3000        # empty scene keeps its length
    assert scenes[2].start_ms == scenes[1].end_ms
    assert total == scenes[-1].end_ms


def test_absolute_rounding_never_drifts():
    # Boundaries that don't fall on frames: per-span rounding would accumulate
    # error, absolute rounding keeps the sum exact.
    bounds = [0] + [i * 1037 for i in range(1, 60)]
    total = sum(span_frames(a, b, 24) for a, b in zip(bounds, bounds[1:]))
    assert total == ms_to_frame(bounds[-1], 24)


def test_split_span_tiles_exactly():
    pieces = split_span(1000, 11_000, max_ms=4500, min_ms=1500)
    assert pieces[0][0] == 1000 and pieces[-1][1] == 11_000
    assert all(b == c for (_, b), (c, _) in zip(pieces, pieces[1:]))
    assert all(b - a <= 4500 for a, b in pieces)


# ---- duration target -------------------------------------------------------

def test_template_script_lands_near_target_duration():
    for dur, n in [(30, 4), (45, 4), (60, 4), (90, 6), (120, 8)]:
        s = template_script("p", "A lighthouse keeper befriends a stranded whale",
                            target_duration_s=dur, scene_count=n)
        planned = s.total_duration_ms() / 1000
        assert abs(planned - dur) / dur <= 0.15, (dur, n, planned)


# ---- rendered film -----------------------------------------------------------

def test_rendered_film_is_in_sync(small_project):
    state, _ = small_project(duration_s=30, scenes=3)
    manifest = state.audio.manifest
    video = state.video
    fps = video.fps

    # Master audio is exactly as long as the timeline.
    assert abs(_duration_ms(state.audio.master_track) - manifest.total_duration_ms) <= 50

    # Final video has exactly the timeline's frame count.
    assert _video_frames(video.final_video_path) == ms_to_frame(manifest.total_duration_ms, fps)

    # Shots tile the whole timeline with no gaps or overlaps...
    shots = [s for f in video.frames for s in f.shots]
    assert shots[0].start_ms == 0
    for a, b in zip(shots, shots[1:]):
        assert a.start_ms + a.duration_ms == b.start_ms
    assert shots[-1].start_ms + shots[-1].duration_ms == manifest.total_duration_ms

    # ...and every line starts exactly on a cut, where its speaker is on screen.
    starts = {s.start_ms: s for s in shots}
    narrator = {c.id for c in state.script.characters.characters if c.role == "narrator"}
    for seg in manifest.segments:
        assert seg.start_ms in starts, f"no cut at {seg.line_id}"
        shot = starts[seg.start_ms]
        if seg.character_id not in narrator:
            assert shot.character_id == seg.character_id

    # Subtitles use the same timeline.
    subs = Path(video.final_video_path).parent / "subtitles" / "english.srt"
    first = next(s for s in manifest.segments)
    ts = subs.read_text(encoding="utf-8").splitlines()[1].split(" --> ")[0]
    h, m, rest = ts.split(":")
    sec, ms = rest.split(",")
    assert (int(h) * 3600 + int(m) * 60 + int(sec)) * 1000 + int(ms) == first.start_ms


def test_recompose_reuses_unchanged_scenes(small_project):
    from agents.video_agent import VideoAgent
    state, _ = small_project(duration_s=24, scenes=3)
    before = {f.scene_id: Path(f.clip_path).stat().st_mtime_ns for f in state.video.frames}
    VideoAgent().compose(state)
    after = {f.scene_id: Path(f.clip_path).stat().st_mtime_ns for f in state.video.frames}
    assert before == after
