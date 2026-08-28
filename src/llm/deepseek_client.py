"""DeepSeek model client backed by the OpenAI-compatible API.

Only this module knows the DeepSeek request details. Every other layer talks
to the ``LLMClient`` interface.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from openai import OpenAI

from src.core.models import Message, ToolCall
from src.llm.base import LLMClient, LLMResponse

logger = logging.getLogger(__name__)


class DeepSeekClient(LLMClient):
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        temperature: float = 0.0,
    ) -> None:
        api_key = api_key or os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError(
                "DEEPSEEK_API_KEY is not set. Create a .env file from .env.example "
                "and fill in your key."
            )
        self._model = model or os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
        base_url = base_url or os.environ.get(
            "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
        )
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._temperature = temperature

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[m.to_openai() for m in messages],
            tools=tools,
            temperature=self._temperature,
        )
        choice = response.choices[0]
        message = choice.message
        tool_calls = [
            ToolCall(
                id=tc.id,
                name=tc.function.name,
                arguments=self._parse_arguments(tc.function.arguments),
                arguments_json=tc.function.arguments or "{}",
            )
            for tc in (message.tool_calls or [])
        ]
        usage = None
        if getattr(response, "usage", None) is not None:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }
        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls or None,
            finish_reason=choice.finish_reason,
            usage=usage,
        )

    @staticmethod
    def _parse_arguments(raw: str | None) -> dict[str, Any]:
        try:
            return json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse tool arguments: %s", exc)
            return {}
