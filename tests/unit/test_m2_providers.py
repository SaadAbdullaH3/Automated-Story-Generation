"""M2 — the model settings layer: config, chains, fallback, parallelism."""
from __future__ import annotations
import textwrap
import threading
import time

import pytest

from shared import providers
from shared.utils.parallel import run_jobs

CONFIG = textwrap.dedent("""
    version: 1
    roles:
      story:
        - provider: gemini
          model: gemini-flash-latest
          requires: [TEST_GEMINI_KEY]
        - provider: groq
          model: openai/gpt-oss-120b
          requires: [TEST_GROQ_KEY|TEST_GROQ_ALT]
        - provider: mock
      image:
        - provider: cloudflare
          model: "@cf/flux"
          params: {steps: 8}
          requires: [TEST_CF_ACCOUNT, TEST_CF_TOKEN]
          concurrency: 4
        - provider: placeholder
          concurrency: 1
""")


@pytest.fixture
def config(tmp_path, monkeypatch):
    path = tmp_path / "providers.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    monkeypatch.setenv("PROVIDERS_FILE", str(path))
    for name in ("TEST_GEMINI_KEY", "TEST_GROQ_KEY", "TEST_GROQ_ALT",
                 "TEST_CF_ACCOUNT", "TEST_CF_TOKEN", "LLM_PROVIDER",
                 "PROVIDER_STORY", "PROVIDER_IMAGE"):
        monkeypatch.delenv(name, raising=False)
    providers.load(force=True)
    yield
    providers.load(force=True)


# ---- config + chains -------------------------------------------------------

def test_providers_without_credentials_are_skipped(config):
    assert [s.provider for s in providers.chain("story")] == ["mock"]
    assert providers.active("story").provider == "mock"


def test_credentials_promote_a_provider(config, monkeypatch):
    monkeypatch.setenv("TEST_GEMINI_KEY", "x")
    providers.load(force=True)
    active = providers.active("story")
    assert active.provider == "gemini" and active.model == "gemini-flash-latest"


def test_either_of_two_env_vars_satisfies_a_requirement(config, monkeypatch):
    monkeypatch.setenv("TEST_GROQ_ALT", "x")
    providers.load(force=True)
    assert [s.provider for s in providers.chain("story")] == ["groq", "mock"]


def test_params_and_concurrency_come_from_config(config, monkeypatch):
    monkeypatch.setenv("TEST_CF_ACCOUNT", "a")
    monkeypatch.setenv("TEST_CF_TOKEN", "t")
    providers.load(force=True)
    spec = providers.active("image")
    assert spec.provider == "cloudflare" and spec.params == {"steps": 8}
    assert providers.concurrency("image") == 4
    assert providers.concurrency("story") == 1        # default when unset


def test_env_override_forces_one_provider(config, monkeypatch):
    monkeypatch.setenv("TEST_GEMINI_KEY", "x")
    monkeypatch.setenv("PROVIDER_STORY", "mock")
    providers.load(force=True)
    assert [s.provider for s in providers.chain("story")] == ["mock"]


def test_legacy_llm_provider_env_still_works(config, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    providers.load(force=True)
    assert providers.active("story").provider == "mock"


def test_describe_lists_what_is_missing(config):
    rows = providers.describe()["story"]
    gemini = next(r for r in rows if r["provider"] == "gemini")
    assert gemini["available"] is False and gemini["missing"] == ["TEST_GEMINI_KEY"]
    assert any(r["available"] for r in rows)


def test_shipped_config_is_valid():
    """The config that ships with the repo must parse and cover every role."""
    cfg = providers.load(force=True)
    for role in ("story", "edit_intent", "translate", "image", "tts", "music"):
        assert cfg.chain(role), f"no usable provider for {role} without any keys"


# ---- LLM chain -------------------------------------------------------------

def _client(monkeypatch, calls, failures=()):
    from mcp.tools.llm_tools import llm_client
    monkeypatch.setenv("TEST_GEMINI_KEY", "x")
    monkeypatch.setenv("TEST_GROQ_KEY", "x")
    providers.load(force=True)

    def fake_call(self, spec, prompt, system, temperature, max_tokens, schema=None):
        calls.append(spec.provider)
        if spec.provider in failures:
            raise RuntimeError(f"{spec.provider} is down")
        return '{"ok": true}'

    monkeypatch.setattr(llm_client.LLMClient, "_call", fake_call)
    llm_client.reset_clients()
    return llm_client.get_llm_client("story", force_new=True)


def test_llm_uses_the_first_available_provider(config, monkeypatch):
    calls = []
    client = _client(monkeypatch, calls)
    assert client.provider == "gemini"
    assert client.generate("hi").provider == "gemini"
    assert calls == ["gemini"]


def test_llm_falls_through_to_the_next_provider(config, monkeypatch):
    calls = []
    client = _client(monkeypatch, calls, failures={"gemini"})
    resp = client.generate("hi")
    assert calls == ["gemini", "groq"]
    assert resp.provider == "groq"


def test_llm_returns_mock_when_every_provider_fails(config, monkeypatch):
    calls = []
    client = _client(monkeypatch, calls, failures={"gemini", "groq"})
    resp = client.generate("hi")
    assert resp.provider == "mock" and resp.text.startswith("[mock-llm]")


def test_structured_output_is_validated(config, monkeypatch):
    from pydantic import BaseModel

    class Schema(BaseModel):
        ok: bool

    calls = []
    client = _client(monkeypatch, calls, failures={"gemini"})
    assert client.generate_structured("hi", Schema).ok is True
    # A provider that is down is abandoned immediately, not retried.
    assert calls == ["gemini", "groq"]


def test_bad_json_is_retried_on_the_same_provider(config, monkeypatch):
    from pydantic import BaseModel
    from mcp.tools.llm_tools import llm_client

    class Schema(BaseModel):
        ok: bool

    monkeypatch.setenv("TEST_GEMINI_KEY", "x")
    providers.load(force=True)
    calls = []

    def flaky(self, spec, prompt, system, temperature, max_tokens, schema=None):
        calls.append(spec.provider)
        return "not json" if len(calls) == 1 else '{"ok": true}'

    monkeypatch.setattr(llm_client.LLMClient, "_call", flaky)
    llm_client.reset_clients()
    client = llm_client.get_llm_client("story", force_new=True)
    assert client.generate_structured("hi", Schema).ok is True
    assert calls == ["gemini", "gemini"]


def test_structured_output_raises_without_a_model(config):
    from pydantic import BaseModel
    from mcp.tools.llm_tools import llm_client

    class Schema(BaseModel):
        ok: bool

    llm_client.reset_clients()
    client = llm_client.get_llm_client("story", force_new=True)   # only "mock" available
    assert client.provider == "mock"
    with pytest.raises(RuntimeError, match="offline fallback"):
        client.generate_structured("hi", Schema)


def test_json_is_extracted_from_fenced_or_chatty_replies():
    from mcp.tools.llm_tools.llm_client import _extract_json
    assert _extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert _extract_json('Sure!\n{"a": 1}\nHope that helps') == '{"a": 1}'
    assert _extract_json('["x"]') == '["x"]'


def test_openai_compatible_providers_point_at_their_own_endpoints(monkeypatch):
    from mcp.tools.llm_tools import llm_client
    from shared.providers import ProviderSpec
    seen = {}

    class FakeClient:
        def __init__(self, **kwargs):
            seen.update(kwargs)
            self.chat = self

        @property
        def completions(self):
            return self

        def create(self, **kwargs):
            seen["model"] = kwargs["model"]
            seen["json_mode"] = kwargs.get("response_format")
            return type("R", (), {"choices": [type("C", (), {
                "message": type("M", (), {"content": "hello"})()})()]})()

    import openai
    monkeypatch.setattr(openai, "OpenAI", FakeClient)
    monkeypatch.setenv("GROQ_API_KEY", "gk")
    out = llm_client._openai_compatible(
        ProviderSpec(role="story", provider="groq", model="openai/gpt-oss-120b"),
        "hi", "", 0.5, 100)
    assert out == "hello"
    assert seen["base_url"] == "https://api.groq.com/openai/v1"
    assert seen["api_key"] == "gk" and seen["model"] == "openai/gpt-oss-120b"

    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")
    llm_client._openai_compatible(
        ProviderSpec(role="story", provider="ollama", model="qwen3:4b"), "hi", "", 0.5, 100)
    assert seen["base_url"] == "http://localhost:11434/v1"


def test_gemini_uses_native_structured_output(monkeypatch):
    from pydantic import BaseModel
    from mcp.tools.llm_tools import llm_client
    from shared.providers import ProviderSpec

    class Schema(BaseModel):
        ok: bool

    captured = {}

    class FakeModels:
        def generate_content(self, model, contents, config):
            captured["model"] = model
            captured["config"] = config
            return type("R", (), {"text": '{"ok": true}'})()

    class FakeClient:
        def __init__(self, api_key=None):
            captured["api_key"] = api_key
            self.models = FakeModels()

    import google.genai as genai
    monkeypatch.setattr(genai, "Client", FakeClient)
    monkeypatch.setenv("GEMINI_API_KEY", "gk")
    out = llm_client._gemini(ProviderSpec(role="story", provider="gemini",
                                          model="gemini-flash-latest"),
                             "hi", "sys", 0.5, 100, Schema)
    assert out == '{"ok": true}'
    assert captured["model"] == "gemini-flash-latest" and captured["api_key"] == "gk"
    assert captured["config"].response_schema is Schema
    assert captured["config"].response_mime_type == "application/json"


# ---- the providers CLI -----------------------------------------------------

def test_providers_command_lists_chains_and_checks_them(monkeypatch, capsys):
    import argparse
    import main
    from mcp.tool_executor import ToolExecutor
    from mcp.base_tool import ToolResult

    monkeypatch.setenv("PROVIDER_IMAGE", "placeholder")
    providers.load(force=True)

    def fake_execute(self, tool, **kwargs):
        if tool == "text.translate":
            return ToolResult(success=True, data=["Bonsoir."], metadata={"provider": "mymemory"})
        return ToolResult(success=True, data=kwargs.get("out_path"),
                          metadata={"provider": "placeholder"})

    monkeypatch.setattr(ToolExecutor, "execute", fake_execute)
    code = main.cmd_providers(argparse.Namespace(check=True))
    out = capsys.readouterr().out
    assert code == 0
    assert "story" in out and "image" in out
    assert "needs GEMINI_API_KEY" in out          # says what each alternative wants
    assert "translate    OK  via mymemory" in out
    assert "wanted placeholder, served by placeholder" in out


# ---- parallelism -----------------------------------------------------------

def test_run_jobs_keeps_order_and_runs_concurrently():
    live, peak, lock = 0, 0, threading.Lock()

    def job(i):
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        time.sleep(0.15)
        with lock:
            live -= 1
        return i

    started = time.monotonic()
    assert run_jobs([(job, (i,)) for i in range(8)], workers=4) == list(range(8))
    assert peak > 1                       # actually parallel
    assert time.monotonic() - started < 0.15 * 8 * 0.8


def test_run_jobs_with_one_worker_is_sequential():
    order = []
    jobs = [(lambda i: order.append(i) or i, (i,)) for i in range(4)]
    assert run_jobs(jobs, workers=1) == [0, 1, 2, 3]
    assert order == [0, 1, 2, 3]
