"""M9 — burned subtitles are readable in every script, on whatever machine renders them.

Building the production image found the container picked "Noto Sans" for
every non-Latin language, and Noto Sans has no Arabic, Devanagari or CJK at
all: an Urdu film's burned subtitles were a row of empty boxes, with no error
anywhere. The tests that existed checked the font's *name* against a list —
which the wrong name was on.

These burn a real sentence through the app's own subtitle tool and look at
what came out. A missing glyph is drawn as a hollow rectangle; a line that is
mostly hollow rectangles is a line of boxes. (Comparing pixels against a
stand-in string doesn't work — libass draws the boxes at a different size —
and libass's own log can't be trusted either: it reports loading Noto
Nastaliq Urdu cleanly, then draws boxes.)

Measured in the container when this was written — share of shapes that are
boxes: Urdu in Noto Sans 0.95, in Noto Nastaliq Urdu 1.00, in Noto Naskh
Arabic 0.00. Hindi and CJK in Noto Sans came out fine: libass found a
fallback for them, and not for Urdu.
"""
from __future__ import annotations

import shutil
import subprocess
from collections import deque
from pathlib import Path
from typing import List, Set, Tuple

import pytest
from PIL import Image

from mcp.tool_executor import ToolExecutor
from shared import fonts

SAMPLES = {
    "Urdu": "ہم ایک ہی نسخہ بانٹتے ہیں",
    "Arabic": "نحن نتشارك الوصفة نفسها",
    "Hindi": "हम एक ही नुस्खा साझा करते हैं",
    "Japanese": "私たちは同じレシピを共有します",
    "Korean": "우리는 같은 레시피를 공유합니다",
    "Chinese": "我们分享同一个食谱",
}

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _burned(tmp_path: Path, text: str, language: str) -> Image.Image:
    """Burn one line through the real tool, large enough to see each glyph."""
    clip = tmp_path / "plain.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=0x303030:s=1280x360:d=2:r=12", "-pix_fmt", "yuv420p",
                    str(clip)], check=True)
    out = tmp_path / "burned.mp4"
    res = ToolExecutor().execute("video.subtitle", in_path=str(clip), out_path=str(out),
                                 lines=[{"start_ms": 0, "end_ms": 2000, "text": text}],
                                 language=language, font_size=64)
    assert res.success, res.error
    png = tmp_path / "frame.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "1", "-i", str(out),
                    "-frames:v", "1", str(png)], check=True)
    return Image.open(png).convert("L")


Shape = Tuple[Set[Tuple[int, int]], int, int, int, int]


def _shapes(im: Image.Image, bright: int = 150, min_side: int = 12) -> List[Shape]:
    """Connected bright regions at least `min_side` wide and tall: the glyphs."""
    w, h = im.size
    px = im.load()
    seen = bytearray(w * h)
    found: List[Shape] = []
    for y in range(h):
        for x in range(w):
            if seen[y * w + x] or px[x, y] <= bright:
                continue
            queue, pts = deque([(x, y)]), set()
            seen[y * w + x] = 1
            while queue:
                cx, cy = queue.popleft()
                pts.add((cx, cy))
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if 0 <= nx < w and 0 <= ny < h and not seen[ny * w + nx] \
                            and px[nx, ny] > bright:
                        seen[ny * w + nx] = 1
                        queue.append((nx, ny))
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
            if x1 - x0 + 1 >= min_side and y1 - y0 + 1 >= min_side:
                found.append((pts, x0, x1, y0, y1))
    return found


def _is_box(shape: Shape) -> bool:
    """All four edges drawn, nothing inside: the missing-glyph rectangle."""
    pts, x0, x1, y0, y1 = shape
    edges = [[(x, y0) for x in range(x0, x1 + 1)], [(x, y1) for x in range(x0, x1 + 1)],
             [(x0, y) for y in range(y0, y1 + 1)], [(x1, y) for y in range(y0, y1 + 1)]]
    if not all(sum(p in pts for p in edge) >= 0.8 * len(edge) for edge in edges):
        return False
    inside = [(x, y) for x in range(x0 + 2, x1 - 1) for y in range(y0 + 2, y1 - 1)]
    return not inside or sum(p in pts for p in inside) <= 0.15 * len(inside)


@needs_ffmpeg
@pytest.mark.parametrize("language", list(SAMPLES))
def test_burned_subtitles_are_readable(language, tmp_path):
    font = fonts.font_for(language)
    covering = fonts.covering_families(fonts.FC_LANG[language]) if \
        shutil.which("fc-list") else None
    if not fonts.is_installed(font) or (covering is not None and font.lower() not in covering):
        pytest.skip(f"no font for {language} on this machine")

    shapes = _shapes(_burned(tmp_path, SAMPLES[language], language))
    assert shapes, f"nothing was drawn for {language} in {font!r}"
    boxes = sum(_is_box(s) for s in shapes)
    assert boxes <= 0.2 * len(shapes), (
        f"{language} in {font!r} is drawn as empty boxes ({boxes} of {len(shapes)} shapes)")


# ---- the choice itself ----------------------------------------------------------

def test_a_font_that_does_not_cover_the_language_is_passed_over(monkeypatch):
    """The container's case: Noto Sans is installed and first-listed in the old
    order, and covers none of these scripts."""
    installed = {"Noto Sans", "Noto Naskh Arabic", "Noto Sans Devanagari", "Noto Sans CJK JP"}
    covers = {"ur": {"noto naskh arabic"}, "hi": {"noto sans devanagari"},
              "ja": {"noto sans cjk jp"}}
    monkeypatch.setattr(fonts, "is_installed", lambda f: f in installed)
    monkeypatch.setattr(fonts, "covering_families", lambda lang: frozenset(covers.get(lang, ())))
    fonts.clear_cache()
    assert fonts.font_for("Urdu") == "Noto Naskh Arabic"
    assert fonts.font_for("Hindi") == "Noto Sans Devanagari"
    assert fonts.font_for("Japanese") == "Noto Sans CJK JP"
    fonts.clear_cache()


def test_without_fontconfig_each_script_still_gets_its_own_font(monkeypatch):
    """Windows: Segoe UI has Arabic but no Devanagari or CJK."""
    installed = {"Arial", "Segoe UI", "Nirmala UI", "Yu Gothic", "Malgun Gothic",
                 "Microsoft YaHei"}
    monkeypatch.setattr(fonts, "is_installed", lambda f: f in installed)
    monkeypatch.setattr(fonts, "covering_families", lambda lang: None)
    fonts.clear_cache()
    assert [fonts.font_for(lang) for lang in
            ("English", "Urdu", "Hindi", "Japanese", "Korean", "Chinese")] == [
        "Arial", "Segoe UI", "Nirmala UI", "Yu Gothic", "Malgun Gothic", "Microsoft YaHei"]
    fonts.clear_cache()


def test_with_none_of_ours_installed_fontconfig_is_asked(monkeypatch):
    monkeypatch.setattr(fonts, "is_installed", lambda f: False)
    monkeypatch.setattr(fonts, "covering_families",
                        lambda lang: frozenset({"some arabic face"}))
    monkeypatch.setattr(fonts, "_fc_match", lambda pattern: "Some Arabic Face")
    fonts.clear_cache()
    assert fonts.font_for("Urdu") == "Some Arabic Face"
    fonts.clear_cache()
