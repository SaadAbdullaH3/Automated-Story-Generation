"""Subtitle overlay — burn-in SRT subtitles via ffmpeg + multi-track soft subs."""
from __future__ import annotations
import subprocess
from pathlib import Path
from typing import List, Dict, Any

from mcp.base_tool import BaseTool, ToolResult
from shared.languages import iso639_2


def _ms_to_srt_ts(ms: int) -> str:
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, msec = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{msec:03d}"


class SubtitleTool(BaseTool):
    name = "video.subtitle"
    description = "Burn dialogue subtitles from a list of {start_ms,end_ms,text} dicts onto an MP4."
    category = "video"

    def run(self, in_path: str, out_path: str, lines: List[Dict[str, Any]],
            font_size: int = 20, **_) -> ToolResult:
        in_p = Path(in_path)
        out_p = Path(out_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        srt_p = out_p.with_suffix(".srt")
        srt_p.write_text(self._build_srt(lines), encoding="utf-8")
        # ffmpeg's subtitles filter wants forward slashes even on Windows.
        srt_arg = srt_p.resolve().as_posix().replace(":", "\\:")
        # BorderStyle=1 = outline only (no opaque box).
        # Alignment=2 = bottom-center (ASS standard).
        # MarginV=40 keeps lines off the very edge.
        # WrapStyle=2 = no automatic line breaks unless we add \N (forces single line then wraps to 2).
        vf = (f"subtitles=filename='{srt_arg}':"
              f"force_style='FontSize={font_size},"
              f"PrimaryColour=&Hffffff&,OutlineColour=&H000000&,BackColour=&H80000000&,"
              f"BorderStyle=1,Outline=2,Shadow=1,"
              f"Alignment=2,MarginV=40,MarginL=80,MarginR=80,"
              f"Bold=0'")
        cmd = ["ffmpeg", "-y", "-i", str(in_p), "-vf", vf,
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
               "-c:a", "copy", str(out_p)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            return ToolResult(success=False, error=proc.stderr[-2000:],
                              metadata={"cmd": " ".join(cmd)})
        return ToolResult(success=True, data=str(out_p),
                          metadata={"srt": str(srt_p), "line_count": len(lines)})

    @staticmethod
    def _build_srt(lines: List[Dict[str, Any]]) -> str:
        chunks = []
        for i, ln in enumerate(lines, start=1):
            chunks.append(
                f"{i}\n"
                f"{_ms_to_srt_ts(int(ln['start_ms']))} --> "
                f"{_ms_to_srt_ts(int(ln['end_ms']))}\n"
                f"{ln['text'].strip()}\n"
            )
        return "\n".join(chunks)


class MultiSubtitleTool(BaseTool):
    """Embed multiple SRT subtitle tracks into an MP4 as switchable soft subs.

    Most modern players (VLC, MPV, web HTML5 with `<track>` exposure, MX Player,
    Windows Movies & TV) expose these as a language menu — no re-encoding of
    the video stream needed; we stream-copy and just attach subtitle streams
    using the `mov_text` codec (the MP4-compatible subtitle codec).

    Input shape:
        tracks = {
          "English": [{"start_ms": ..., "end_ms": ..., "text": ...}, ...],
          "French":  [...],
          ...
        }
    """
    name = "video.multi_subtitle"
    description = "Embed multiple language SRT tracks into an MP4 as switchable soft subtitles."
    category = "video"

    def run(self, in_path: str, out_path: str,
            tracks: Dict[str, List[Dict[str, Any]]],
            default_language: str = "English", **_) -> ToolResult:
        in_p = Path(in_path)
        out_p = Path(out_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)

        if not tracks:
            return ToolResult(success=False, error="no subtitle tracks provided")

        # Write one .srt file per language alongside the output.
        srt_dir = out_p.parent / "subtitles"
        srt_dir.mkdir(parents=True, exist_ok=True)

        # Stable ordering: default language first, then alphabetical.
        ordered = sorted(tracks.keys(),
                         key=lambda k: (k.lower() != default_language.lower(), k.lower()))

        srt_paths: List[Path] = []
        for lang in ordered:
            lines = tracks[lang]
            if not lines:
                continue
            srt = srt_dir / f"{lang.lower()}.srt"
            srt.write_text(SubtitleTool._build_srt(lines), encoding="utf-8")
            srt_paths.append(srt)

        if not srt_paths:
            return ToolResult(success=False, error="all subtitle tracks were empty")

        cmd: List[str] = ["ffmpeg", "-y", "-i", str(in_p)]
        for srt in srt_paths:
            cmd += ["-i", str(srt)]
        # Map video + audio from input 0, then each subtitle file in order.
        cmd += ["-map", "0:v:0", "-map", "0:a:0?"]
        for idx in range(len(srt_paths)):
            cmd += ["-map", f"{idx + 1}:0"]
        # Stream-copy A/V (no re-encode), encode subs as mov_text (MP4-compatible).
        cmd += ["-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text"]

        # Per-stream metadata: language code + human-readable title (player menu).
        for sub_idx, lang in enumerate(ordered[:len(srt_paths)]):
            code = iso639_2(lang)
            cmd += [f"-metadata:s:s:{sub_idx}", f"language={code}"]
            cmd += [f"-metadata:s:s:{sub_idx}", f"title={lang}"]
            # Mark the default track as default + forced so players auto-select it.
            if lang.lower() == default_language.lower():
                cmd += [f"-disposition:s:{sub_idx}", "default"]

        cmd.append(str(out_p))
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            return ToolResult(success=False, error=proc.stderr[-2000:],
                              metadata={"cmd": " ".join(cmd)})
        return ToolResult(
            success=True, data=str(out_p),
            metadata={
                "languages": ordered[:len(srt_paths)],
                "srt_paths": [str(p) for p in srt_paths],
                "track_count": len(srt_paths),
                "default_language": default_language,
            },
        )
