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

The DIRECT/DELEGATE decision is a **calibratable numeric score**, not a keyword
gate. ``complexity_score`` (0..100) is a weighted sum of measurable features:

    score = 20·min(1, tokens/40)      description length (chars/4 estimate)
          + 20·min(1, files/3)        files referenced (path-like tokens)
          + 15·min(1, deps/2)         dependencies in the plan
          + 15·is_write               assigned a mutating role (coding)
          + 40·verb_risk              coarse verb class (read/create/fix/refactor)

Steps scoring below the threshold go DIRECT; at/above it go DELEGATE. The only
semantic component is the coarse verb class (a weighted *feature*, not a binary
gate), so the score is still deterministic, explainable, and tunable — and the
threshold can be calibrated on the eval suite (see ROADMAP).

Two scheduling guarantees follow from the data model:

1. *Runnable steps are mutually independent by construction* — a step is runnable
   only when every dependency is already COMPLETED, so no two runnable steps can
   depend on each other. Parallelizing a runnable batch cannot violate ordering.
2. *Read-only steps never race a write* — the batch of leading read-only steps is
   parallelized only until the first mutating step in plan order; that mutating
   step then runs alone, and any later read-only steps wait for it.
"""
from __future__ import annotations

import re
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
    # 0..100 difficulty estimate; None for PARALLEL (not a complexity decision).
    complexity_score: int | None = None


# Roles that expose no file-write tools (no patch_file / write_file).
#
# NOTE: ``test`` still has ``execute_command`` (a shell is an escape hatch), so
# "read-only" here means "no file-write *tools*" — a documented soft guarantee,
# not a sandbox boundary. Parallelism is therefore gated on role capability,
# not on a hard read-only proof.
READ_ONLY_AGENTS = frozenset({"explorer", "test"})

# Path-like tokens: measure "how many files this step touches" (a robust proxy,
# independent of wording).
_FILE_REF_RE = re.compile(r"[\w./\\-]+\.(?:py|json|txt|md|js|ts|yml|yaml|toml|ini|cfg)")

# Coarse verb-class risk — the single semantic feature. Checked in descending
# order so the riskiest class wins; unknown verbs get a neutral 0.5.
_VERB_CLASSES: tuple[tuple[float, tuple[str, ...]], ...] = (
    (
        1.0,
        (
            "refactor", "migrate", "split", "restructure", "redesign", "rewrite",
            "implement",
            "重构", "拆分", "迁移", "移动", "移到", "重写", "实现",
        ),
    ),
    (
        0.7,
        (
            "fix", "debug", "repair", "update", "modify", "change",
            "修复", "调试", "修正", "更新", "修改", "调整",
        ),
    ),
    (
        0.3,
        (
            "create", "add", "write", "generate", "define",
            "创建", "新增", "写入", "生成", "定义",
        ),
    ),
    (
        0.0,
        (
            "read", "list", "search", "check", "run", "test", "inspect",
            "explore", "investigate",
            "读取", "查找", "搜索", "检查", "运行", "测试", "找出",
        ),
    ),
)
_DEFAULT_VERB_RISK = 0.5

DIRECT_THRESHOLD = 50  # score < 50 -> DIRECT; >= 50 -> DELEGATE


class DelegationPolicy:
    """Deterministic scheduler: ``direct / delegate / parallel`` per runnable batch."""

    def __init__(self, threshold: int = DIRECT_THRESHOLD) -> None:
        self._threshold = threshold

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
        return self._single(runnable[0], "mutating step runs serially")

    def _single(self, step: PlanStep, label: str) -> DelegationDecision:
        score = self.score(step)
        if score < self._threshold:
            return DelegationDecision(
                DelegationStrategy.DIRECT, (step,), f"simple {label}", complexity_score=score
            )
        return DelegationDecision(
            DelegationStrategy.DELEGATE, (step,), f"{label} (complexity {score})",
            complexity_score=score,
        )

    def score(self, step: PlanStep) -> int:
        """Compute the 0..100 complexity estimate for a single step."""
        n_tok = max(1, (len(step.description) + 3) // 4)
        n_files = len(_FILE_REF_RE.findall(step.description))
        n_deps = len(step.dependencies)
        is_write = 0 if step.assigned_agent in READ_ONLY_AGENTS else 1
        raw = (
            20.0 * min(1.0, n_tok / 40.0)
            + 20.0 * min(1.0, n_files / 3.0)
            + 15.0 * min(1.0, n_deps / 2.0)
            + 15.0 * is_write
            + 40.0 * self._verb_risk(step.description)
        )
        return int(round(raw))

    @staticmethod
    def _verb_risk(description: str) -> float:
        desc = description.lower()
        for risk, signals in _VERB_CLASSES:
            if any(sig in desc for sig in signals):
                return risk
        return _DEFAULT_VERB_RISK
