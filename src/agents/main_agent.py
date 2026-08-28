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
from typing import Callable, Protocol

from src.context.workspace_context import WorkspaceContext
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
    "Answer in the user's language. Do not mention internal step ids or orchestration."
)


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
        on_progress: Callable[[str], None] | None = None,
    ) -> None:
        self._planner = planner
        self._replanner = replanner
        self._agents = agents
        self._llm = llm
        self._default_agent = default_agent
        self._max_replans = max_replans
        self._on_progress = on_progress

    def run(self, task: str) -> AgentResult:
        state = StateMachine(initial=MainAgentState.IDLE)
        state.transition(MainAgentState.PLANNING)
        self._progress(f"Goal: {task}")
        plan = self._planner.plan(task)
        self._progress(f"Plan ({len(plan.steps)} step(s)):")
        for s in plan.steps:
            deps = f" (after {', '.join(s.dependencies)})" if s.dependencies else ""
            self._progress(f"  - {s.id} [{s.assigned_agent}]: {s.description}{deps}")

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
                    plan = self._replanner.replan(plan, "no runnable step (dependency issue)")
                    self._progress(f"Replanned ({replans_left} replans left)")
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
            self._progress(f"Dispatch {step.id} [{step.assigned_agent}]: {step.description}")
            state.transition(MainAgentState.EXECUTING)
            result = worker.run(subtask)
            state.transition(MainAgentState.OBSERVING)

            if result.status == AgentStatus.SUCCESS:
                step.status = PlanStepStatus.COMPLETED
                step.result = result
                workspace.record(result)
                self._progress(f"  -> {step.id} COMPLETED: {result.summary}")
            else:
                step.status = PlanStepStatus.FAILED
                step.result = result
                self._progress(f"  -> {step.id} FAILED: {result.summary}")
                if replans_left > 0:
                    replans_left -= 1
                    replans_used += 1
                    state.transition(MainAgentState.REPLANNING)
                    plan = self._replanner.replan(
                        plan, f"step {step.id} failed: {result.summary}"
                    )
                    self._progress(f"Replanned ({replans_left} replans left)")
                    state.transition(MainAgentState.EXECUTING)
                else:
                    state.transition(MainAgentState.FAILED)
                    return self._finalize(state, plan, "max replans exceeded", replans_used)

        state.transition(MainAgentState.VERIFYING)
        final_answer = self._synthesize(plan.goal, plan.steps)
        state.transition(MainAgentState.COMPLETED)
        return self._finalize(state, plan, "completed", replans_used, final_answer)

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

    def _synthesize(self, goal: str, steps: list[PlanStep]) -> str:
        if self._llm is None:
            return self._fallback_summary(steps)
        messages = [
            Message(role="system", content=FINAL_SYNTHESIS_SYSTEM),
            Message(
                role="user",
                content=f"Original request: {goal}\n\nStep reports:\n{self._collect_reports(steps)}",
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

    def _progress(self, message: str) -> None:
        logger.info("main_agent: %s", message)
        if self._on_progress:
            self._on_progress(message)

    def _finalize(self, state, plan, reason, replans_used, final_answer: str | None = None) -> AgentResult:
        succeeded = state.current == MainAgentState.COMPLETED
        status = AgentStatus.SUCCESS if succeeded else AgentStatus.FAILED
        if succeeded:
            summary = final_answer or self._fallback_summary(plan.steps)
        else:
            lines = [
                f"{s.id} [{s.status.value}]: {s.result.summary if s.result else s.description}"
                for s in plan.steps
            ]
            summary = f"Failed: {reason}.\n" + "\n".join(lines)
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
