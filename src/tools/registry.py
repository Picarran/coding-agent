"""Tool registry: the single source of truth for available tools."""
from __future__ import annotations

from src.tools.definitions import ToolDefinition


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def tool_schemas(self) -> list[dict]:
        return [tool.to_openai() for tool in self._tools.values()]
