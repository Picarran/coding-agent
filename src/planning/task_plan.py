"""Task plan data structures for the Plan-and-Execute layer."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from src.core.models import AgentResult


class PlanStepStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"


@dataclass
class PlanStep:
    id: str
    description: str
    assigned_agent: str = "coding"
    status: PlanStepStatus = PlanStepStatus.PENDING
    dependencies: list[str] = field(default_factory=list)
    result: AgentResult | None = None


@dataclass
class TaskPlan:
    goal: str
    steps: list[PlanStep]

    def next_runnable_step(self) -> PlanStep | None:
        """First PENDING step whose dependencies are all COMPLETED."""
        completed = {s.id for s in self.steps if s.status == PlanStepStatus.COMPLETED}
        for step in self.steps:
            if step.status != PlanStepStatus.PENDING:
                continue
            if all(dep in completed for dep in step.dependencies):
                return step
        return None

    def pending_steps(self) -> list[PlanStep]:
        return [s for s in self.steps if s.status == PlanStepStatus.PENDING]

    def completed_steps(self) -> list[PlanStep]:
        return [s for s in self.steps if s.status == PlanStepStatus.COMPLETED]

    def is_complete(self) -> bool:
        return all(
            s.status in (PlanStepStatus.COMPLETED, PlanStepStatus.SKIPPED)
            for s in self.steps
        )
