"""Image-to-video: a still becomes a moving shot.

The order comes from the `video` role in config/providers.yaml, not from
whichever key happens to be set. Providers:

  gemini_veo   Veo 3.1 through the Gemini API. The best of these by some way,
               and the only one the project already holds a key for — but it
               is BILLED PER SECOND OF VIDEO and is not in the Gemini free
               tier, so it additionally requires VIDEO_BUDGET_OK=1. That flag
               exists so nobody turns on a per-second bill by pasting an API
               key they already had.
  fal          fal.ai, ~$1 of free trial credit; SVD / AnimateDiff.
  replicate    paid per second.
  huggingface  free tier, noticeably lower quality.

Returning success=False makes the caller fall back to the offline camera moves
in agents/video_agent/camera.py, which is the `ffmpeg` entry in the chain.
"""
from __future__ import annotations
import base64
import os
from pathlib import Path
from typing import Optional

from mcp.base_tool import BaseTool, ToolResult
from shared import providers
from shared.utils.logging import get_logger

log = get_logger("text_to_video")

# fal.ai image-to-video model IDs to try in order
_FAL_I2V_MODELS = [
    "fal-ai/stable-video-diffusion",
    "fal-ai/fast-animatediff/turbo",
]
# fal.ai text-to-video model IDs to try in order
_FAL_T2V_MODELS = [
    "fal-ai/fast-animatediff/text-to-video",
    "fal-ai/cogvideox-5b",
]


class TextToVideoTool(BaseTool):
    name = "vision.text_to_video"
    description = "Generate a real video clip from text + (optional) source image."
    category = "vision"

    def run(self, prompt: str, out_path: str,
            image_path: Optional[str] = None,
            duration_s: float = 4.0,
            width: int = 1024, height: int = 576,
            fps: int = 24, **_) -> ToolResult:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.suffix.lower() != ".mp4":
            out = out.with_suffix(".mp4")

        tried = []
        for spec in providers.chain("video"):
            if spec.provider == "ffmpeg":
                break           # the offline animator is the caller's job
            runner = getattr(self, f"_{spec.provider}", None)
            if runner is None:
                continue
            tried.append(spec.provider)
            try:
                runner(prompt=prompt, image_path=image_path, out=out,
                       duration_s=duration_s, width=width, height=height,
                       fps=fps, model=spec.model)
                return ToolResult(success=True, data=str(out),
                                  metadata={"provider": spec.provider,
                                            "model": spec.model,
                                            "duration_s": duration_s})
            except Exception as e:  # noqa: BLE001
                log.warning("%s image->video failed (%s) — trying the next provider",
                            spec.provider, e)

        return ToolResult(
            success=False,
            error=("no real-motion provider available"
                   + (f" (tried: {', '.join(tried)})" if tried else
                      " — see the `video` role in config/providers.yaml")))

    # ---- providers -------------------------------------------------------

    def _gemini_veo(self, *, prompt: str, image_path: Optional[str], out: Path,
                    duration_s: float, width: int, height: int, fps: int = 24,
                    model: Optional[str] = None) -> None:
        """Veo 3.1 through the Gemini API.

        BILLED PER SECOND. The provider entry in config/providers.yaml requires
        VIDEO_BUDGET_OK as well as the API key precisely so that holding a
        Gemini key for the free-tier text models cannot quietly start a
        per-second video bill.

        Veo renders 16:9 or 9:16 at its own resolution; the caller normalises
        the clip to the project's size afterwards.
        """
        import time

        from google import genai
        from google.genai import types

        if not os.getenv("VIDEO_BUDGET_OK"):
            raise RuntimeError(
                "Veo is billed per second — set VIDEO_BUDGET_OK=1 to allow it")

        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        config = types.GenerateVideosConfig(
            number_of_videos=1,
            duration_seconds=max(1, int(round(duration_s))),
            aspect_ratio="16:9" if width >= height else "9:16",
            # The pipeline lays its own dialogue and music over the shot.
            generate_audio=False,
        )
        kwargs = {"model": model or "veo-3.1-fast-generate-preview",
                  "prompt": prompt, "config": config}
        if image_path and Path(image_path).exists():
            # Animating the still we already generated keeps the character and
            # the composition the rest of the scene was built from.
            kwargs["image"] = types.Image.from_file(location=str(image_path))

        operation = client.models.generate_videos(**kwargs)
        log.info("veo: submitted, waiting for the render ...")
        deadline = time.time() + 600
        while not operation.done:
            if time.time() > deadline:
                raise TimeoutError("veo did not finish within 10 minutes")
            time.sleep(10)
            operation = client.operations.get(operation)

        if getattr(operation, "error", None):
            raise RuntimeError(f"veo failed: {operation.error}")
        videos = getattr(operation.response, "generated_videos", None) or []
        if not videos:
            raise RuntimeError("veo returned no video")
        client.files.download(file=videos[0].video)
        videos[0].video.save(str(out))

    def _fal(self, *, prompt: str, image_path: Optional[str], out: Path,
             duration_s: float, width: int, height: int, fps: int,
             model: Optional[str] = None) -> None:
        w, h = width, height
        """Use fal-client SDK with base64 data URLs (no CDN upload required)."""
        import fal_client
        import requests

        def _download(url: str) -> None:
            r = requests.get(url, timeout=300)
            r.raise_for_status()
            out.write_bytes(r.content)

        def _to_data_url(path: str) -> str:
            mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
            with open(path, "rb") as f:
                return f"data:{mime};base64,{base64.b64encode(f.read()).decode()}"

        def _extract_url(result: dict) -> Optional[str]:
            vid = result.get("video", {})
            return (vid.get("url") if isinstance(vid, dict)
                    else result.get("video_url") or result.get("url"))

        if image_path and Path(image_path).exists():
            img_data_url = _to_data_url(image_path)
            last_err: Exception | None = None
            for model_id in _FAL_I2V_MODELS:
                try:
                    log.info("fal: submitting i2v %s ...", model_id)
                    result = fal_client.subscribe(
                        model_id,
                        arguments={
                            "image_url": img_data_url,
                            "motion_bucket_id": 100,
                            "cond_aug": 0.02,
                            "fps": min(fps, 24),
                            "num_frames": int(duration_s * min(fps, 24)),
                        },
                        with_logs=False,
                    )
                    url = _extract_url(result)
                    if url:
                        _download(url)
                        log.info("fal: i2v done via %s", model_id)
                        return
                    raise RuntimeError(f"no video URL in response: {str(result)[:200]}")
                except Exception as e:  # noqa: BLE001
                    log.warning("fal %s i2v failed: %s", model_id, e)
                    last_err = e
            raise RuntimeError(f"all fal i2v models failed; last: {last_err}")
        else:
            last_err = None
            for model_id in _FAL_T2V_MODELS:
                try:
                    log.info("fal: submitting t2v %s ...", model_id)
                    result = fal_client.subscribe(
                        model_id,
                        arguments={
                            "prompt": prompt,
                            "video_size": {"width": w, "height": h},
                            "num_frames": int(duration_s * min(fps, 24)),
                            "fps": min(fps, 24),
                        },
                        with_logs=False,
                    )
                    url = _extract_url(result)
                    if url:
                        _download(url)
                        log.info("fal: t2v done via %s", model_id)
                        return
                    raise RuntimeError(f"no video URL in response: {str(result)[:200]}")
                except Exception as e:  # noqa: BLE001
                    log.warning("fal %s t2v failed: %s", model_id, e)
                    last_err = e
            raise RuntimeError(f"all fal t2v models failed; last: {last_err}")

    def _replicate(self, *, prompt: str, image_path: Optional[str], out: Path,
                   duration_s: float, width: int, height: int, fps: int = 24,
                   model: Optional[str] = None) -> None:
        w, h = width, height
        """Run text-to-video / image-to-video via the Replicate Python SDK."""
        import replicate
        import requests

        if image_path and Path(image_path).exists():
            log.info("replicate: submitting stability-ai/stable-video-diffusion ...")
            with open(image_path, "rb") as img_f:
                output = replicate.run(
                    "stability-ai/stable-video-diffusion",
                    input={
                        "input_image": img_f,
                        "video_length": "25_frames_with_svd_xt",
                        "fps_id": 6,
                        "motion_bucket_id": 127,
                        "cond_aug": 0.02,
                        "decoding_t": 7,
                    },
                )
        else:
            log.info("replicate: submitting lucataco/zeroscope-v2-xl (t2v) ...")
            output = replicate.run(
                "lucataco/zeroscope-v2-xl:9f747673945c62801b13b84701c783929c0ee784e4748ec062204894dda1a351",
                input={
                    "prompt": prompt,
                    "num_frames": max(16, int(duration_s * 8)),
                    "fps": 8,
                    "width": min(w, 1024),
                    "height": min(h, 576),
                    "num_inference_steps": 40,
                },
            )

        url = output if isinstance(output, str) else (
            output[0] if isinstance(output, list) else None
        )
        if not url:
            raise RuntimeError(f"no output URL from replicate: {output}")

        log.info("replicate: downloading video ...")
        r = requests.get(url, timeout=300)
        r.raise_for_status()
        out.write_bytes(r.content)

    def _huggingface(self, *, prompt: str, out: Path, duration_s: float,
                     width: int, height: int, image_path: Optional[str] = None,
                     fps: int = 24, model: Optional[str] = None) -> None:
        w, h = width, height   # noqa: F841 - the endpoint sizes its own output
        import requests
        token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_API_KEY")
        headers = {"Authorization": f"Bearer {token}"}
        url = "https://api-inference.huggingface.co/models/damo-vilab/text-to-video-ms-1.7b"
        r = requests.post(url, headers=headers,
                          json={"inputs": prompt}, timeout=180)
        r.raise_for_status()
        out.write_bytes(r.content)
