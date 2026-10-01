"""Pick a subtitle font that exists here and has the script's glyphs.

A font without the glyphs doesn't fail — libass just draws nothing, which is
how a whole Urdu subtitle track can vanish without one error in the log. And a
font name that is right on Windows ("Segoe UI") is absent on the Linux image
the container runs, so the name cannot be hard-coded either.
"""
from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache
from typing import List, Optional, Sequence

from shared.languages import WIDE_SCRIPT_LANGUAGES, canonical

# In preference order. The first family actually installed wins.
WIDE_SCRIPT_FONTS = [
    "Segoe UI",          # Windows: covers Arabic/Urdu, Devanagari and CJK
    "Nirmala UI",        # Windows: Indic
    "Noto Sans",         # Linux/containers: the Noto family covers nearly all
    "Noto Naskh Arabic",
    "DejaVu Sans",
    "Arial Unicode MS",
]
LATIN_FONTS = ["Arial", "Helvetica", "DejaVu Sans", "Liberation Sans", "Noto Sans"]


def font_for(language: str) -> str:
    """The best installed font for this subtitle language."""
    wide = canonical(language) in WIDE_SCRIPT_LANGUAGES
    return _first_installed(tuple(WIDE_SCRIPT_FONTS if wide else LATIN_FONTS))


@lru_cache(maxsize=8)
def _first_installed(candidates: Sequence[str]) -> str:
    for family in candidates:
        if is_installed(family):
            return family
    # Nothing matched — name the first candidate anyway and let libass
    # substitute, which is still better than an empty FontName.
    return candidates[0]


@lru_cache(maxsize=64)
def is_installed(family: str) -> bool:
    """Whether fontconfig can resolve this family to itself (not a substitute)."""
    resolved = _fc_match(family)
    if resolved is None:
        return _windows_has(family)
    return resolved.strip().lower() == family.strip().lower()


def _fc_match(family: str) -> Optional[str]:
    """The family fontconfig would actually use, or None where it isn't available."""
    if not shutil.which("fc-match"):
        return None
    try:
        out = subprocess.run(["fc-match", "-f", "%{family}", family],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    # fc-match can answer with a comma-separated list of aliases.
    return out.stdout.split(",")[0]


def _windows_has(family: str) -> bool:
    """No fontconfig (Windows): look for the font file itself."""
    from pathlib import Path
    stem = family.replace(" ", "").lower()
    for directory in _windows_font_dirs():
        if not directory.is_dir():
            continue
        for path in directory.glob("*.tt[fc]"):
            if path.stem.replace(" ", "").replace("-", "").lower().startswith(stem):
                return True
    return False


def _windows_font_dirs() -> List["Path"]:  # noqa: F821 - imported lazily above
    import os
    from pathlib import Path
    dirs = [Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Fonts"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    return dirs
