"""LLM tools — text generation, structured JSON output."""
from .llm_client import LLMClient, get_llm_client
from .text_generator import TextGeneratorTool
from .json_structurer import JsonStructurerTool
from .translate_tool import TranslateTool
from mcp.tool_registry import registry

registry.register(TextGeneratorTool())
registry.register(JsonStructurerTool())
registry.register(TranslateTool())

__all__ = ["LLMClient", "get_llm_client", "TextGeneratorTool", "JsonStructurerTool",
           "TranslateTool"]
