"""Low-level ffmpeg helpers exposed as MCP tools."""
from __future__ import annotations
import subprocess
from pathlib import Path
from typing import List, Optional

from mcp.base_tool import BaseTool, ToolResult
from shared.utils.images import ensure_readable_image

# A looped still is an endless input; see shared/utils/images.py.
CLIP_TIMEOUT_S = 900


class FfmpegTool(BaseTool):
    name = "video.ffmpeg"
    description = "Run an arbitrary ffmpeg command. Pass `args` (list)."
    category = "video"

    def run(self, args: List[str], **_) -> ToolResult:
        cmd = ["ffmpeg", "-y"] + list(args)
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            return ToolResult(success=False,
                              error=proc.stderr[-2000:],
                              metadata={"cmd": " ".join(cmd)})
        return ToolResult(success=True, data=proc.stdout,
                          metadata={"cmd": " ".join(cmd)})


class ImageToClipTool(BaseTool):
    name = "video.image_to_clip"
    description = (
        "Convert a still image into a video clip with optional Ken Burns motion."
    )
    category = "video"

    def run(self, image_path: str, out_path: str, duration_ms: int,
            width: int = 1280, height: int = 720, fps: int = 24,
            motion: str = "ken_burns", audio_path: Optional[str] = None,
            **_) -> ToolResult:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.suffix.lower() != ".mp4":
            out = out.with_suffix(".mp4")
        dur = max(0.5, duration_ms / 1000.0)
        frames = int(round(dur * fps))

        # Build a ken-burns / pan / zoom filter graph operating on the still.
        zoom = self._zoompan_for(motion, frames, width, height)

        vf = (
            f"scale=w={width*2}:h={height*2}:force_original_aspect_ratio=increase,"
            f"crop={width*2}:{height*2},"
            f"{zoom},"
            f"format=yuv420p,fps={fps}"
        )

        ensure_readable_image(image_path)
        cmd = ["ffmpeg", "-y", "-loop", "1", "-i", str(image_path)]
        if audio_path and Path(audio_path).exists():
            cmd += ["-i", str(audio_path)]
        cmd += [
            "-vf", vf,
            "-t", f"{dur:.2f}",
            "-r", str(fps),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p",
        ]
        if audio_path and Path(audio_path).exists():
            cmd += ["-c:a", "aac", "-b:a", "128k", "-shortest"]
        else:
            cmd += ["-an"]
        cmd.append(str(out))

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=CLIP_TIMEOUT_S)
        if proc.returncode != 0:
            return ToolResult(success=False,
                              error=proc.stderr[-2000:],
                              metadata={"cmd": " ".join(cmd)})
        return ToolResult(success=True, data=str(out),
                          metadata={"motion": motion, "duration_ms": duration_ms})

    @staticmethod
    def _zoompan_for(motion: str, frames: int, w: int, h: int) -> str:
        f = max(1, frames)
        if motion == "zoom_in":
            return (f"zoompan=z='min(zoom+0.0015,1.5)':d={f}:"
                    f"s={w}x{h}:fps={f/(f/24)}")
        if motion == "zoom_out":
            return (f"zoompan=z='if(eq(on,0),1.5,max(zoom-0.0015,1.0))':d={f}:"
                    f"s={w}x{h}")
        if motion == "pan_left":
            return (f"zoompan=z=1.2:x='if(eq(on,0),iw-iw/zoom,x-2)':y='ih/2-(ih/zoom/2)':"
                    f"d={f}:s={w}x{h}")
        if motion == "pan_right":
            return (f"zoompan=z=1.2:x='if(eq(on,0),0,x+2)':y='ih/2-(ih/zoom/2)':"
                    f"d={f}:s={w}x{h}")
        if motion == "none":
            return f"scale={w}:{h}"
        # ken_burns default
        return (f"zoompan=z='min(zoom+0.0008,1.25)':d={f}:"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}")
