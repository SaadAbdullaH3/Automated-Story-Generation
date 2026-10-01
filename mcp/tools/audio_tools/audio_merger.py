"""Audio merging — build a dialogue track and overlay BGM via ffmpeg.

Two ways to build the dialogue track:
- `placements` (preferred): each clip is placed at its absolute `start_ms` on
  the film timeline, padded/trimmed to `total_ms`. This is what keeps audio in
  sync with video (see shared/timeline.py).
- `segments`: plain back-to-back concatenation (e.g. for joining BGM beds).
"""
from __future__ import annotations
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from mcp.base_tool import BaseTool, ToolResult
from shared.utils.logging import get_logger

log = get_logger("audio_merger")

SAMPLE_RATE = 22050


class AudioMergerTool(BaseTool):
    name = "audio.merge"
    description = (
        "Place dialogue clips on a timeline (or concatenate clips) into a master "
        "track and optionally mix in a BGM bed."
    )
    category = "audio"

    def run(self, out_path: str, segments: Optional[List[str]] = None,
            bgm: Optional[str] = None, bgm_volume: float = 0.25,
            placements: Optional[List[Dict]] = None,
            total_ms: Optional[int] = None, duck: bool = True,
            loudness: bool = True, **_) -> ToolResult:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.suffix.lower() not in (".wav", ".mp3", ".m4a"):
            out = out.with_suffix(".wav")

        dialogue_path = out.parent / f"{out.stem}_dialogue.wav"
        if placements is not None:
            if total_ms is None:
                return ToolResult(success=False, error="placements require total_ms")
            placed = [p for p in placements if Path(p["path"]).exists()]
            missing = len(placements) - len(placed)
            if missing:
                log.warning("%d placement clip(s) missing on disk — left silent", missing)
            self._place(placed, total_ms, dialogue_path)
            count = len(placed)
        else:
            segs = [s for s in (segments or []) if Path(s).exists()]
            if not segs:
                return ToolResult(success=False, error="no input segments exist")
            self._concat(segs, dialogue_path)
            count = len(segs)

        if bgm and Path(bgm).exists():
            self._mix(dialogue_path, Path(bgm), out, bgm_volume=bgm_volume,
                      duck=duck, loudness=loudness)
        elif loudness and placements is not None:
            self._normalize_only(dialogue_path, out)
        else:
            # Re-encode straight to the target format.
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(dialogue_path), str(out)],
                check=True, capture_output=True,
            )

        try:
            dialogue_path.unlink()
        except Exception:  # noqa: BLE001
            pass

        return ToolResult(success=True, data=str(out),
                          metadata={"segment_count": count, "bgm": bool(bgm),
                                    "ducked": bool(bgm) and duck, "loudness": loudness,
                                    "mode": "placements" if placements is not None else "concat"})

    # ---- helpers ---------------------------------------------------------

    def _place(self, placements: List[Dict], total_ms: int, out: Path) -> None:
        """Mix clips onto a silent bed of `total_ms`, each at its `start_ms`."""
        total_s = max(0.05, total_ms / 1000.0)
        if not placements:
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi",
                 "-i", f"anullsrc=channel_layout=mono:sample_rate={SAMPLE_RATE}",
                 "-t", f"{total_s:.3f}", "-c:a", "pcm_s16le", str(out)],
                check=True, capture_output=True,
            )
            return
        cmd = ["ffmpeg", "-y"]
        for p in placements:
            cmd += ["-i", str(p["path"])]
        parts = []
        for i, p in enumerate(placements):
            start = max(0, int(p["start_ms"]))
            parts.append(
                f"[{i}:a]aformat=sample_rates={SAMPLE_RATE}:channel_layouts=mono,"
                f"adelay=delays={start}:all=1[d{i}]"
            )
        labels = "".join(f"[d{i}]" for i in range(len(placements)))
        parts.append(
            f"{labels}amix=inputs={len(placements)}:normalize=0:duration=longest,"
            f"apad=whole_dur={total_s:.3f},atrim=end={total_s:.3f}[out]"
        )
        cmd += ["-filter_complex", ";".join(parts), "-map", "[out]",
                "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(out)]
        subprocess.run(cmd, check=True, capture_output=True)

    def _concat(self, segs: List[str], out: Path) -> None:
        list_path = out.parent / f"{out.stem}_concat.txt"
        list_path.write_text(
            "\n".join(f"file '{Path(s).resolve().as_posix()}'" for s in segs),
            encoding="utf-8",
        )
        # Decode to PCM WAV first so the concat demuxer never trips on mixed codecs/headers.
        norm_dir = out.parent / "_norm"
        norm_dir.mkdir(exist_ok=True)
        norm_paths: List[Path] = []
        for i, s in enumerate(segs):
            norm = norm_dir / f"seg_{i:03d}.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-i", s,
                 "-ar", "22050", "-ac", "1", "-c:a", "pcm_s16le",
                 str(norm)],
                check=True, capture_output=True,
            )
            norm_paths.append(norm)

        list_path.write_text(
            "\n".join(f"file '{p.resolve().as_posix()}'" for p in norm_paths),
            encoding="utf-8",
        )
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", str(list_path), "-c", "copy", str(out)],
            check=True, capture_output=True,
        )

        # cleanup
        try:
            for p in norm_paths:
                p.unlink(missing_ok=True)
            norm_dir.rmdir()
            list_path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass

    def _mix(self, dialogue: Path, bgm: Path, out: Path, bgm_volume: float = 0.25,
             duck: bool = True, loudness: bool = True) -> None:
        """Mix music under dialogue.

        With `duck`, the music is side-chained to the dialogue: it drops while
        someone speaks and returns in the gaps, so the bed can sit louder
        without ever masking a line. `loudness` applies EBU R128 normalisation
        (-16 LUFS, the usual streaming/podcast target) so films don't come out
        at wildly different volumes.
        """
        parts = [f"[1:a]volume={bgm_volume},aloop=loop=-1:size=2e9,"
                 f"aformat=sample_rates={SAMPLE_RATE}:channel_layouts=mono[bgm]"]
        if duck:
            # One copy of the dialogue drives the compressor, the other is heard.
            parts.append("[0:a]asplit=2[dry][key]")
            parts.append("[bgm][key]sidechaincompress=threshold=0.03:ratio=12:"
                         "attack=25:release=450:makeup=1[ducked]")
            parts.append("[dry][ducked]amix=inputs=2:duration=first:"
                         "dropout_transition=2:normalize=0[mixed]")
        else:
            # normalize=0 keeps dialogue at full level instead of halving it.
            parts.append("[0:a][bgm]amix=inputs=2:duration=first:"
                         "dropout_transition=2:normalize=0[mixed]")
        parts.append(f"[mixed]{'loudnorm=I=-16:TP=-1.5:LRA=11' if loudness else 'anull'}[a]")

        cmd = ["ffmpeg", "-y", "-i", str(dialogue), "-i", str(bgm),
               "-filter_complex", ";".join(parts), "-map", "[a]",
               "-ar", str(SAMPLE_RATE), "-ac", "2", str(out)]
        subprocess.run(cmd, check=True, capture_output=True)

    @staticmethod
    def _normalize_only(src: Path, out: Path) -> None:
        """No music: still normalise the dialogue to the same target loudness."""
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
             "-ar", str(SAMPLE_RATE), str(out)],
            check=True, capture_output=True,
        )
