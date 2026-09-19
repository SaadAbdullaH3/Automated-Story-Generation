"""Image edit tool — applies named filters via Pillow / OpenCV (subset).

Filters live in `phase5_edit/filters.py` style spirit; we expose them
through a stable named API so the edit agent can invoke them by name.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any, Dict

from mcp.base_tool import BaseTool, ToolResult


def _filter_brightness(img, factor: float = 1.2):
    from PIL import ImageEnhance
    return ImageEnhance.Brightness(img).enhance(factor)


def _filter_contrast(img, factor: float = 1.3):
    from PIL import ImageEnhance
    return ImageEnhance.Contrast(img).enhance(factor)


def _filter_saturation(img, factor: float = 1.5):
    from PIL import ImageEnhance
    return ImageEnhance.Color(img).enhance(factor)


def _filter_sharpness(img, factor: float = 2.0):
    from PIL import ImageEnhance
    return ImageEnhance.Sharpness(img).enhance(factor)


def _filter_grayscale(img, **_):
    return img.convert("L").convert("RGB")


def _filter_sepia(img, **_):
    from PIL import ImageOps
    g = img.convert("L")
    return ImageOps.colorize(g, (40, 24, 8), (255, 220, 170))


def _filter_blur(img, radius: float = 3.0):
    from PIL import ImageFilter
    return img.filter(ImageFilter.GaussianBlur(radius=radius))


def _filter_darker(img, factor: float = 0.7):
    from PIL import ImageEnhance
    return ImageEnhance.Brightness(img).enhance(factor)


def _filter_brighter(img, factor: float = 1.3):
    from PIL import ImageEnhance
    return ImageEnhance.Brightness(img).enhance(factor)


def _filter_warm(img, **_):
    from PIL import Image
    r, g, b = img.split()
    r = r.point(lambda p: min(255, int(p * 1.15)))
    b = b.point(lambda p: int(p * 0.85))
    return Image.merge("RGB", (r, g, b))


def _filter_cool(img, **_):
    from PIL import Image
    r, g, b = img.split()
    r = r.point(lambda p: int(p * 0.85))
    b = b.point(lambda p: min(255, int(p * 1.15)))
    return Image.merge("RGB", (r, g, b))


def _filter_vintage(img, **_):
    return _filter_sepia(_filter_blur(img, radius=1.0))


def _filter_invert(img, **_):
    from PIL import ImageOps
    return ImageOps.invert(img.convert("RGB"))


FILTERS = {
    "brightness": _filter_brightness,
    "contrast": _filter_contrast,
    "saturation": _filter_saturation,
    "sharpness": _filter_sharpness,
    "grayscale": _filter_grayscale,
    "sepia": _filter_sepia,
    "blur": _filter_blur,
    "darker": _filter_darker,
    "brighter": _filter_brighter,
    "warm": _filter_warm,
    "cool": _filter_cool,
    "vintage": _filter_vintage,
    "invert": _filter_invert,
}


class ImageEditTool(BaseTool):
    name = "vision.edit_image"
    description = "Apply named filters or style presets (noir, cinematic, ...) to an image."
    category = "vision"

    def run(self, in_path: str, out_path: str, filters: list | None = None,
            params: Dict[str, Any] | None = None, **_) -> ToolResult:
        from PIL import Image
        from .style_transfer import STYLES

        names = [str(n).lower() for n in (filters or [])]
        unknown = [n for n in names if n not in FILTERS and n not in STYLES]
        if unknown or not names:
            return ToolResult(success=False,
                              error=f"unknown filter(s) {unknown or names}; available: "
                                    f"{list_filter_names()}")
        in_p = Path(in_path)
        out_p = Path(out_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        img = Image.open(in_p).convert("RGB")
        applied = []
        for name in names:
            # Style presets expand to a chain of basic filters.
            chain = STYLES.get(name) if name not in FILTERS else [(name, (params or {}).get(name, {}))]
            for fname, kwargs in chain:
                fn = FILTERS[fname]
                img = fn(img, **kwargs) if kwargs else fn(img)
                applied.append(fname)
        img.save(out_p)
        return ToolResult(success=True, data=str(out_p),
                          metadata={"applied": applied, "requested": names})


def list_filter_names() -> list[str]:
    from .style_transfer import STYLES
    return sorted(set(FILTERS) | set(STYLES))
