"""M1 — subtitle translation never ships English under a foreign label."""
from __future__ import annotations

import pytest

from mcp.tool_executor import ToolExecutor
from mcp.tools.llm_tools.translate_tool import TranslateTool, _chunks


def test_translate_tool_fails_when_every_provider_fails(monkeypatch):
    def boom(self, lines, lang):
        raise RuntimeError("quota exceeded")
    monkeypatch.setattr(TranslateTool, "_mymemory", boom)
    res = ToolExecutor().execute("text.translate", lines=["hello"], target_language="French")
    assert res.success is False
    assert "quota exceeded" in res.error


def test_translate_tool_rejects_unsupported_language():
    res = ToolExecutor().execute("text.translate", lines=["hi"], target_language="Klingon")
    assert res.success is False


def test_mymemory_chunks_respect_request_limit():
    lines = [f"line number {i} " * 6 for i in range(40)]
    chunks = _chunks(lines, 450)
    assert sum(len(c) for c in chunks) == len(lines)
    assert all(len("\n".join(c)) <= 450 for c in chunks)


def test_failed_translation_is_skipped_not_mislabeled(small_project, monkeypatch):
    def boom(self, lines, lang):
        raise RuntimeError("rate limited")
    monkeypatch.setattr(TranslateTool, "_mymemory", boom)
    state, _ = small_project(subtitle_language="French")
    assert state.video.subtitle_languages == ["English"]
    assert "French" not in state.video.subtitle_cache


def test_translation_embedded_and_cached(small_project, fake_translation):
    from agents.video_agent import VideoAgent
    state, _ = small_project(subtitle_language="Urdu")
    assert state.video.subtitle_languages == ["Urdu", "English"]  # default track first
    assert fake_translation == ["Urdu"]
    assert state.video.subtitle_cache["Urdu"][0].startswith("[Urdu] ")

    VideoAgent().compose(state)          # nothing changed -> no new translation calls
    assert fake_translation == ["Urdu"]


def test_extra_languages_from_env(small_project, fake_translation, monkeypatch):
    monkeypatch.setenv("SUBTITLE_EXTRA_LANGUAGES", "Spanish, klingon ,Urdu")
    state, _ = small_project(subtitle_language="Urdu")
    assert sorted(state.video.subtitle_languages) == ["English", "Spanish", "Urdu"]


@pytest.mark.parametrize("name,code", [("urdu", "urd"), ("Japanese", "jpn"), ("xx", "und")])
def test_language_table(name, code):
    from shared.languages import iso639_2
    assert iso639_2(name) == code
