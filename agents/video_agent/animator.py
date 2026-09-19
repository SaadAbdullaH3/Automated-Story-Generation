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
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


# Distinct motion patterns that look good on a still image.
# IMPORTANT: pan presets (`pan_left_subtle`, `pan_right_subtle`) were removed
# because ffmpeg's `zoompan` filter operates on integer pixel coordinates —
# fractional motion like `x+0.6` per frame rounds inconsistently, producing
# visible micro-shake especially on detail-rich landscape shots. We keep only
# centered zooms + the locked `static_hold`, which are pixel-stable.
MOTION_PRESETS = [
    "slow_zoom_in",
    "slow_zoom_out",
    "static_hold",
    "very_slow_zoom_in",
    "static_hold",         # weighted: half of all shots are locked
    "slow_zoom_in",
    "very_slow_zoom_in",
]


@dataclass
class Shot:
    """A single sub-clip inside a scene."""
    image_path: str
    duration_ms: int
    motion: str = "ken_burns_diag"
    audio_path: Optional[str] = None       # if set, gets muxed in (used for lip sync)
    is_lip_sync: bool = False              # if True, image_path is already a video
    frames: int = 0                        # exact frame count; 0 = derive from duration_ms


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
                add_grain: bool = True, add_vignette: bool = True) -> Path:
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

    motion_filter = _motion_filter_for(shot.motion, frames, width, height, fps)

    # Cinematic post-chain: subtle vignette + (static) grain + colour grading.
    # IMPORTANT: we use SPATIAL grain only (no `t` flag) — temporal grain
    # generates fresh noise every frame, which the eye reads as visible
    # shimmer / vibration across the whole image. A single static noise
    # pattern still gives a film feel without the shake.
    post = []
    if add_vignette:
        post.append("vignette=PI/5")
    if add_grain:
        # `alls=2` (low strength) + `allf=u` (uniform spatial only, no `t`).
        post.append("noise=alls=2:allf=u")
    # Colour grading: very mild S-curve.
    post.append("eq=contrast=1.04:saturation=1.06")
    post.append("format=yuv420p")
    post_chain = "," + ",".join(post)

    vf = (
        f"scale=w={int(width*1.6)}:h={int(height*1.6)}:force_original_aspect_ratio=increase,"
        f"crop={int(width*1.6)}:{int(height*1.6)},"
        f"{motion_filter}"
        f"{post_chain}"
    )

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
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


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

def _motion_filter_for(motion: str, frames: int, w: int, h: int, fps: int = 24) -> str:
    """Return a `zoompan=...` filter string for the requested motion.

    `fps` is passed to zoompan so it emits frames at the project rate
    (its default is 25, which would then be resampled and judder).
    """
    f = max(1, frames)
    tail = f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps}"
    # All motions are CENTERED zoom only — no pans, no diagonal drift.
    # We keep zoom rates very low (≤ 0.0005 per frame) so any residual
    # pixel-rounding is invisible to the eye.
    if motion == "very_slow_zoom_in":
        # Barely-perceptible zoom: 1.00 -> ~1.06 over the whole shot.
        return f"zoompan=z='min(zoom+0.0003,1.10)':d={f}:{tail}"
    if motion == "slow_zoom_in":
        return f"zoompan=z='min(zoom+0.0005,1.18)':d={f}:{tail}"
    if motion == "slow_zoom_out":
        return f"zoompan=z='if(eq(on,0),1.18,max(zoom-0.0005,1.0))':d={f}:{tail}"
    if motion in ("static_hold", "static_breathing",
                  "pan_left_subtle", "pan_right_subtle",
                  "ken_burns_diag", "ken_burns_diag_rev"):
        # Pan / ken-burns presets now collapse to a stable centered hold.
        return f"zoompan=z=1.10:d={f}:{tail}"
    # default — gentle centered zoom in.
    return f"zoompan=z='min(zoom+0.0003,1.10)':d={f}:{tail}"


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


def pick_motion_for_index(scene_idx: int, shot_idx: int) -> str:
    """Deterministic motion picker — alternates so adjacent shots feel different."""
    n = len(MOTION_PRESETS)
    return MOTION_PRESETS[(scene_idx * 3 + shot_idx) % n]
