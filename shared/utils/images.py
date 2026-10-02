"""Checks on a still image before ffmpeg is given it."""
from __future__ import annotations
from pathlib import Path


def ensure_readable_image(path: str | Path) -> None:
    """Raise ValueError unless `path` decodes as an image.

    A still becomes video through `ffmpeg -loop 1 -i still.png`, which makes
    it an endless input. When ffmpeg cannot decode it, it does not fail: it
    retries forever and never produces a frame, so the render hangs with no
    error, the worker's heartbeat keeps the job looking alive, and a cancel
    never lands because the step never ends.
    """
    from PIL import Image
    try:
        with Image.open(path) as im:
            im.load()
    except Exception as e:  # noqa: BLE001 — missing, truncated, not an image at all
        raise ValueError(f"unreadable image {Path(path).name}: {e}") from e
