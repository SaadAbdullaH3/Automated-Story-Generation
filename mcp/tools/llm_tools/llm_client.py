"""LLM access driven by the model settings layer (`config/providers.yaml`).

Callers ask for a role, not a model:

    client = get_llm_client("story")
    client.generate("...")                      # free text
    client.generate_structured("...", Schema)   # validated Pydantic object

The client walks that role's provider chain: the first provider with credentials
is used, and if a call fails (quota, outage, bad JSON) the next one takes over.
`provider == "mock"` means no real model is configured and the caller should use
its offline fallback (the template script, the keyword classifier, ...).
"""
from __future__ import annotations
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from shared import providers
from shared.providers import ProviderSpec
from shared.utils.logging import get_logger

log = get_logger("llm_client")

T = TypeVar("T", bound=BaseModel)

MOCK = "mock"

# OpenAI-compatible providers: name -> (base URL, API key env var)
OPENAI_COMPATIBLE: Dict[str, tuple] = {
    "openai": (None, "OPENAI_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "ollama": (None, None),   # base URL comes from OLLAMA_HOST; no key needed
}


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str


class LLMClient:
    """Single entry point for text + structured generation for one role."""

    def __init__(self, role: str = "story"):
        self.role = role
        self.specs: List[ProviderSpec] = [s for s in providers.chain(role)
                                          if s.provider != "mymemory"]
        log.info("LLM role=%s chain=[%s]", role,
                 ", ".join(str(s) for s in self.specs) or MOCK)

    # ---- what's active ---------------------------------------------------

    @property
    def spec(self) -> Optional[ProviderSpec]:
        return self.specs[0] if self.specs else None

    @property
    def provider(self) -> str:
        return self.spec.provider if self.spec else MOCK

    @property
    def model(self) -> str:
        return (self.spec.model or self.spec.provider) if self.spec else "mock-1.0"

    # ---- text generation -------------------------------------------------

    def generate(self, prompt: str, system: str = "", temperature: float = 0.7,
                 max_tokens: int = 2000) -> LLMResponse:
        errors = []
        for spec in self.specs:
            if spec.provider == MOCK:
                break
            try:
                text = self._call(spec, prompt, system, temperature, max_tokens)
                if not text.strip():
                    raise ValueError("empty response")
                return LLMResponse(text=text, provider=spec.provider,
                                   model=spec.model or spec.provider)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{spec.provider}: {e}")
                log.warning("%s failed for role %s (%s) — trying next provider",
                            spec.provider, self.role, str(e)[:200])
        if errors:
            log.warning("every provider failed for role %s: %s", self.role, "; ".join(errors))
        # Mock: callers fall back to their own offline path.
        return LLMResponse(text=f"[mock-llm] {prompt[:120]}", provider=MOCK, model="mock-1.0")

    # ---- structured (JSON) generation -----------------------------------

    def generate_structured(self, prompt: str, schema: Type[T], system: str = "",
                            temperature: float = 0.5, max_tokens: int = 3000,
                            max_retries: int = 1) -> T:
        """Returns a validated Pydantic instance, or raises if no provider can."""
        sys_msg = (
            (system + "\n\n" if system else "")
            + "You MUST respond with a single valid JSON object that matches the requested "
              "schema. Do not include markdown fences, comments, or any text outside the JSON."
        )
        errors: List[str] = []
        for spec in self.specs:
            if spec.provider == MOCK:
                break
            attempt_prompt = prompt
            for attempt in range(max_retries + 1):
                try:
                    text = self._call(spec, attempt_prompt, sys_msg, temperature, max_tokens,
                                      schema=schema)
                    return schema.model_validate(json.loads(_extract_json(text)))
                except (json.JSONDecodeError, ValidationError, TypeError) as e:
                    # The model answered, but not with usable JSON: worth one more try
                    # with the error fed back to it.
                    errors.append(f"{spec.provider}: {type(e).__name__}: {e}")
                    log.warning("%s structured attempt %d failed for role %s: %s",
                                spec.provider, attempt + 1, self.role, str(e)[:200])
                    attempt_prompt = (
                        f"{prompt}\n\nPrevious response was invalid: {e}\n"
                        "Return only valid JSON matching the schema."
                    )
                except Exception as e:  # noqa: BLE001
                    # The provider itself failed (quota, network, outage): move on.
                    errors.append(f"{spec.provider}: {type(e).__name__}: {e}")
                    log.warning("%s failed for role %s (%s) — trying next provider",
                                spec.provider, self.role, str(e)[:200])
                    break
        raise RuntimeError(
            f"no provider produced valid structured output for role '{self.role}'"
            + (f": {'; '.join(errors[-3:])}" if errors else
               " (no model configured — use the offline fallback)")
        )

    # ---- provider adapters ----------------------------------------------

    def _call(self, spec: ProviderSpec, prompt: str, system: str, temperature: float,
              max_tokens: int, schema: Optional[Type[BaseModel]] = None) -> str:
        if spec.provider == "gemini":
            return _gemini(spec, prompt, system, temperature, max_tokens, schema)
        if spec.provider == "anthropic":
            return _anthropic(spec, prompt, system, temperature, max_tokens)
        if spec.provider in OPENAI_COMPATIBLE:
            return _openai_compatible(spec, prompt, system, temperature, max_tokens, schema)
        raise ValueError(f"unknown LLM provider '{spec.provider}'")


# ---- adapters --------------------------------------------------------------

def _gemini(spec: ProviderSpec, prompt: str, system: str, temperature: float,
            max_tokens: int, schema: Optional[Type[BaseModel]] = None) -> str:
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    config: Dict[str, Any] = {
        "temperature": temperature,
        "max_output_tokens": max_tokens,
    }
    if system:
        config["system_instruction"] = system
    if schema is not None:
        # Native structured output: the model is constrained to the schema.
        config["response_mime_type"] = "application/json"
        config["response_schema"] = schema
    resp = client.models.generate_content(
        model=spec.model or "gemini-flash-latest",
        contents=prompt,
        config=types.GenerateContentConfig(**config),
    )
    return resp.text or ""


def _anthropic(spec: ProviderSpec, prompt: str, system: str, temperature: float,
               max_tokens: int) -> str:
    import anthropic
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=spec.model or "claude-sonnet-4-6",
        max_tokens=max_tokens,
        temperature=temperature,
        system=system or "You are a helpful assistant.",
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in resp.content if hasattr(b, "text"))


def _openai_compatible(spec: ProviderSpec, prompt: str, system: str, temperature: float,
                       max_tokens: int, schema: Optional[Type[BaseModel]] = None) -> str:
    """OpenAI, Groq, OpenRouter and Ollama all speak the chat-completions API."""
    from openai import OpenAI
    base_url, key_env = OPENAI_COMPATIBLE[spec.provider]
    if spec.provider == "ollama":
        base_url = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/") + "/v1"
        api_key = "ollama"   # ignored by Ollama, required by the client
    else:
        api_key = os.environ[key_env] if key_env else None
    client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)

    messages = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": prompt}]
    kwargs: Dict[str, Any] = {
        "model": spec.model or "gpt-4o-mini",
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if schema is not None:
        kwargs["response_format"] = {"type": "json_object"}
    resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""


# ---- helpers ---------------------------------------------------------------

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _extract_json(text: str) -> str:
    """Pull the JSON object out of a response that may carry fences or prose."""
    text = _FENCE.sub("", text.strip()).strip()
    if not text.startswith(("{", "[")):
        match = re.search(r"\{.*\}|\[.*\]", text, flags=re.DOTALL)
        if match:
            text = match.group(0)
    return text


_CLIENTS: Dict[str, LLMClient] = {}


def get_llm_client(role: str = "story", force_new: bool = False) -> LLMClient:
    """Cached client per role (the chain is resolved once per process)."""
    if force_new or role not in _CLIENTS:
        _CLIENTS[role] = LLMClient(role=role)
    return _CLIENTS[role]


def reset_clients() -> None:
    """Drop cached clients — used by tests that change the environment."""
    _CLIENTS.clear()
