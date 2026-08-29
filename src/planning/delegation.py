"""DelegationPolicy: how the Main Agent should execute runnable steps (V2-5).

Three strategies:

- ``DIRECT``   — a single *simple* step runs through a lightweight ``DirectWorker``
                 (one ReAct loop, full toolset, no structured report tool). Spinning
                 up a role SubAgent for a trivial step is pure overhead.
- ``DELEGATE`` — one step (complex, or mutating) runs through its assigned role
                 SubAgent, serially.
- ``PARALLEL`` — several *read-only* steps that are simultaneously runnable run
                 through their SubAgents concurrently. A mutating (``coding``) step
                 never runs concurrently with anything.

The decision is deterministic (heuristics only — no extra LLM call), so it is
cheap and reproducible. Two scheduling guarantees follow from the data model:

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
    DIRECT = "direct"
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

# Words that mark a step as too involved for the direct path (multi-file /
# refactor / bug-fix work that benefits from a full role SubAgent).
COMPLEX_SIGNALS = (
    "implement", "refactor", "migrate", "restructure", "redesign", "rewrite",
    "fix", "debug", "repair",
    "multiple", "several", "across",
    "修复", "重构", "拆分", "迁移", "调试", "多个", "多处", "跨",
)

_MAX_SIMPLE_DESCRIPTION_LEN = 200


class DelegationPolicy:
    """Deterministic scheduler: ``direct / delegate / parallel`` per runnable batch."""

    def __init__(self, complex_signals: tuple[str, ...] = COMPLEX_SIGNALS) -> None:
        self._complex_signals = tuple(sig.lower() for sig in complex_signals)

    def decide(self, runnable: list[PlanStep]) -> DelegationDecision:
        # Leading read-only steps (in plan order) may run in parallel with each
        # other, but never concurrently with a write: stop at the first mutating
        # step and defer it (and everything after) to a later iteration.
        leading_reads: list[PlanStep] = []
        for step in runnable:
            if step.assigned_agent not in READ_ONLY_AGENTS:
                break
            leading_reads.append(step)

        if leading_reads:
            if len(leading_reads) > 1:
                return DelegationDecision(
                    DelegationStrategy.PARALLEL,
                    tuple(leading_reads),
                    f"{len(leading_reads)} independent read-only steps",
                )
            return self._single(leading_reads[0], "read-only step")

        # No leading read-only step: the first runnable step is mutating (coding
        # or an unknown role) and must run alone, in plan order.
        step = runnable[0]
        return self._single(step, "mutating step runs serially")

    def _single(self, step: PlanStep, label: str) -> DelegationDecision:
        if self._is_simple(step):
            return DelegationDecision(DelegationStrategy.DIRECT, (step,), "simple " + label)
        return DelegationDecision(DelegationStrategy.DELEGATE, (step,), label)

    def _is_simple(self, step: PlanStep) -> bool:
        if step.dependencies:
            return False  # part of a data flow: not a self-contained trivial step
        desc = step.description.lower()
        if any(sig in desc for sig in self._complex_signals):
            return False
        return len(step.description) <= _MAX_SIMPLE_DESCRIPTION_LEN
