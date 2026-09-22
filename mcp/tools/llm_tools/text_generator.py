"""MCP wrapper around free-text LLM generation."""
from __future__ import annotations
from mcp.base_tool import BaseTool, ToolResult
from .llm_client import get_llm_client


class TextGeneratorTool(BaseTool):
    name = "llm.text_generate"
    description = "Generate free-form text from a prompt using the configured LLM."
    category = "llm"

    def run(self, prompt: str, system: str = "", temperature: float = 0.7,
            max_tokens: int = 2000, role: str = "story", **_) -> ToolResult:
        client = get_llm_client(role)
        resp = client.generate(prompt=prompt, system=system,
                               temperature=temperature, max_tokens=max_tokens)
        return ToolResult(
            success=True,
            data=resp.text,
            metadata={"provider": resp.provider, "model": resp.model},
        )
