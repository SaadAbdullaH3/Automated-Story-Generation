"""Subtitle translation tool.

Strategy (first success wins):
1. The configured LLM (when not the mock provider): one request per language,
   answering with a JSON array aligned 1:1 with the input lines.
2. MyMemory (free, no key): lines are batched into <=450-char newline-joined
   requests. Anonymous use allows ~5,000 chars/day; set MYMEMORY_EMAIL in
   .env to raise that to ~50,000.

If every provider fails the tool FAILS — callers must skip the language rather
than ship an untranslated track under a foreign label.
"""
from __future__ import annotations
import json
import os
import re
import time
from typing import List

from mcp.base_tool import BaseTool, ToolResult
from shared.languages import canonical, mymemory_code
from shared.utils.logging import get_logger

from .llm_client import get_llm_client

log = get_logger("translate")

_MYMEMORY_MAX_CHARS = 450  # MyMemory rejects requests over 500 chars


class TranslateTool(BaseTool):
    name = "text.translate"
    description = "Translate a list of subtitle lines into a target language (LLM, then MyMemory)."
    category = "llm"

    def run(self, lines: List[str], target_language: str, **_) -> ToolResult:
        lang = canonical(target_language)
        if not lang:
            return ToolResult(success=False, error=f"unsupported language '{target_language}'")
        if lang == "English" or not lines:
            return ToolResult(success=True, data=list(lines), metadata={"provider": "identity"})

        errors: List[str] = []
        client = get_llm_client()
        if client.provider != "mock":
            try:
                out = self._llm(client, lines, lang)
                return ToolResult(success=True, data=out,
                                  metadata={"provider": f"llm:{client.provider}"})
            except Exception as e:  # noqa: BLE001
                errors.append(f"llm: {e}")
                log.info("LLM translation to %s failed (%s) — trying MyMemory", lang, e)
        try:
            out = self._mymemory(lines, lang)
            return ToolResult(success=True, data=out, metadata={"provider": "mymemory"})
        except Exception as e:  # noqa: BLE001
            errors.append(f"mymemory: {e}")
        return ToolResult(success=False, error="; ".join(errors))

    # ---- providers -------------------------------------------------------

    @staticmethod
    def _llm(client, lines: List[str], lang: str) -> List[str]:
        prompt = (
            f"Translate each subtitle line below into {lang}. Keep the tone and keep "
            f"lines short enough to read on screen.\n"
            f"Return ONLY a JSON array of {len(lines)} strings, in the same order, "
            f"one translation per input line.\n\n"
            + json.dumps(lines, ensure_ascii=False)
        )
        text = client.generate(prompt, system="You are a professional subtitle translator.",
                               temperature=0.2, max_tokens=4000).text
        match = re.search(r"\[.*\]", text, flags=re.DOTALL)
        if not match:
            raise ValueError("no JSON array in response")
        out = json.loads(match.group(0))
        if not isinstance(out, list) or len(out) != len(lines) \
                or not all(isinstance(s, str) and s.strip() for s in out):
            raise ValueError(f"expected {len(lines)} non-empty strings, got {len(out) if isinstance(out, list) else type(out).__name__}")
        return [s.strip() for s in out]

    def _mymemory(self, lines: List[str], lang: str) -> List[str]:
        from deep_translator import MyMemoryTranslator
        code = mymemory_code(lang)
        if not code:
            raise ValueError(f"MyMemory has no code for {lang}")
        kwargs = {}
        if os.getenv("MYMEMORY_EMAIL"):
            kwargs["email"] = os.environ["MYMEMORY_EMAIL"]
        translator = MyMemoryTranslator(source="en-GB", target=code, **kwargs)

        out: List[str] = []
        for chunk in _chunks(lines, _MYMEMORY_MAX_CHARS):
            joined = "\n".join(s.replace("\n", " ") for s in chunk)
            result = self._call_with_retry(translator, joined)
            parts = [p.strip() for p in result.split("\n")]
            if len(parts) != len(chunk):
                # Line breaks weren't preserved — fall back to one request per line.
                parts = [self._call_with_retry(translator, s).strip() for s in chunk]
            out.extend(parts)
        return out

    @staticmethod
    def _call_with_retry(translator, text: str, attempts: int = 3) -> str:
        last: Exception | None = None
        for i in range(attempts):
            try:
                result = translator.translate(text)
                if not result or "MYMEMORY WARNING" in result.upper():
                    raise RuntimeError(result or "empty translation")
                return result
            except Exception as e:  # noqa: BLE001
                last = e
                if "MYMEMORY WARNING" in str(e).upper():
                    break  # daily quota used up — retrying won't help
                time.sleep(1.5 * (i + 1))
        raise RuntimeError(str(last))


def _chunks(lines: List[str], max_chars: int) -> List[List[str]]:
    chunks: List[List[str]] = []
    current: List[str] = []
    size = 0
    for line in lines:
        add = len(line) + (1 if current else 0)
        if current and size + add > max_chars:
            chunks.append(current)
            current, size = [], 0
            add = len(line)
        current.append(line)
        size += add
    if current:
        chunks.append(current)
    return chunks
