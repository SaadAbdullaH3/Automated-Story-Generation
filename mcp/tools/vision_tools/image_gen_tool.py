"""Image generation, driven by the `image` role in config/providers.yaml.

Providers are tried in the order the config lists them (Cloudflare FLUX, local
Stable Diffusion, an SD WebUI, Pollinations, OpenAI, and finally an offline PIL
placeholder). Whatever a provider returns is validated, cover-fitted to the
requested size and saved in the requested format, so a provider error page can
never end up on screen as a picture.
"""
from __future__ import annotations
import base64
import hashlib
import os
import urllib.parse
from io import BytesIO
from pathlib import Path
from typing import Optional

from mcp.base_tool import BaseTool, ToolResult
from shared import providers
from shared.providers import ProviderSpec
from shared.utils.logging import get_logger

log = get_logger("image_gen")

# Module-level cache for the diffusers pipeline so we only load it once
# per process (the model is ~7 GB; loading it 18 times per pipeline run
# would be unworkable).
_DIFFUSERS_PIPE = None


class ImageGenTool(BaseTool):
    name = "vision.generate_image"
    description = "Generate an image from a text prompt using the configured image providers."
    category = "vision"

    def run(self, prompt: str, out_path: str, width: int = 1280, height: int = 720,
            seed: Optional[int] = None, style: str = "", negative_prompt: str = "",
            **_) -> ToolResult:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            out = out.with_suffix(".png")

        full_prompt = f"{prompt}, {style}".strip(", ") if style else prompt
        seed = seed or int(hashlib.md5(prompt.encode()).hexdigest()[:8], 16) % 2**31

        errors = []
        for spec in providers.chain("image"):
            handler = getattr(self, f"_provider_{spec.provider}", None)
            if handler is None:
                log.warning("unknown image provider '%s' in config — skipping", spec.provider)
                continue
            try:
                meta = handler(spec, full_prompt, negative_prompt, out, width, height, seed) or {}
                return ToolResult(success=True, data=str(out),
                                  metadata={"provider": spec.provider, "model": spec.model,
                                            "seed": seed, **meta})
            except Exception as e:  # noqa: BLE001
                errors.append(f"{spec.provider}: {e}")
                log.warning("image provider %s failed (%s) — trying next", spec.provider,
                            str(e)[:200])
        return ToolResult(success=False, error="; ".join(errors) or "no image provider configured")

    # ---- providers -------------------------------------------------------

    def _provider_local_sd(self, spec: ProviderSpec, prompt: str, negative: str, out: Path,
                           w: int, h: int, seed: int) -> dict:
        """Local Stable Diffusion via the diffusers library.

        Tries (in order) SDXL-Turbo with sequential offload, then SD 1.5 as
        a fallback for low-VRAM GPUs. Pipeline is cached in `_DIFFUSERS_PIPE`
        so the model only loads once per process.

        On a 6 GB GPU (e.g. RTX 3050 Laptop):
          - SD 1.5      -> ~3-5 s per image at 512x512
          - SDXL-Turbo  -> ~15-25 s per image at 512x512 (sequential offload)
        """
        global _DIFFUSERS_PIPE

        # Help PyTorch avoid VRAM fragmentation on tight GPUs.
        os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

        if _DIFFUSERS_PIPE is None:
            import torch
            from diffusers import AutoPipelineForText2Image
            model_id = os.getenv("LOCAL_SD_MODEL", spec.model or "stabilityai/sdxl-turbo")
            log.info("loading %s (first call may take 30-90s)...", model_id)

            try:
                pipe = AutoPipelineForText2Image.from_pretrained(
                    model_id,
                    torch_dtype=torch.float16,
                    variant="fp16",
                    use_safetensors=True,
                )
            except Exception as e:  # noqa: BLE001
                log.warning("could not load %s with fp16 variant (%s) — using default", model_id, e)
                pipe = AutoPipelineForText2Image.from_pretrained(
                    model_id, torch_dtype=torch.float16,
                )

            # Aggressive VRAM tweaks for 6 GB GPUs.
            pipe.enable_attention_slicing("max")
            pipe.enable_vae_slicing()
            pipe.enable_vae_tiling()
            try:
                # Sequential offload streams layers one at a time — slowest but
                # has the smallest peak VRAM footprint (works on 4 GB cards).
                pipe.enable_sequential_cpu_offload()
                log.info("  using sequential CPU offload (low-VRAM mode)")
            except Exception:  # noqa: BLE001
                try:
                    pipe.enable_model_cpu_offload()
                    log.info("  using model CPU offload")
                except Exception:  # noqa: BLE001
                    pipe = pipe.to("cuda")
                    log.info("  using full-GPU mode")

            _DIFFUSERS_PIPE = pipe
            log.info("%s ready", model_id)

        import torch
        # Turbo is trained at 512x512; render small and scale.
        target_w = min(w, 768) - (min(w, 768) % 8)
        target_h = min(h, 768) - (min(h, 768) % 8)

        gen = torch.Generator(device="cpu").manual_seed(int(seed))
        try:
            result = _DIFFUSERS_PIPE(
                prompt=prompt,
                negative_prompt=negative or None,
                num_inference_steps=int(os.getenv("LOCAL_SD_STEPS", spec.params.get("steps", 4))),
                guidance_scale=0.0,                 # Turbo is trained for CFG=0
                width=target_w, height=target_h,
                generator=gen,
            )
        except torch.cuda.OutOfMemoryError:
            torch.cuda.empty_cache()
            log.warning("CUDA OOM — retrying at 384x384")
            target_w = target_h = 384
            result = _DIFFUSERS_PIPE(
                prompt=prompt,
                negative_prompt=negative or None,
                num_inference_steps=int(os.getenv("LOCAL_SD_STEPS", spec.params.get("steps", 4))),
                guidance_scale=0.0,
                width=target_w, height=target_h,
                generator=gen,
            )
        finally:
            try:
                torch.cuda.empty_cache()
            except Exception:  # noqa: BLE001
                pass

        img = result.images[0]
        if (target_w, target_h) != (w, h):
            from PIL import Image
            img = img.resize((w, h), Image.LANCZOS)
        img.save(out)
        return {"model": model_id, "native_size": [target_w, target_h]}

    def _provider_pollinations(self, spec: ProviderSpec, prompt: str, negative: str,
                               out: Path, w: int, h: int, seed: int) -> dict:
        """Pollinations image generation. Returns metadata about what was served.

        With POLLINATIONS_API_KEY (free account at enter.pollinations.ai) this
        uses gen.pollinations.ai and POLLINATIONS_MODEL (default z-image-turbo).
        Without a key it falls back to the legacy keyless endpoint, which as of
        2026-09 ignores the requested model/size and serves `sana` at ~1024x576.
        """
        import requests
        encoded = urllib.parse.quote(prompt)
        key = os.getenv("POLLINATIONS_API_KEY")
        if key:
            model = os.getenv("POLLINATIONS_MODEL", spec.model or "tongyi-mai/z-image-turbo")
            url = (f"https://gen.pollinations.ai/image/{encoded}"
                   f"?model={urllib.parse.quote(model, safe='')}&width={w}&height={h}&seed={seed}")
            r = requests.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=120)
            endpoint = "gen"
        else:
            model = "flux"
            url = (f"https://image.pollinations.ai/prompt/{encoded}"
                   f"?width={w}&height={h}&seed={seed}&nologo=true&model={model}")
            r = requests.get(url, timeout=120)
            endpoint = "legacy"
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if not ctype.startswith("image/"):
            raise RuntimeError(f"expected an image, got {ctype or 'no content-type'}: "
                               f"{r.text[:200]!r}")
        served = _served_model(r.content)
        native = _save_image_bytes(r.content, out, w, h)
        if endpoint == "legacy" and served and served != model:
            _warn_once("legacy_pollinations",
                       "Pollinations legacy endpoint served model %r at %dx%d instead of %r "
                       "at %dx%d. Set POLLINATIONS_API_KEY (free at enter.pollinations.ai) "
                       "for the requested model.", served, native[0], native[1], model, w, h)
        return {"endpoint": endpoint, "requested_model": model,
                "served_model": served or model, "native_size": list(native)}

    def _provider_cloudflare(self, spec: ProviderSpec, prompt: str, negative: str, out: Path,
                             w: int, h: int, seed: int) -> dict:
        """Cloudflare Workers AI (free: 10,000 neurons/day, ~170 FLUX images).

        FLUX schnell takes no size parameters and returns a square image, which
        is cover-fitted to the project's aspect ratio.
        """
        import requests
        account = os.environ["CLOUDFLARE_ACCOUNT_ID"]
        model = spec.model or "@cf/black-forest-labs/flux-1-schnell"
        payload = {"prompt": prompt[:2048], "seed": seed, **spec.params}
        r = requests.post(
            f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}",
            headers={"Authorization": f"Bearer {os.environ['CLOUDFLARE_API_TOKEN']}",
                     "Content-Type": "application/json"},
            json=payload, timeout=180,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
        if r.headers.get("content-type", "").startswith("image/"):
            data = r.content                      # some models answer with raw bytes
        else:
            body = r.json()
            if not body.get("success", True):
                raise RuntimeError(f"cloudflare error: {body.get('errors')}")
            result = body.get("result", body)
            image = result.get("image") if isinstance(result, dict) else None
            if not image:
                raise RuntimeError(f"no image in response: {str(body)[:200]}")
            data = base64.b64decode(image)
        native = _save_image_bytes(data, out, w, h)
        return {"native_size": list(native)}

    def _provider_openai(self, spec: ProviderSpec, prompt: str, negative: str, out: Path,
                         w: int, h: int, seed: int) -> dict:
        from openai import OpenAI
        client = OpenAI()
        size = "1792x1024" if max(w, h) >= 1024 else "1024x1024"
        resp = client.images.generate(model=spec.model or "dall-e-3", prompt=prompt,
                                      size=size, n=1)
        import requests
        img_url = resp.data[0].url
        r = requests.get(img_url, timeout=60)
        r.raise_for_status()
        native = _save_image_bytes(r.content, out, w, h)
        return {"native_size": list(native)}

    def _provider_sd_webui(self, spec: ProviderSpec, prompt: str, negative: str, out: Path,
                           w: int, h: int, seed: int) -> dict:
        """Stable Diffusion WebUI (Automatic1111) HTTP API.

        Auto-detects SDXL Turbo by checking the loaded checkpoint name and
        switches to Turbo-optimal settings (4 steps, CFG 1, DPM++ SDE).
        """
        import requests
        api = os.environ["SD_API_URL"].rstrip("/")

        # Detect Turbo vs standard SD by checking the loaded checkpoint.
        is_turbo = False
        try:
            opts = requests.get(f"{api}/sdapi/v1/options", timeout=5).json()
            is_turbo = "turbo" in str(opts.get("sd_model_checkpoint", "")).lower()
        except Exception:  # noqa: BLE001
            pass

        if is_turbo:
            payload = {
                "prompt": prompt, "negative_prompt": negative,
                "width": w, "height": h, "seed": seed,
                "steps": 4, "cfg_scale": 1.0,
                "sampler_index": "DPM++ SDE",
            }
        else:
            payload = {
                "prompt": prompt, "negative_prompt": negative,
                "width": w, "height": h, "seed": seed,
                "steps": 20, "sampler_index": "Euler a",
            }
        r = requests.post(f"{api}/sdapi/v1/txt2img", json=payload, timeout=300)
        r.raise_for_status()
        b64 = r.json()["images"][0]
        native = _save_image_bytes(base64.b64decode(b64), out, w, h)
        return {"native_size": list(native), "turbo": is_turbo}

    def _provider_placeholder(self, spec: ProviderSpec, prompt: str, negative: str, out: Path,
                              w: int, h: int, seed: int) -> dict:
        from PIL import Image, ImageDraw, ImageFont
        import random
        rng = random.Random(seed)
        c1 = (rng.randint(20, 90), rng.randint(20, 90), rng.randint(40, 140))
        c2 = (rng.randint(120, 220), rng.randint(80, 200), rng.randint(60, 200))
        img = Image.new("RGB", (w, h), c1)
        draw = ImageDraw.Draw(img)
        # Vertical gradient.
        for y in range(h):
            t = y / max(1, h - 1)
            r = int(c1[0] + (c2[0] - c1[0]) * t)
            g = int(c1[1] + (c2[1] - c1[1]) * t)
            b = int(c1[2] + (c2[2] - c1[2]) * t)
            draw.line([(0, y), (w, y)], fill=(r, g, b))
        # Decorative shapes.
        for _ in range(8):
            x = rng.randint(0, w); yy = rng.randint(0, h)
            rad = rng.randint(40, 200)
            shade = (rng.randint(180, 255), rng.randint(180, 255), rng.randint(180, 255))
            draw.ellipse([x - rad, yy - rad, x + rad, yy + rad],
                         outline=shade, width=2)
        # Title.
        try:
            font = ImageFont.truetype("arial.ttf", 36)
        except Exception:  # noqa: BLE001
            font = ImageFont.load_default()
        text = (prompt[:80] + "…") if len(prompt) > 80 else prompt
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad = 20
        draw.rectangle(
            [(w - tw) // 2 - pad, h - th - 60 - pad,
             (w + tw) // 2 + pad, h - 60 + pad],
            fill=(0, 0, 0, 180),
        )
        draw.text(((w - tw) // 2, h - th - 60), text, fill=(255, 255, 255), font=font)
        img.save(out)
        return {"native_size": [w, h]}


# ---- helpers ------------------------------------------------------------

_WARNED: set = set()


def _warn_once(key: str, msg: str, *args) -> None:
    if key not in _WARNED:
        _WARNED.add(key)
        log.warning(msg, *args)


def _save_image_bytes(data: bytes, out: Path, w: int, h: int) -> tuple:
    """Decode provider bytes, cover-fit to exactly w x h, save in `out`'s format.

    Returns the provider's native (width, height). Raises if the bytes aren't
    an image, so a provider error page is never saved as a picture.
    """
    from PIL import Image, ImageOps
    img = Image.open(BytesIO(data))
    img.load()
    native = img.size
    img = img.convert("RGB")
    if img.size != (w, h):
        img = ImageOps.fit(img, (w, h), method=Image.LANCZOS)
    fmt = {".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}.get(out.suffix.lower(), "PNG")
    img.save(out, format=fmt)
    return native


def _served_model(data: bytes) -> Optional[str]:
    """Pollinations embeds generation params (incl. the model) as JSON in EXIF."""
    import re
    head = data[:4096].decode("latin-1", errors="ignore")
    m = re.search(r'"model"\s*:\s*"([^"]+)"', head)
    return m.group(1) if m else None
