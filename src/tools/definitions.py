"""Tool definition: name, description, JSON-schema parameters, and callable."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    func: Callable[..., Any]

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
