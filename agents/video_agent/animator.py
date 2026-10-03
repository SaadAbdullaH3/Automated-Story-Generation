"""Cinematic animator — turns stills into watchable video.

Two key upgrades over the basic ken-burns clip:

1. **Multi-shot scene composition.** Each scene gets one establishing shot
   image plus one portrait per character that speaks in it. Each dialogue
   line becomes its OWN sub-clip (shot of the speaker if it's a character,
   or wide shot for the narrator), and sub-clips are crossfaded together
   inside the scene. Result: a 4-scene project becomes ~10-15 cuts instead
   of 4 long static shots.

2. **Smoother motion.** We use a richer ffmpeg filter chain — high-resolution
   source crop, slow zoompan with eased curves, optional `minterpolate` for
   frame interpolation, subtle vignette + film-grain for cinematic feel.
"""
from __future__ import annotations
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from shared.utils.images import ensure_readable_image
from shared.utils.logging import get_logger

from . import camera

log = get_logger("animator")

# A looped still is an endless input to ffmpeg, so a shot gets a hard limit
# rather than trusting it to stop. Generous: a long 1080p shot takes ~1 min.
SHOT_TIMEOUT_S = 900

# Each shot at once holds a supersampled frame (up to 8.5 MP) in memory.
MAX_SHOT_WORKERS = 4


def shot_workers() -> int:
    """How many shots render at once: one per core this process may use.

    A shot keeps about one core busy — zoompan, which makes the camera move,
    runs on a single thread, and the encoder adds roughly a third of another.
    Rendered one at a time on the 2-core server, the CPU sat at ~130% of 200%
    for the three quarters of a render that is shots. SHOT_WORKERS overrides.
    """
    configured = os.getenv("SHOT_WORKERS", "").strip()
    if configured:
        try:
            return max(1, int(configured))
        except ValueError:
            log.warning("SHOT_WORKERS=%r is not a number — using the core count", configured)
    try:
        cores = len(os.sched_getaffinity(0))      # what a container is actually given
    except AttributeError:                         # not on Windows
        cores = os.cpu_count() or 1
    return max(1, min(cores, MAX_SHOT_WORKERS))


# Camera moves live in camera.py now. Pans were once removed from this list
# because zoompan's integer crop position made them shiver; camera.py computes
# the move at 3x the output size and scales down, which puts the wobble below
# a third of a pixel. The old names still resolve, so existing states render.
MOTION_PRESETS = list(camera.MOVES)

# What the old preset names mean in terms of camera moves.
LEGACY_MOTIONS = {
    "very_slow_zoom_in": "push_in",
    "slow_zoom_in": "push_in",
    "slow_zoom_out": "pull_out",
    "static_breathing": "static_hold",
    "pan_left_subtle": "pan_left",
    "pan_right_subtle": "pan_right",
    "ken_burns_diag": "drift",
    "ken_burns_diag_rev": "drift",
}


@dataclass
class Shot:
    """A single sub-clip inside a scene."""
    image_path: str
    duration_ms: int
    motion: str = "drift"
    audio_path: Optional[str] = None       # if set, gets muxed in (used for lip sync)
    is_lip_sync: bool = False              # if True, image_path is already a video
    frames: int = 0                        # exact frame count; 0 = derive from duration_ms
    # Phase 1 decided the film's look; the grade follows it rather than a default.
    visual_style: str = ""
    tone: str = ""


def probe_duration_ms(path: str | Path) -> Optional[int]:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, check=True,
        )
        return int(float(json.loads(r.stdout)["format"]["duration"]) * 1000)
    except Exception:  # noqa: BLE001
        return None


def render_shot(shot: Shot, out_path: Path, width: int, height: int, fps: int,
                add_grain: bool = True, add_vignette: bool = True,
                letterbox: bool = False) -> Path:
    """Render a single shot (still image -> mp4) with cinematic motion.

    The clip is exactly `shot.frames` frames long (or duration_ms at `fps`),
    so shots line up with the audio timeline without drift.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.suffix.lower() != ".mp4":
        out_path = out_path.with_suffix(".mp4")

    frames = shot.frames or max(1, int(round(shot.duration_ms * fps / 1000.0)))

    if shot.is_lip_sync and shot.image_path.lower().endswith((".mp4", ".mov", ".webm")):
        # Already a video — just normalize size/format/length.
        return _normalize_video(shot.image_path, out_path, width, height, fps,
                                frames=frames, audio_path=shot.audio_path,
                                add_grain=add_grain, add_vignette=add_vignette)

    move = LEGACY_MOTIONS.get(shot.motion, shot.motion)
    # camera.motion_filter is the whole chain: scale up, move, scale back down.
    vf = (
        camera.motion_filter(move, frames, width, height, fps)
        + ","
        + camera.post_chain(shot.visual_style, shot.tone, add_grain=add_grain,
                            add_vignette=add_vignette, letterbox=letterbox,
                            width=width, height=height)
    )

    ensure_readable_image(shot.image_path)
    has_audio = bool(shot.audio_path and Path(shot.audio_path).exists())
    cmd = ["ffmpeg", "-y", "-loop", "1", "-i", str(shot.image_path)]
    if has_audio:
        cmd += ["-i", str(shot.audio_path)]
    cmd += [
        "-vf", vf,
        "-frames:v", str(frames),
        "-r", str(fps),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-pix_fmt", "yuv420p",
    ]
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-shortest"]
    else:
        cmd += ["-an"]
    cmd.append(str(out_path))
    proc = _run_shot(cmd, check=False)
    if proc.returncode != 0:
        # Computing the move at several times the output size is what keeps a
        # pan smooth, but it is also the one memory-hungry part of a render.
        # Rather than lose the whole film, redo this shot at output size and
        # say so — a slightly steppier shot beats no shot.
        log.warning("shot %s failed at %.1fx supersampling (%s) — retrying at 1x",
                    out_path.stem, camera.supersample_for(width, height),
                    (proc.stderr or b"").decode("utf-8", "replace").strip()[-200:])
        vf_plain = (
            camera.motion_filter(move, frames, width, height, fps, factor=1.0)
            + "," + camera.post_chain(shot.visual_style, shot.tone,
                                      add_grain=add_grain, add_vignette=add_vignette,
                                      letterbox=letterbox, width=width, height=height)
        )
        cmd[cmd.index("-vf") + 1] = vf_plain
        _run_shot(cmd, check=True)
    return out_path


def _run_shot(cmd: List[str], check: bool) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, check=check, timeout=SHOT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"ffmpeg did not finish a shot in {SHOT_TIMEOUT_S}s "
                           f"({Path(cmd[-1]).name})") from None


def assemble_scene(shots: List[Path], out_path: Path,
                   crossfade_ms: float = 250,
                   audio_path: Optional[str] = None,
                   durations_s: Optional[List[float]] = None) -> Path:
    """Concatenate the sub-clips of a scene with crossfades and (optional) audio.

    Pass `durations_s` (the exact clip lengths) to place crossfades precisely;
    otherwise each clip is probed.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.suffix.lower() != ".mp4":
        out_path = out_path.with_suffix(".mp4")

    if len(shots) == 1 and not audio_path:
        # Single shot — already in the project format, copy it as-is.
        if Path(shots[0]).resolve() != out_path.resolve():
            shutil.copyfile(shots[0], out_path)
        return out_path

    if durations_s is None:
        durations_s = [(probe_duration_ms(s) or 1000) / 1000.0 for s in shots]
    xfade_s = max(0.04, crossfade_ms / 1000.0)

    inputs: List[str] = []
    for s in shots:
        inputs += ["-i", str(s)]
    if audio_path and Path(audio_path).exists():
        inputs += ["-i", str(audio_path)]

    if len(shots) == 1:
        fc = "[0:v]null[v0]"
        prev = "v0"
    else:
        filter_parts = []
        prev = "0:v"
        offset = 0.0
        for i in range(1, len(shots)):
            offset += durations_s[i - 1] - xfade_s
            out_label = f"v{i}"
            filter_parts.append(
                f"[{prev}][{i}:v]xfade=transition=fade:duration={xfade_s:.6f}:"
                f"offset={max(0.0, offset):.6f}[{out_label}]"
            )
            prev = out_label
        fc = ";".join(filter_parts)

    cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", fc,
           "-map", f"[{prev}]"]
    if audio_path and Path(audio_path).exists():
        cmd += ["-map", f"{len(shots)}:a:0", "-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-an"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-pix_fmt", "yuv420p", str(out_path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


# ---- internals --------------------------------------------------------------

def _normalize_video(in_path: str, out_path: Path, width: int, height: int,
                     fps: int, frames: int,
                     audio_path: Optional[str] = None,
                     add_grain: bool = False, add_vignette: bool = False) -> Path:
    """Re-encode an existing clip to the project's size / fps / exact length.

    Clips shorter than `frames` are extended by holding their last frame, so a
    provider clip (e.g. lip-sync) always fills its slot on the timeline.
    """
    vf_parts = []
    if width and height:
        vf_parts.append(
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}"
        )
    if fps:
        vf_parts.append(f"fps={fps}")
    if add_vignette:
        vf_parts.append("vignette=PI/5")
    if add_grain:
        vf_parts.append("noise=alls=2:allf=u")
    vf_parts.append(f"tpad=stop_mode=clone:stop_duration={frames / max(1, fps):.3f}")
    vf_parts.append("format=yuv420p")
    vf = ",".join(vf_parts)

    has_audio = bool(audio_path and Path(audio_path).exists())
    cmd = ["ffmpeg", "-y", "-i", str(in_path)]
    if has_audio:
        cmd += ["-i", str(audio_path)]
    cmd += ["-vf", vf, "-frames:v", str(frames),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-pix_fmt", "yuv420p"]
    if has_audio:
        cmd += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-an"]
    cmd.append(str(out_path))
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def _has_audio(path: str | Path) -> bool:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_type", "-of", "json", str(path)],
            capture_output=True, text=True, check=True,
        )
        return bool(json.loads(r.stdout).get("streams"))
    except Exception:  # noqa: BLE001
        return False


def pick_motion_for_index(scene_idx: int, shot_idx: int,
                          shot_kind: str = "", tone: str = "") -> str:
    """Pick the camera move for a shot.

    It used to be a round-robin over a fixed list, so the move had nothing to
    do with what was on screen. Now the shot's job and the scene's tone choose
    it, and the index only breaks ties between adjacent shots.
    """
    # With a mood, the scene opens on that mood's own move and varies from
    # there. Offsetting by the scene number as well picked an arbitrary entry
    # from the mood's list instead — in one real render, three scenes of three
    # different moods all opened on the same drift. Without a mood, the offset
    # is what keeps adjacent scenes from opening identically.
    offset = shot_idx if camera.canonical_tone(tone) else scene_idx + shot_idx
    return camera.move_for(shot_kind, tone, offset)
