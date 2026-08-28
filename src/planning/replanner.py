"""Replanner: revises the remaining (incomplete) part of a plan after a failure.

Only the incomplete portion is regenerated; completed steps (and their results)
are preserved, matching the "replan minimally" requirement.
"""
from __future__ import annotations

import logging

from src.core.models import Message
from src.llm.base import LLMClient
from src.planning.planner import SUBMIT_PLAN_TOOL, build_plan, extract_plan_arguments
from src.planning.task_plan import PlanStep, TaskPlan

logger = logging.getLogger(__name__)

REPLANNER_SYSTEM = (
    "You revise a partially-completed coding task plan. Keep completed steps as they "
    "are; propose ONLY the remaining steps (revised if needed) to recover from the "
    "reported problem. Submit them with submit_plan."
)


class Replanner:
    def __init__(self, llm: LLMClient, max_retries: int = 3, environment: str = "") -> None:
        self._llm = llm
        self._max_retries = max_retries
        self._system = REPLANNER_SYSTEM + ("\n\n" + environment if environment else "")

    def replan(self, plan: TaskPlan, reason: str) -> TaskPlan:
        messages = [
            Message(role="system", content=self._system),
            Message(role="user", content=self._build_prompt(plan, reason)),
        ]
        for attempt in range(self._max_retries):
            response = self._llm.chat(messages, tools=SUBMIT_PLAN_TOOL)
            args = extract_plan_arguments(response)
            if args is not None:
                new_steps = build_plan(args).steps
                return TaskPlan(goal=plan.goal, steps=plan.completed_steps() + new_steps)
            logger.warning("replanner attempt %d returned no submit_plan", attempt + 1)
        logger.warning("replanner failed; adding a single retry step")
        retry = PlanStep(
            id=f"retry-{len(plan.steps) + 1}",
            description=f"Recover from: {reason}",
        )
        return TaskPlan(goal=plan.goal, steps=plan.completed_steps() + [retry])

    @staticmethod
    def _build_prompt(plan: TaskPlan, reason: str) -> str:
        lines = [f"Overall goal: {plan.goal}", f"Problem: {reason}"]
        completed = plan.completed_steps()
        if completed:
            lines.append("Completed steps (keep as-is):")
            for s in completed:
                summary = s.result.summary if s.result else s.description
                lines.append(f"- {s.id}: {summary}")
        pending = plan.pending_steps()
        if pending:
            lines.append("Remaining steps (you may revise these):")
            for s in pending:
                lines.append(f"- {s.id}: {s.description}")
        lines.append("Return the revised REMAINING steps only, with fresh ids.")
        return "\n".join(lines)
