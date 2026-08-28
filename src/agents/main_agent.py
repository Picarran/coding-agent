"""Main Agent: the task-level Plan-and-Execute supervisor.

It sits above the action layer. For a user task it:
  1. PLANs a list of steps (Planner),
  2. DISPATCHes each runnable step to the assigned SubAgent,
  3. OBSERVEs the SubAgent's structured AgentResult and accumulates a WorkspaceContext,
  4. REPLANs only the incomplete part when a step fails,
  5. SYNTHESIZEs a natural-language final answer from the step reports.

The loop is driven by an explicit state machine (MainAgentState), not by asking
the model to declare "done". Completed steps and their results are preserved
across replans.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Protocol

from src.context.workspace_context import WorkspaceContext
from src.core.events import EventBus, EventType
from src.core.models import AgentResult, AgentStatus, Message
from src.core.state import MainAgentState, StateMachine
from src.llm.base import LLMClient
from src.planning.planner import Planner
from src.planning.replanner import Replanner
from src.planning.task_plan import PlanStep, PlanStepStatus, TaskPlan

logger = logging.getLogger(__name__)

FINAL_SYNTHESIS_SYSTEM = (
    "You are the final responder of a coding agent. Based on the completed steps "
    "below, write a concise, natural-language answer to the user's original request. "
    "Do not mention internal step ids or orchestration."
)

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _answer_language(text: str) -> str:
    """Determine the language the final answer should use, from the user's input."""
    if _CJK_RE.search(text or ""):
        return "Answer in Chinese (中文)."
    return "Answer in English."


class Worker(Protocol):
    def run(self, task: str) -> AgentResult: ...


class MainAgent:
    def __init__(
        self,
        planner: Planner,
        replanner: Replanner,
        agents: dict[str, Worker],
        llm: LLMClient | None = None,
        default_agent: str = "coding",
        max_replans: int = 3,
        event_bus: EventBus | None = None,
        agent_id: str = "main_agent",
    ) -> None:
        self._planner = planner
        self._replanner = replanner
        self._agents = agents
        self._llm = llm
        self._default_agent = default_agent
        self._max_replans = max_replans
        self._bus = event_bus
        self._agent_id = agent_id

    def run(self, task: str) -> AgentResult:
        state = StateMachine(initial=MainAgentState.IDLE)
        state.transition(MainAgentState.PLANNING)
        self._emit(EventType.AGENT_START, payload={"task": task})
        plan = self._planner.plan(task)
        self._emit(
            EventType.PLAN_CREATED,
            payload={"steps": [self._step_dict(s) for s in plan.steps]},
        )

        state.transition(MainAgentState.EXECUTING)
        workspace = WorkspaceContext()
        replans_left = self._max_replans
        replans_used = 0

        while not plan.is_complete():
            step = plan.next_runnable_step()
            if step is None:
                if replans_left > 0:
                    replans_left -= 1
                    replans_used += 1
                    state.transition(MainAgentState.REPLANNING)
                    self._emit(
                        EventType.REPLAN_START,
                        payload={"reason": "no runnable step (dependency issue)", "replans_left": replans_left},
                    )
                    plan = self._replanner.replan(plan, "no runnable step (dependency issue)")
                    self._emit(EventType.REPLAN_FINISH, payload={"replans_left": replans_left})
                    state.transition(MainAgentState.EXECUTING)
                    continue
                state.transition(MainAgentState.FAILED)
                return self._finalize(state, plan, "no runnable step", replans_used)

            step.status = PlanStepStatus.RUNNING
            state.transition(MainAgentState.DISPATCHING)
            worker = self._agents.get(step.assigned_agent) or self._agents.get(
                self._default_agent
            )
            subtask = self._build_subtask(plan.goal, step, plan.completed_steps(), workspace)
            self._emit(
                EventType.STEP_START,
                payload={
                    "step_id": step.id,
                    "description": step.description,
                    "assigned_agent": step.assigned_agent,
                },
            )
            state.transition(MainAgentState.EXECUTING)
            self._emit(
                EventType.SUBAGENT_START,
                agent_id=step.assigned_agent,
                payload={"step_id": step.id},
            )
            result = worker.run(subtask)
            self._emit(
                EventType.SUBAGENT_FINISH,
                agent_id=step.assigned_agent,
                payload={
                    "step_id": step.id,
                    "status": result.status.value,
                    "summary": result.summary,
                },
            )
            state.transition(MainAgentState.OBSERVING)

            if result.status == AgentStatus.SUCCESS:
                step.status = PlanStepStatus.COMPLETED
                step.result = result
                workspace.record(result)
            elif result.status == AgentStatus.BLOCKED:
                # A SubAgent was blocked (permission denied / user rejected):
                # stop the whole plan and yield control back to the user.
                step.status = PlanStepStatus.BLOCKED
                step.result = result
                state.transition(MainAgentState.BLOCKED)
                return self._finalize(
                    state, plan, f"step {step.id} was blocked: {result.summary}", replans_used
                )
            else:
                step.status = PlanStepStatus.FAILED
                step.result = result
                if replans_left > 0:
                    replans_left -= 1
                    replans_used += 1
                    state.transition(MainAgentState.REPLANNING)
                    self._emit(
                        EventType.REPLAN_START,
                        payload={
                            "reason": f"step {step.id} failed: {result.summary}",
                            "replans_left": replans_left,
                        },
                    )
                    plan = self._replanner.replan(
                        plan, f"step {step.id} failed: {result.summary}"
                    )
                    self._emit(EventType.REPLAN_FINISH, payload={"replans_left": replans_left})
                    state.transition(MainAgentState.EXECUTING)
                else:
                    state.transition(MainAgentState.FAILED)
                    return self._finalize(state, plan, "max replans exceeded", replans_used)

        state.transition(MainAgentState.VERIFYING)
        final_answer = self._synthesize(task, plan.steps)
        state.transition(MainAgentState.COMPLETED)
        return self._finalize(state, plan, "completed", replans_used, final_answer)

    @staticmethod
    def _step_dict(s: PlanStep) -> dict:
        return {
            "id": s.id,
            "description": s.description,
            "assigned_agent": s.assigned_agent,
            "dependencies": list(s.dependencies),
        }

    @staticmethod
    def _build_subtask(goal: str, step: PlanStep, completed_steps: list, workspace: WorkspaceContext) -> str:
        parts = [f"Overall goal: {goal}", f"Your task: {step.description}"]
        ws = workspace.render()
        if ws:
            parts.append(ws)
        if completed_steps:
            ctx = "\n".join(
                f"- {s.id}: {s.result.summary if s.result else s.description}"
                for s in completed_steps
            )
            parts.append(f"Completed steps:\n{ctx}")
        parts.append("Complete this step using your tools, then submit your report with submit_report.")
        return "\n\n".join(parts)

    def _synthesize(self, task: str, steps: list[PlanStep]) -> str:
        if self._llm is None:
            return self._fallback_summary(steps)
        language = _answer_language(task)
        messages = [
            Message(role="system", content=FINAL_SYNTHESIS_SYSTEM),
            Message(
                role="user",
                content=(
                    f"Original request: {task}\n\n{language}\n\n"
                    f"Step reports:\n{self._collect_reports(steps)}"
                ),
            ),
        ]
        try:
            response = self._llm.chat(messages)
            if response and response.content:
                return response.content
        except Exception as exc:  # noqa: BLE001 - fall back to concatenation
            logger.warning("final answer synthesis failed: %s", exc)
        return self._fallback_summary(steps)

    @staticmethod
    def _collect_reports(steps: list[PlanStep]) -> str:
        parts: list[str] = []
        for s in steps:
            if s.result and s.result.artifacts.get("report"):
                report = json.dumps(s.result.artifacts["report"], ensure_ascii=False)
                parts.append(f"- {s.description}\n  {report}")
            elif s.result and s.result.summary:
                parts.append(f"- {s.description}\n  {s.result.summary}")
        return "\n".join(parts)

    @staticmethod
    def _fallback_summary(steps: list[PlanStep]) -> str:
        lines = [
            s.result.summary if s.result and s.result.summary else s.description
            for s in steps
        ]
        return "\n".join(f"- {line}" for line in lines)

    def _emit(
        self,
        event_type: EventType,
        agent_id: str | None = None,
        payload: dict | None = None,
        duration_ms: float | None = None,
        status: str | None = None,
    ) -> None:
        if self._bus is not None:
            self._bus.emit_simple(
                event_type,
                agent_id=agent_id or self._agent_id,
                payload=payload,
                duration_ms=duration_ms,
                status=status,
            )

    def _finalize(self, state, plan, reason, replans_used, final_answer: str | None = None) -> AgentResult:
        if state.current == MainAgentState.COMPLETED:
            status = AgentStatus.SUCCESS
            summary = final_answer or self._fallback_summary(plan.steps)
        elif state.current == MainAgentState.BLOCKED:
            status = AgentStatus.BLOCKED
            summary = f"Blocked: {reason}"
        else:
            status = AgentStatus.FAILED
            lines = [
                f"{s.id} [{s.status.value}]: {s.result.summary if s.result else s.description}"
                for s in plan.steps
            ]
            summary = f"Failed: {reason}.\n" + "\n".join(lines)
        self._emit(
            EventType.AGENT_FINISH,
            payload={"final_state": state.current.value, "replans": replans_used},
            status=status.value,
        )
        return AgentResult(
            agent_name="main_agent",
            status=status,
            summary=summary,
            artifacts={
                "final_state": state.current.value,
                "replans": replans_used,
                "plan": [
                    {
                        "id": s.id,
                        "description": s.description,
                        "status": s.status.value,
                        "summary": s.result.summary if s.result else None,
                    }
                    for s in plan.steps
                ],
            },
        )
