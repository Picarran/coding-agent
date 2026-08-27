"""Main Agent: the task-level Plan-and-Execute supervisor.

It sits above the action layer (the ReAct loop). For a user task it:
  1. PLANs a list of steps (Planner),
  2. DISPATCHes each runnable step to a worker (the ReAct loop),
  3. OBSERVEs the worker's structured AgentResult,
  4. REPLANs only the incomplete part when a step fails,
  5. VERIFIEs and returns a final result.

The loop is driven by an explicit state machine (MainAgentState), not by asking
the model to declare "done". Completed steps and their results are preserved
across replans.
"""
from __future__ import annotations

import logging
from typing import Callable, Protocol

from src.core.models import AgentResult, AgentStatus
from src.core.state import MainAgentState, StateMachine
from src.planning.planner import Planner
from src.planning.replanner import Replanner
from src.planning.task_plan import PlanStep, PlanStepStatus, TaskPlan

logger = logging.getLogger(__name__)


class Worker(Protocol):
    def run(self, task: str) -> AgentResult: ...


class MainAgent:
    def __init__(
        self,
        planner: Planner,
        replanner: Replanner,
        worker: Worker,
        max_replans: int = 3,
        on_progress: Callable[[str], None] | None = None,
    ) -> None:
        self._planner = planner
        self._replanner = replanner
        self._worker = worker
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
            self._progress(f"  - {s.id}: {s.description}{deps}")

        state.transition(MainAgentState.EXECUTING)
        replans_left = self._max_replans
        replans_used = 0

        while not plan.is_complete():
            step = plan.next_runnable_step()
            if step is None:
                # Pending steps exist but none are runnable (dependency deadlock).
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
            subtask = self._build_subtask(plan.goal, step, plan.completed_steps())
            self._progress(f"Dispatch {step.id}: {step.description}")
            state.transition(MainAgentState.EXECUTING)
            result = self._worker.run(subtask)
            state.transition(MainAgentState.OBSERVING)

            if result.status == AgentStatus.SUCCESS:
                step.status = PlanStepStatus.COMPLETED
                step.result = result
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
        state.transition(MainAgentState.COMPLETED)
        return self._finalize(state, plan, "completed", replans_used)

    @staticmethod
    def _build_subtask(goal: str, step: PlanStep, completed_steps: list) -> str:
        parts = [f"Overall goal: {goal}", f"Your task: {step.description}"]
        if completed_steps:
            ctx = "\n".join(
                f"- {s.id}: {s.result.summary if s.result else s.description}"
                for s in completed_steps
            )
            parts.append(f"Context from completed steps:\n{ctx}")
        parts.append(
            "Complete this step using your tools. Finish with a concise summary of what you did."
        )
        return "\n\n".join(parts)

    def _progress(self, message: str) -> None:
        logger.info("main_agent: %s", message)
        if self._on_progress:
            self._on_progress(message)

    def _finalize(self, state, plan, reason, replans_used) -> AgentResult:
        succeeded = state.current == MainAgentState.COMPLETED
        status = AgentStatus.SUCCESS if succeeded else AgentStatus.FAILED
        lines = []
        for s in plan.steps:
            summary = s.result.summary if s.result else s.description
            lines.append(f"{s.id} [{s.status.value}]: {summary}")
        header = "All steps completed.\n" if succeeded else f"Failed: {reason}.\n"
        return AgentResult(
            agent_name="main_agent",
            status=status,
            summary=header + "\n".join(lines),
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
