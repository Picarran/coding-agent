"""A single-agent ReAct loop driven by native tool calling.

The loop owns an explicit state machine: it transitions ``RUNNING -> DONE /
FAILED / MAX_STEPS`` based on concrete events (tool calls, final response,
errors, step limits) — never on the model merely claiming "I'm done".

Every step emits structured trace events through a ``Tracer`` so the run can be
rendered on the CLI now and on a web UI later without changing the loop.
"""
from __future__ import annotations

import logging
import time

from src.core.events import NullTracer, Tracer
from src.core.models import AgentResult, AgentStatus, Message, ToolCall
from src.core.state import AgentState, StateMachine
from src.llm.base import LLMClient, LLMResponse
from src.tools.executor import ToolExecutor

logger = logging.getLogger(__name__)


class ReactLoop:
    def __init__(
        self,
        llm: LLMClient,
        executor: ToolExecutor,
        system_prompt: str,
        max_steps: int = 20,
        llm_retries: int = 3,
        retry_sleep: float = 1.0,
        tracer: Tracer | None = None,
    ) -> None:
        self._llm = llm
        self._executor = executor
        self._system_prompt = system_prompt
        self._max_steps = max_steps
        self._llm_retries = llm_retries
        self._retry_sleep = retry_sleep
        self._tracer: Tracer = tracer if tracer is not None else NullTracer()

    def run(self, task: str) -> AgentResult:
        state = StateMachine(initial=AgentState.RUNNING)
        messages: list[Message] = [
            Message(role="system", content=self._system_prompt),
            Message(role="user", content=task),
        ]
        steps = 0
        final_text = ""

        while state.current == AgentState.RUNNING:
            steps += 1
            self._tracer.on_step(steps)

            if steps > self._max_steps:
                logger.warning("ReAct loop hit max_steps=%d", self._max_steps)
                self._transition(state, AgentState.MAX_STEPS)
                break

            response = self._call_llm(messages)
            if response is None:
                self._transition(state, AgentState.FAILED)
                break

            if response.tool_calls:
                logger.info(
                    "step %d: model requested %d tool call(s)",
                    steps,
                    len(response.tool_calls),
                )
                messages.append(
                    Message(
                        role="assistant",
                        content=response.content,
                        tool_calls=response.tool_calls,
                    )
                )
                for call in response.tool_calls:
                    self._tracer.on_tool_call(call)
                    result = self._executor.execute(call)
                    logger.info("tool %s -> error=%s", call.name, result.error)
                    self._tracer.on_tool_result(result)
                    messages.append(result.to_message())
                continue

            # No tool calls => final answer.
            final_text = response.content or ""
            self._transition(state, AgentState.DONE)
            break

        if state.current == AgentState.DONE:
            status = AgentStatus.SUCCESS
            summary = final_text or "(empty final answer)"
        elif state.current == AgentState.MAX_STEPS:
            status = AgentStatus.PARTIAL_SUCCESS if final_text else AgentStatus.FAILED
            summary = final_text or f"Stopped after {steps} steps without a final answer."
        else:
            status = AgentStatus.FAILED
            summary = "LLM call failed repeatedly; loop aborted."

        return AgentResult(
            agent_name="react_agent",
            status=status,
            summary=summary,
            artifacts={
                "steps": steps,
                "final_state": state.current.value,
                "message_count": len(messages),
            },
        )

    def _transition(self, state: StateMachine, new_state: AgentState) -> None:
        old_state = state.current
        state.transition(new_state)
        self._tracer.on_state_transition(old_state, new_state)

    def _call_llm(self, messages: list[Message]) -> LLMResponse | None:
        last_error: Exception | None = None
        for attempt in range(1, self._llm_retries + 1):
            try:
                return self._llm.chat(messages, tools=self._executor.tool_schemas)
            except Exception as exc:  # noqa: BLE001 - retry transient LLM errors
                last_error = exc
                self._tracer.on_llm_error(attempt, exc)
                logger.warning(
                    "LLM call attempt %d/%d failed: %s",
                    attempt,
                    self._llm_retries,
                    exc,
                )
                if attempt < self._llm_retries:
                    time.sleep(self._retry_sleep * attempt)
        logger.error("LLM call failed after %d attempts: %s", self._llm_retries, last_error)
        return None
