"""DelegationPolicy: parallelize independent read-only steps, else run serially.

The DIRECT/DELEGATE complexity decision moved UP to the task-level ``TaskRouter``:
a whole task is routed fast (single agent) vs multi (MainAgent) *before* planning.
Inside multi every step now runs through its role SubAgent; this policy only decides
*parallelism* — leading read-only steps run concurrently, a mutating step runs alone.

Two scheduling guarantees follow from the data model:

1. *Runnable steps are mutually independent by construction* — a step is runnable
   only when every dependency is already COMPLETED, so no two runnable steps can
   depend on each other. Parallelizing a runnable batch cannot violate ordering.
2. *Read-only steps never race a write* — the batch of leading read-only steps is
   parallelized only until the first mutating step in plan order; that mutating
   step then runs alone, and any later read-only steps wait for it.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.planning.task_plan import PlanStep


class DelegationStrategy(str, Enum):
    DELEGATE = "delegate"
    PARALLEL = "parallel"


@dataclass(frozen=True)
class DelegationDecision:
    strategy: DelegationStrategy
    steps: tuple[PlanStep, ...]
    reason: str


# Roles that expose no file-write tools (no patch_file / write_file).
#
# NOTE: ``test`` still has ``execute_command`` (a shell is an escape hatch), so
# "read-only" here means "no file-write *tools*" — a documented soft guarantee,
# not a sandbox boundary. Parallelism is therefore gated on role capability,
# not on a hard read-only proof.
READ_ONLY_AGENTS = frozenset({"explorer", "test"})


class DelegationPolicy:
    """Parallel scheduler: ``parallel`` for a read-only batch, else ``delegate``."""

    def decide(self, runnable: list[PlanStep]) -> DelegationDecision:
        # Leading read-only steps (in plan order) may run in parallel with each
        # other, but never concurrently with a write: stop at the first mutating
        # step and defer it (and everything after) to a later iteration.
        leading_reads: list[PlanStep] = []
        for step in runnable:
            if step.assigned_agent not in READ_ONLY_AGENTS:
                break
            leading_reads.append(step)

        if len(leading_reads) > 1:
            return DelegationDecision(
                DelegationStrategy.PARALLEL,
                tuple(leading_reads),
                f"{len(leading_reads)} independent read-only steps",
            )
        step = runnable[0]
        if step.assigned_agent in READ_ONLY_AGENTS:
            return DelegationDecision(DelegationStrategy.DELEGATE, (step,), "read-only step")
        return DelegationDecision(
            DelegationStrategy.DELEGATE, (step,), "mutating step runs serially"
        )
