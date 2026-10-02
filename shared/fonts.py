"""Pick a subtitle font that exists here and has the script's glyphs.

A font without the glyphs doesn't fail — libass draws a row of empty boxes,
which is how a whole Urdu subtitle track can be unreadable without one error
in the log. And a font name that is right on Windows ("Segoe UI") is absent on
the Linux image the container runs, so the name cannot be hard-coded either.

The choice is per language because the fonts are split per script. In the
container "Noto Sans" has no Arabic, Devanagari or CJK at all — it was the
pick for every one of them, and burned Urdu came out as boxes. "Segoe UI" has
Arabic but no Devanagari or CJK. Where fontconfig is present a family is only
used if it covers the language — and coverage is necessary, not sufficient:
fontconfig lists Noto Nastaliq Urdu for Urdu, yet libass draws it as boxes
too, so it is not on the list. A test renders each language to prove it.
"""
from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache
from typing import FrozenSet, List, Optional, Sequence

from shared.languages import canonical

# Per language, best first: Windows names, then the container's Noto family.
FONTS_BY_LANGUAGE = {
    "Urdu": ["Segoe UI", "Noto Naskh Arabic", "Noto Sans Arabic"],
    "Arabic": ["Segoe UI", "Noto Naskh Arabic", "Noto Sans Arabic", "DejaVu Sans"],
    "Hindi": ["Nirmala UI", "Noto Sans Devanagari", "Mangal"],
    "Japanese": ["Yu Gothic", "Meiryo", "MS Gothic", "Noto Sans CJK JP"],
    "Korean": ["Malgun Gothic", "Noto Sans CJK KR"],
    "Chinese": ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC"],
}
LATIN_FONTS = ["Arial", "Helvetica", "DejaVu Sans", "Liberation Sans", "Noto Sans"]

# fontconfig's language tags, for the coverage check.
FC_LANG = {"Urdu": "ur", "Arabic": "ar", "Hindi": "hi", "Japanese": "ja",
           "Korean": "ko", "Chinese": "zh-cn", "Russian": "ru", "Turkish": "tr"}


def font_for(language: str) -> str:
    """The best installed font for this subtitle language."""
    name = canonical(language) or language
    candidates = FONTS_BY_LANGUAGE.get(name, LATIN_FONTS)
    return _pick(tuple(candidates), FC_LANG.get(name))


@lru_cache(maxsize=32)
def _pick(candidates: Sequence[str], fc_lang: Optional[str]) -> str:
    covering = covering_families(fc_lang) if fc_lang else None
    for family in candidates:
        if is_installed(family) and (covering is None or family.lower() in covering):
            return family
    if covering:
        # None of ours is here, but something installed covers the language:
        # fontconfig's own pick beats a name known to lack the glyphs.
        best = _fc_match(f":lang={fc_lang}")
        if best and best.lower() in covering:
            return best
    # Nothing matched — name the first candidate anyway and let libass
    # substitute, which is still better than an empty FontName.
    return candidates[0]


@lru_cache(maxsize=32)
def covering_families(fc_lang: str) -> Optional[FrozenSet[str]]:
    """Families (lower-cased) fontconfig says cover a language; None without fontconfig."""
    if not shutil.which("fc-list"):
        return None
    try:
        out = subprocess.run(["fc-list", f":lang={fc_lang}", "family"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return frozenset(line.split(",")[0].strip().lower()
                     for line in out.stdout.splitlines() if line.strip())


def clear_cache() -> None:
    """For tests that change what is 'installed'."""
    for cached in (_pick, covering_families, is_installed, _windows_families):
        if hasattr(cached, "cache_clear"):      # a test may have swapped one out
            cached.cache_clear()


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
    """No fontconfig (Windows): ask the registry, then look for the file.

    File names don't follow family names ("Nirmala UI" is Nirmala.ttc,
    "Microsoft YaHei" is msyh.ttc), so the registry's own list comes first.
    """
    wanted = family.strip().lower()
    for name in _windows_families():
        # "Segoe UI Bold" is still Segoe UI.
        if name == wanted or name.startswith(wanted + " "):
            return True
    from pathlib import Path
    stem = family.replace(" ", "").lower()
    for directory in _windows_font_dirs():
        if not directory.is_dir():
            continue
        for path in directory.glob("*.tt[fc]"):
            if path.stem.replace(" ", "").replace("-", "").lower().startswith(stem):
                return True
    return False


@lru_cache(maxsize=1)
def _windows_families() -> FrozenSet[str]:
    """Family names from the registry's font list, lower-cased (empty elsewhere)."""
    try:
        import winreg
    except ImportError:
        return frozenset()
    names = set()
    key_path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            key = winreg.OpenKey(hive, key_path)
        except OSError:
            continue
        with key:
            for i in range(winreg.QueryInfoKey(key)[1]):
                value = winreg.EnumValue(key, i)[0]
                # "Microsoft YaHei & Microsoft YaHei UI (TrueType)"
                value = value.split(" (")[0]
                names.update(part.strip().lower() for part in value.split("&"))
    return frozenset(names)


def _windows_font_dirs() -> List["Path"]:  # noqa: F821 - imported lazily above
    import os
    from pathlib import Path
    dirs = [Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Fonts"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    return dirs
