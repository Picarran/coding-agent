"""A single-agent ReAct loop driven by native tool calling.

The loop owns an explicit state machine: it transitions ``RUNNING -> DONE /
FAILED / MAX_STEPS`` based on concrete events (tool calls, final response,
errors, step limits) — never on the model merely claiming "I'm done".

Context is managed by a ``ContextManager`` (trims old tool exchanges to bound
growth), and a ``TerminationMonitor`` guards against stuck loops (repeated
actions and consecutive tool errors). Loop-level lifecycle events (LOOP_STEP,
LLM_CALL, AGENT_FINISH) are emitted through an ``EventBus``.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Callable

from src.context.context_manager import ContextManager
from src.core.events import EventBus, EventType
from src.core.models import AgentResult, AgentStatus, Message, ToolCall
from src.core.state import AgentState, StateMachine
from src.core.termination import TerminationConfig, TerminationMonitor
from src.llm.base import LLMClient, LLMResponse
from src.tools.executor import ToolExecutor

logger = logging.getLogger(__name__)

_SUMMARIZE_SYSTEM = (
    "You are compressing a coding agent's conversation history to save context. "
    "Summarize the messages below into concise bullet points, preserving ONLY: "
    "key conclusions, files modified/created, failed attempts, and unresolved issues. "
    "Output the bullet points only, with no preamble."
)


class ReactLoop:
    def __init__(
        self,
        llm: LLMClient,
        executor: ToolExecutor,
        system_prompt: str,
        max_steps: int = 20,
        llm_retries: int = 3,
        retry_sleep: float = 1.0,
        event_bus: EventBus | None = None,
        agent_id: str | None = None,
        report_tool_name: str | None = None,
        summarizer_llm: LLMClient | None = None,
        checkpoint_cb: Callable[[], None] | None = None,
        max_messages: int = 30,
        max_tokens: int = 8000,
        repeated_action_warn: int = 3,
        repeated_action_limit: int = 6,
        consecutive_error_warn: int = 3,
        consecutive_error_limit: int = 6,
    ) -> None:
        self._llm = llm
        self._summarizer_llm = summarizer_llm
        self._checkpoint_cb = checkpoint_cb
        self._executor = executor
        self._system_prompt = system_prompt
        self._llm_retries = llm_retries
        self._retry_sleep = retry_sleep
        self._bus = event_bus
        self._agent_id = agent_id
        self._report_tool_name = report_tool_name
        self._max_messages = max_messages
        self._max_tokens = max_tokens
        self._termination = TerminationConfig(
            max_steps=max_steps,
            repeated_action_warn=repeated_action_warn,
            repeated_action_limit=repeated_action_limit,
            consecutive_error_warn=consecutive_error_warn,
            consecutive_error_limit=consecutive_error_limit,
        )

    def new_context(self) -> ContextManager:
        return ContextManager(
            self._system_prompt,
            max_messages=self._max_messages,
            max_tokens=self._max_tokens,
            event_bus=self._bus,
            agent_id=self._agent_id,
            summarizer=self._summarize,
        )

    def run(self, task: str) -> AgentResult:
        """Run a single, fresh task (one-shot)."""
        return self.run_turn(self.new_context(), task)

    def run_turn(self, context: ContextManager, task: str) -> AgentResult:
        """Run one turn, continuing the given (already-started or empty) context."""
        if context.is_empty():
            context.start(task)
        else:
            context.append(Message(role="user", content=task))
        return self._run(context)

    def _run(self, context: ContextManager) -> AgentResult:
        state = StateMachine(initial=AgentState.RUNNING)
        monitor = TerminationMonitor(self._termination)
        steps = 0
        final_text = ""
        stop_reason = "done"
        blocked_reason: str | None = None
        report_args: dict | None = None

        while state.current == AgentState.RUNNING:
            steps += 1
            if self._checkpoint_cb is not None:
                self._checkpoint_cb()
            self._emit(EventType.LOOP_STEP, payload={"iteration": steps})

            if steps > self._termination.max_steps:
                logger.warning("ReAct loop hit max_steps=%d", self._termination.max_steps)
                stop_reason = "max_steps"
                self._transition(state, AgentState.MAX_STEPS)
                break

            response = self._call_llm(context.messages)
            if response is None:
                stop_reason = "llm_error"
                self._transition(state, AgentState.FAILED)
                break

            if response.tool_calls:
                report_call = self._find_report_call(response.tool_calls)
                if report_call is not None:
                    report_args = report_call.arguments
                    final_text = str(
                        report_args.get("summary")
                        or json.dumps(report_args, ensure_ascii=False)
                    )
                    self._emit(
                        EventType.PRE_TOOL_USE,
                        payload={
                            "tool": report_call.name,
                            "arguments": report_call.arguments,
                            "report": True,
                        },
                    )
                    context.append(Message(role="assistant", content=final_text))
                    stop_reason = "done"
                    self._transition(state, AgentState.DONE)
                    break
                for call in response.tool_calls:
                    monitor.record_tool_call(call)

                if monitor.should_terminate_repetition():
                    logger.warning("repeated action detected; terminating loop")
                    stop_reason = "repeated_action"
                    self._transition(state, AgentState.MAX_STEPS)
                    break

                context.append(
                    Message(
                        role="assistant",
                        content=response.content,
                        tool_calls=response.tool_calls,
                    )
                )
                for call in response.tool_calls:
                    result = self._executor.execute(call)
                    if result.permission_denied:
                        # A human rejection / policy block is an *interrupt*,
                        # not an observation: stop now and yield to the user.
                        blocked_reason = result.error or f"permission denied for {call.name}"
                        logger.warning("permission denied; interrupting loop: %s", blocked_reason)
                        stop_reason = "permission_denied"
                        self._transition(state, AgentState.BLOCKED)
                        break
                    monitor.record_tool_result(result)
                    context.append(result.to_message())

                if state.current == AgentState.BLOCKED:
                    break

                if monitor.should_terminate_consecutive_errors():
                    logger.warning("consecutive tool errors; terminating loop")
                    stop_reason = "consecutive_tool_errors"
                    self._transition(state, AgentState.FAILED)
                    break

                if monitor.should_warn_repetition():
                    context.append(
                        Message(role="system", content=self._repetition_warning(monitor))
                    )
                if monitor.should_warn_consecutive_errors():
                    context.append(
                        Message(
                            role="system",
                            content=self._consecutive_errors_warning(monitor),
                        )
                    )
                continue

            # No tool calls => final answer; record it in context for later turns.
            final_text = response.content or ""
            context.append(Message(role="assistant", content=final_text))
            stop_reason = "done"
            self._transition(state, AgentState.DONE)
            break

        status, summary = self._finalize(
            state, final_text, steps, stop_reason, monitor, blocked_reason
        )
        self._emit(
            EventType.AGENT_FINISH,
            payload={
                "final_state": state.current.value,
                "stop_reason": stop_reason,
                "steps": steps,
                **({"report": report_args} if report_args is not None else {}),
            },
            status=status.value,
        )
        return AgentResult(
            agent_name="react_agent",
            status=status,
            summary=summary,
            artifacts={
                "steps": steps,
                "final_state": state.current.value,
                "stop_reason": stop_reason,
                "message_count": len(context.messages),
                "trimmed_exchanges": context.trimmed_exchanges,
                **({"blocked_reason": blocked_reason} if blocked_reason else {}),
                **({"report": report_args} if report_args is not None else {}),
            },
        )

    def _find_report_call(self, calls: list[ToolCall]) -> ToolCall | None:
        if not self._report_tool_name:
            return None
        for call in calls:
            if call.name == self._report_tool_name:
                return call
        return None

    @staticmethod
    def _finalize(
        state, final_text, steps, stop_reason, monitor, blocked_reason: str | None = None
    ) -> tuple[AgentStatus, str]:
        if state.current == AgentState.DONE:
            return AgentStatus.SUCCESS, final_text or "(empty final answer)"
        if state.current == AgentState.BLOCKED:
            return AgentStatus.BLOCKED, blocked_reason or "Blocked: permission denied."
        if stop_reason == "repeated_action":
            return AgentStatus.FAILED, "Stopped: repeated the same tool action without progress."
        if stop_reason == "consecutive_tool_errors":
            return AgentStatus.FAILED, f"Stopped after {monitor.consecutive_errors} consecutive tool errors."
        if stop_reason == "llm_error":
            return AgentStatus.FAILED, "LLM call failed repeatedly; loop aborted."
        # max_steps
        if final_text:
            return AgentStatus.PARTIAL_SUCCESS, final_text
        return AgentStatus.FAILED, f"Stopped after {steps} steps without a final answer."

    @staticmethod
    def _repetition_warning(monitor) -> str:
        return (
            f"Warning: you have called the same tool with the same arguments "
            f"{monitor.repeated_action_count()} times in a row. If it is not "
            "making progress, change your approach."
        )

    @staticmethod
    def _consecutive_errors_warning(monitor) -> str:
        return (
            f"Warning: the last {monitor.consecutive_errors} tool calls failed. "
            "Check your tool arguments or try a different approach."
        )

    def _transition(self, state: StateMachine, new_state: AgentState) -> None:
        state.transition(new_state)

    def _emit(
        self,
        event_type: EventType,
        payload: dict | None = None,
        duration_ms: float | None = None,
        status: str | None = None,
    ) -> None:
        if self._bus is not None:
            self._bus.emit_simple(
                event_type,
                agent_id=self._agent_id,
                payload=payload,
                duration_ms=duration_ms,
                status=status,
            )

    def _call_llm(self, messages: list[Message]) -> LLMResponse | None:
        last_error: Exception | None = None
        for attempt in range(1, self._llm_retries + 1):
            start = time.monotonic()
            try:
                response = self._llm.chat(messages, tools=self._executor.tool_schemas)
                self._emit_llm_call(
                    attempt, (time.monotonic() - start) * 1000, response.usage, None
                )
                return response
            except Exception as exc:  # noqa: BLE001 - retry transient LLM errors
                last_error = exc
                self._emit_llm_call(
                    attempt, (time.monotonic() - start) * 1000, None, exc
                )
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

    def _summarize(self, messages: list[Message]) -> str:
        """LLM-summarize trimmed exchanges; fall back to '' on any failure."""
        text = "\n".join(self._render_message(m) for m in messages)
        try:
            response = (self._summarizer_llm or self._llm).chat(
                [
                    Message(role="system", content=_SUMMARIZE_SYSTEM),
                    Message(role="user", content=text),
                ]
            )
            if response and response.content:
                return response.content
        except Exception as exc:  # noqa: BLE001 - a failed summary must not break the loop
            logger.warning("context summarization failed: %s", exc)
        return ""

    @staticmethod
    def _render_message(m: Message) -> str:
        if m.role == "assistant" and m.tool_calls:
            names = ", ".join(tc.name for tc in m.tool_calls)
            return f"assistant: {m.content or ''} [tool calls: {names}]"
        if m.role == "tool":
            body = (m.content or "")[:400]
            return f"tool {m.name}: {body}"
        return f"{m.role}: {(m.content or '')[:400]}"

    def _emit_llm_call(
        self,
        attempt: int,
        duration_ms: float,
        usage: dict | None,
        error: Exception | None,
    ) -> None:
        payload: dict = {"attempt": attempt}
        if usage is not None:
            payload["total_tokens"] = usage.get("total_tokens")
            payload["prompt_tokens"] = usage.get("prompt_tokens")
            payload["completion_tokens"] = usage.get("completion_tokens")
        status = "ok"
        if error is not None:
            status = "error"
            payload["error"] = str(error)
        self._emit(EventType.LLM_CALL, payload=payload, duration_ms=duration_ms, status=status)
