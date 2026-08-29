"""TaskRouter (V2-5.4): route a whole task to fast (single agent) vs multi (MainAgent).

The step-level DIRECT shortcut was retired — the decision that actually moves the
needle is *topology*: a simple task should never pay for a planner + SubAgents.
TaskRouter scores the raw task text (before planning) and picks:

- ``score < LOW``    -> fast (single ReAct loop), no judge.
- ``LOW..HIGH``      -> fast first, LLM-as-judge on the self-report, escalate to multi.
- ``score >= HIGH``  -> multi (MainAgent) directly.

It reuses the same coarse verb-class taxonomy as the retired step scorer, plus two
task-level signals the step scorer could not see: multi-part and test-requirement.
"""
from __future__ import annotations

import logging
import re
from enum import Enum

from src.core.events import EventBus, EventType
from src.core.models import AgentResult, AgentStatus, Message

logger = logging.getLogger(__name__)


class Route(str, Enum):
    FAST = "fast"
    MULTI = "multi"


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

# Multi-part: the task asks for several sub-tasks / cross-cutting work.
_MULTI_SIGNALS = (
    "并且", "以及", "同时", "分别", "并", "多个", "所有", "每个",
    "两处", "多处", "数据流", "追踪", "相互独立", "三个", "几处",
)

# Test/verification requirement: the task is gated on tests passing.
_TEST_SIGNALS = ("全部通过", "测试通过", "确保", "验证", "assert", "tests pass", "通过测试")

LOW = 40
HIGH = 65

VERIFY_SYSTEM = (
    "You verify whether a coding task was actually completed. Based on the task "
    "description and the agent's own summary, judge whether the work is done. "
    "Answer exactly YES or NO."
)


def _verb_risk(text: str) -> float:
    t = (text or "").lower()
    for risk, signals in _VERB_CLASSES:
        if any(sig in t for sig in signals):
            return risk
    return _DEFAULT_VERB_RISK


def task_score(task: str) -> int:
    """0..100 estimate of how much a *whole task* needs decomposition (multi)."""
    text = task or ""
    n_tok = max(1, (len(text) + 3) // 4)
    n_files = len(_FILE_REF_RE.findall(text))
    multi = 1 if any(sig in text.lower() for sig in _MULTI_SIGNALS) else 0
    test = 1 if any(sig in text.lower() for sig in _TEST_SIGNALS) else 0
    raw = (
        25.0 * min(1.0, n_tok / 80.0)
        + 25.0 * min(1.0, n_files / 5.0)
        + 20.0 * _verb_risk(text)
        + 15.0 * multi
        + 15.0 * test
    )
    return int(round(raw))


class TaskRouter:
    """Routes each task to the single or multi topology, with a fast-first cascade."""

    def __init__(
        self,
        single_agent,
        multi_agent,
        llm=None,
        event_bus: EventBus | None = None,
        low: int = LOW,
        high: int = HIGH,
        agent_id: str = "task_router",
    ) -> None:
        self._single = single_agent
        self._multi = multi_agent
        self._llm = llm
        self._bus = event_bus
        self._low = low
        self._high = high
        self._agent_id = agent_id

    def run(self, task: str, forced_skill: str | None = None) -> AgentResult:
        if forced_skill is not None:
            # An explicitly requested skill lives on the multi (MainAgent) topology.
            return self._multi.run(task, forced_skill=forced_skill)
        score = task_score(task)
        if score < self._low:
            self._emit_route(Route.FAST, score)
            return self._single.run(task)
        if score >= self._high:
            self._emit_route(Route.MULTI, score)
            return self._multi.run(task)

        # Borderline band: try the cheap topology first, escalate on failure or
        # an "incomplete" verdict from the judge.
        self._emit_route(Route.FAST, score)
        result = self._single.run(task)
        ok = result.status == AgentStatus.SUCCESS and self._judge(task, result)
        if ok:
            return result
        reason = result.status.value if result.status != AgentStatus.SUCCESS else "verify"
        self._emit(EventType.ESCALATE, payload={"reason": reason})
        return self._multi.run(task)

    def _judge(self, task: str, result: AgentResult) -> bool:
        """LLM-as-judge on the single-agent self-report. Fail-open (True)."""
        if self._llm is None:
            return True
        try:
            response = self._llm.chat(
                [
                    Message(role="system", content=VERIFY_SYSTEM),
                    Message(
                        role="user",
                        content=(
                            f"Task: {task}\n"
                            f"Agent's answer: {result.summary}\n\n"
                            "Was the task completed correctly? Answer YES or NO."
                        ),
                    ),
                ]
            )
        except Exception as exc:  # noqa: BLE001 - a failed judge must not block
            logger.warning("task verify failed: %s", exc)
            return True
        answer = (response.content or "").strip().lower() if response else ""
        return not answer.startswith("no")

    def _emit_route(self, route: Route, score: int) -> None:
        if self._bus is not None:
            self._bus.emit_simple(
                EventType.ROUTE,
                agent_id=self._agent_id,
                payload={"route": route.value, "task_score": score},
            )

    def _emit(self, event_type: EventType, payload: dict | None = None) -> None:
        if self._bus is not None:
            self._bus.emit_simple(event_type, agent_id=self._agent_id, payload=payload)
