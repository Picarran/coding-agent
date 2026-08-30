"""DeepSeek model client backed by the OpenAI-compatible API.

Only this module knows the DeepSeek request details. Every other layer talks
to the ``LLMClient`` interface.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Iterator

from openai import OpenAI

from src.core.models import Message, ToolCall
from src.llm.base import LLMClient, LLMResponse, StreamChunk

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

    def chat_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[StreamChunk]:
        """Stream the response token-by-token, assembling tool calls along the way.

        Yields one ``StreamChunk`` per content token delta, then a terminal chunk
        carrying the fully-assembled tool calls, finish_reason and usage.
        """
        stream = self._client.chat.completions.create(
            model=self._model,
            messages=[m.to_openai() for m in messages],
            tools=tools,
            temperature=self._temperature,
            stream=True,
            stream_options={"include_usage": True},
        )
        content_parts: list[str] = []
        tool_slots: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        usage: dict[str, int] | None = None
        for chunk in stream:
            # The final chunk (with include_usage) carries token usage and may
            # have empty choices.
            if getattr(chunk, "usage", None) is not None:
                usage = {
                    "prompt_tokens": chunk.usage.prompt_tokens,
                    "completion_tokens": chunk.usage.completion_tokens,
                    "total_tokens": chunk.usage.total_tokens,
                }
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            if delta and delta.content:
                content_parts.append(delta.content)
                yield StreamChunk(content=delta.content)
            if delta and delta.tool_calls:
                for tc in delta.tool_calls:
                    slot = tool_slots.setdefault(
                        tc.index, {"id": "", "name": "", "arguments": ""}
                    )
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function and tc.function.name:
                        slot["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        slot["arguments"] += tc.function.arguments
            if choice.finish_reason:
                finish_reason = choice.finish_reason
        tool_calls = [
            ToolCall(
                id=slot["id"],
                name=slot["name"],
                arguments=self._parse_arguments(slot["arguments"]),
                arguments_json=slot["arguments"] or "{}",
            )
            for slot in tool_slots.values()
        ]
        yield StreamChunk(
            content=None,
            tool_calls=tool_calls or None,
            finish_reason=finish_reason,
            usage=usage,
        )

    @staticmethod
    def _parse_arguments(raw: str | None) -> dict[str, Any]:
        try:
            return json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse tool arguments: %s", exc)
            return {}
