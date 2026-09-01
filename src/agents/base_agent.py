"""Base SubAgent: a ReAct loop with a role, restricted tools, and a structured report.

Each SubAgent is the action layer specialized for one job. It returns a
structured report (via a ``submit_report`` terminal tool) instead of free-form
text, so the Main Agent receives structured artifacts rather than raw history.

The FAST single agent reuses this class with ``synthesize_final=True``: after the
loop finishes it re-summarizes the raw result into a concise, user-language
answer (mirroring ``MainAgent._synthesize``). Without this, a terse model
narration such as "All 4 tests pass." would be surfaced verbatim, in the model's
own language, instead of a proper reply in the user's language.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

from src.core.events import EventBus, EventType
from src.core.models import AgentResult, AgentStatus, Message
from src.llm.base import LLMClient
from src.loops.react_loop import ReactLoop
from src.skills.registry import Skill, SkillMatcher, SkillRegistry
from src.tools.definitions import ToolDefinition
from src.tools.executor import ToolExecutor
from src.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

REPORT_TOOL_NAME = "submit_report"

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

_FINAL_SYNTHESIS_SYSTEM = (
    "You are the final responder of a coding agent. Based on the agent's work "
    "summary below, write a concise, natural-language answer to the user's "
    "original request. Do not mention internal tool calls, step ids, or orchestration."
)


def _answer_language(text: str) -> str:
    """Pick the language for the final answer, from the user's input."""
    if _CJK_RE.search(text or ""):
        return "Answer in Chinese (中文)."
    return "Answer in English."


def make_report_tool(fields: dict[str, Any]) -> ToolDefinition:
    properties = {
        "summary": {
            "type": "string",
            "description": "One-sentence summary of what you did and found.",
        },
    }
    for name, spec in fields.items():
        properties[name] = spec
    return ToolDefinition(
        name=REPORT_TOOL_NAME,
        description="Submit your structured report and finish the task.",
        parameters={"type": "object", "properties": properties, "required": ["summary"]},
        func=lambda **kwargs: "report submitted",
    )


class BaseAgent:
    def __init__(
        self,
        name: str,
        llm: LLMClient,
        registry: ToolRegistry,
        system_prompt: str,
        report_fields: dict[str, Any],
        event_bus: EventBus | None = None,
        max_steps: int = 20,
        permission_checker: Any = None,
        summarizer_llm: LLMClient | None = None,
        checkpoint_cb: Callable[[], None] | None = None,
        skill_registry: SkillRegistry | None = None,
        streaming: bool = False,
        synthesize_final: bool = False,
        synthesis_llm: LLMClient | None = None,
    ) -> None:
        self._name = name
        self._skill_registry = skill_registry
        self._skill_matcher = SkillMatcher(skill_registry) if skill_registry else None
        self._synthesize_final = synthesize_final
        self._synthesis_llm = synthesis_llm or llm
        self._streaming = streaming
        self._bus = event_bus
        registry.register(make_report_tool(report_fields))
        executor = ToolExecutor(
            registry,
            permission_checker=permission_checker,
            event_bus=event_bus,
            agent_id=name,
        )
        self._loop = ReactLoop(
            llm,
            executor,
            system_prompt,
            event_bus=event_bus,
            agent_id=name,
            report_tool_name=REPORT_TOOL_NAME,
            max_steps=max_steps,
            summarizer_llm=summarizer_llm,
            checkpoint_cb=checkpoint_cb,
            # When a final synthesis follows, keep the loop non-streaming so its
            # narration does not leak into the answer panel; the synthesis below
            # streams the polished answer instead.
            streaming=(streaming and not synthesize_final),
        )

    def run(self, subtask: str, forced_skill: str | None = None) -> AgentResult:
        result = self._loop.run(self._inject_skill_guidance(subtask, forced_skill))
        result.agent_name = self._name
        if self._synthesize_final and result.status == AgentStatus.SUCCESS:
            summary = self._synthesize_final_answer(subtask, result.summary)
            if summary:
                result.summary = summary
                result.artifacts["synthesized"] = True
        return result

    def _synthesize_final_answer(self, task: str, raw_summary: str) -> str:
        if not raw_summary or raw_summary == "(empty final answer)":
            return ""
        language = _answer_language(task)
        messages = [
            Message(role="system", content=_FINAL_SYNTHESIS_SYSTEM),
            Message(
                role="user",
                content=(
                    f"Original request: {task}\n\n{language}\n\n"
                    f"Agent's work summary:\n{raw_summary}"
                ),
            ),
        ]
        try:
            stream_method = getattr(self._synthesis_llm, "chat_stream", None)
            if self._streaming and stream_method is not None:
                parts: list[str] = []
                for chunk in stream_method(messages):
                    if chunk.content:
                        parts.append(chunk.content)
                        self._emit(EventType.STREAM_DELTA, payload={"text": chunk.content})
                if parts:
                    return "".join(parts)
            else:
                response = self._synthesis_llm.chat(messages)
                if response and response.content:
                    return response.content
        except Exception as exc:  # noqa: BLE001 - fall back to the raw summary
            logger.warning("final answer synthesis failed: %s", exc)
        return ""

    def _emit(self, event_type: EventType, payload: dict | None = None) -> None:
        if self._bus is not None:
            self._bus.emit_simple(event_type, agent_id=self._name, payload=payload)

    def _inject_skill_guidance(self, task: str, forced_skill: str | None = None) -> str:
        """Single-agent path: prepend a matched (or explicitly forced) skill's guidance."""
        skill: Skill | None = None
        if forced_skill and self._skill_registry is not None:
            skill = self._skill_registry.get(forced_skill)
        elif self._skill_matcher is not None:
            skill = self._skill_matcher.match(task)
        if skill is None or not skill.guidance():
            return task
        return f"{task}\n\nSkill ({skill.name}) guidance:\n{skill.guidance()}"
