"""MCP wrapper around schema-validated structured JSON generation."""
from __future__ import annotations
from typing import Type

from pydantic import BaseModel

from mcp.base_tool import BaseTool, ToolResult
from .llm_client import get_llm_client


class JsonStructurerTool(BaseTool):
    name = "llm.json_structure"
    description = "Generate a JSON object that validates against a Pydantic schema."
    category = "llm"

    def run(self, prompt: str, schema: Type[BaseModel], system: str = "",
            temperature: float = 0.5, role: str = "story", **_) -> ToolResult:
        client = get_llm_client(role)
        if client.provider == "mock":
            return ToolResult(success=False, error="mock provider — caller should use template fallback")
        try:
            obj = client.generate_structured(prompt=prompt, schema=schema,
                                             system=system, temperature=temperature)
            return ToolResult(success=True, data=obj,
                              metadata={"provider": client.provider, "model": client.model})
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False, error=str(e))
