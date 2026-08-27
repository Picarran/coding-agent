"""Planner: turns a user task into a structured TaskPlan via the LLM.

The plan is returned through native tool calling (a ``submit_plan`` tool), so the
plan structure is explicit and parseable rather than free-form text.
"""
from __future__ import annotations

import logging
from typing import Any

from src.core.models import Message
from src.llm.base import LLMClient
from src.planning.task_plan import PlanStep, TaskPlan

logger = logging.getLogger(__name__)

SUBMIT_PLAN_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "submit_plan",
            "description": "Submit a structured plan as an ordered list of steps.",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "One-sentence goal of the plan."},
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "description": "Short unique step id, e.g. 'step-1'."},
                                "description": {"type": "string", "description": "What this step does."},
                                "dependencies": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Ids of steps that must be completed first.",
                                },
                            },
                            "required": ["id", "description"],
                        },
                    },
                },
                "required": ["goal", "steps"],
            },
        },
    }
]

PLANNER_SYSTEM = (
    "You are a task planner for a coding agent. Break the user's task into a small, "
    "ordered list of concrete, verifiable steps (usually 2-6). Each step is a clear "
    "instruction the agent can carry out with its tools. Use dependencies only when a "
    "step truly must wait for another. Submit your plan with submit_plan."
)


class Planner:
    def __init__(self, llm: LLMClient, max_retries: int = 3) -> None:
        self._llm = llm
        self._max_retries = max_retries

    def plan(self, task: str) -> TaskPlan:
        messages = [
            Message(role="system", content=PLANNER_SYSTEM),
            Message(role="user", content=task),
        ]
        for attempt in range(self._max_retries):
            response = self._llm.chat(messages, tools=SUBMIT_PLAN_TOOL)
            args = extract_plan_arguments(response)
            if args is not None:
                return build_plan(args)
            logger.warning("planner attempt %d returned no submit_plan", attempt + 1)
        logger.warning("planner failed to produce a plan; falling back to a single step")
        return TaskPlan(goal=task, steps=[PlanStep(id="step-1", description=task)])


def extract_plan_arguments(response: Any) -> dict[str, Any] | None:
    if response is None or not getattr(response, "tool_calls", None):
        return None
    for call in response.tool_calls:
        if call.name == "submit_plan":
            return call.arguments
    return None


def build_plan(args: dict[str, Any]) -> TaskPlan:
    goal = str(args.get("goal") or "")
    steps: list[PlanStep] = []
    for i, s in enumerate(args.get("steps") or []):
        if not isinstance(s, dict):
            continue
        steps.append(
            PlanStep(
                id=str(s.get("id") or f"step-{i + 1}"),
                description=str(s.get("description") or ""),
                dependencies=[str(d) for d in (s.get("dependencies") or [])],
            )
        )
    if not steps:
        steps = [PlanStep(id="step-1", description=goal or "Complete the task")]
    return TaskPlan(goal=goal, steps=steps)
