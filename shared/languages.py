"""Subtitle languages the pipeline supports, in one place.

Each entry: display name -> (ISO 639-2/B code for MP4 track metadata,
MyMemory locale code for the free translation fallback).
The web UI's language dropdown should only offer names listed here.
"""
from __future__ import annotations
from typing import Dict, List, Optional, Tuple

SUBTITLE_LANGUAGES: Dict[str, Tuple[str, str]] = {
    "English":    ("eng", "en-GB"),
    "Urdu":       ("urd", "ur-PK"),
    "Hindi":      ("hin", "hi-IN"),
    "Arabic":     ("ara", "ar-SA"),
    "French":     ("fre", "fr-FR"),
    "Spanish":    ("spa", "es-ES"),
    "German":     ("ger", "de-DE"),
    "Italian":    ("ita", "it-IT"),
    "Portuguese": ("por", "pt-PT"),
    "Russian":    ("rus", "ru-RU"),
    "Turkish":    ("tur", "tr-TR"),
    "Japanese":   ("jpn", "ja-JP"),
    "Korean":     ("kor", "ko-KR"),
    "Chinese":    ("chi", "zh-CN"),
}


def canonical(name: Optional[str]) -> Optional[str]:
    """Case-insensitive lookup -> canonical display name, or None if unsupported."""
    if not name:
        return None
    key = name.strip().lower()
    return next((n for n in SUBTITLE_LANGUAGES if n.lower() == key), None)


def iso639_2(name: str) -> str:
    lang = canonical(name)
    return SUBTITLE_LANGUAGES[lang][0] if lang else "und"


def mymemory_code(name: str) -> Optional[str]:
    lang = canonical(name)
    return SUBTITLE_LANGUAGES[lang][1] if lang else None


def supported_names() -> List[str]:
    return list(SUBTITLE_LANGUAGES)
