"""Side-quest (``/btw``) support: async side questions while the main agent works.

The three-layer design from the V2-5 concurrency work:

1. **Input decoupling** — a reader thread owns stdin and routes lines to either
   the task queue or the ``SideQuestQueue`` (for ``/btw ...``), so the user can
   type while the main agent is running.
2. **Checkpoint delivery** — the agent loop calls a ``checkpoint`` hook once per
   ReAct iteration / MainAgent step; the hook drains the queue. An LLM call is
   blocking, so delivery happens at the next natural pause, not mid-call.
3. **Parallel vs queued** — a read-only side question runs immediately on a
   read-only worker (thread pool, in parallel with the main agent); a write side
   question is deferred until the main run finishes, so two agents never write
   the same workspace concurrently.
"""
from __future__ import annotations

import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from src.agents.registries import build_coding_registry, build_explorer_registry
from src.context.environment import build_environment_context
from src.core.events import EventBus
from src.core.models import AgentResult
from src.llm.base import LLMClient
from src.llm.router import ModelRouter, TaskType
from src.loops.react_loop import ReactLoop
from src.tools.executor import ToolExecutor

_WRITE_SIGNALS = (
    "create", "write", "add", "fix", "modify", "update", "patch", "implement",
    "refactor", "delete", "remove", "rename", "move",
    "创建", "写入", "新增", "修复", "修改", "更新", "实现", "重构", "删除", "移除", "重命名",
)

_SIDE_QUEST_READ_SYSTEM = (
    "You are answering a short side question about the workspace while the main "
    "agent works. Investigate with read-only tools and answer concisely in plain "
    "text. Do NOT modify or create files."
)
_SIDE_QUEST_WRITE_SYSTEM = (
    "You are handling a short follow-up request after the main task finished. "
    "Use your tools to complete it, then answer concisely in plain text."
)


def classify_side_quest(text: str) -> str:
    """Heuristic: does this side question require writing? Returns read/write."""
    t = (text or "").lower()
    return "write" if any(sig in t for sig in _WRITE_SIGNALS) else "read"


class SideQuestQueue:
    """Thread-safe FIFO of pending side questions."""

    def __init__(self) -> None:
        self._q: queue.Queue[str] = queue.Queue()

    def put(self, text: str) -> None:
        self._q.put(text)

    def poll(self) -> list[str]:
        """Non-blocking drain of all currently-pending questions."""
        out: list[str] = []
        while True:
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                return out

    def pending(self) -> bool:
        return not self._q.empty()


class SideQuestWorker:
    """A no-report ReAct loop for side answers (read-only or full toolset)."""

    def __init__(
        self,
        llm: LLMClient,
        root: Path,
        read_only: bool,
        event_bus: EventBus | None = None,
        max_steps: int = 8,
        permission_checker=None,
    ) -> None:
        registry = build_explorer_registry(root) if read_only else build_coding_registry(root)
        system = _SIDE_QUEST_READ_SYSTEM if read_only else _SIDE_QUEST_WRITE_SYSTEM
        executor = ToolExecutor(
            registry,
            permission_checker=permission_checker,
            event_bus=event_bus,
            agent_id="side_quest",
        )
        self._loop = ReactLoop(
            llm,
            executor,
            system + "\n\n" + build_environment_context(root),
            event_bus=event_bus,
            agent_id="side_quest",
            report_tool_name=None,
            max_steps=max_steps,
        )

    def run(self, task: str) -> AgentResult:
        result = self._loop.run(task)
        result.agent_name = "side_quest"
        return result


class SideQuestCoordinator:
    """Runs the main agent while draining side questions at checkpoints."""

    def __init__(
        self,
        read_worker: SideQuestWorker,
        write_worker: SideQuestWorker,
        side_queue: SideQuestQueue,
    ) -> None:
        self._read_worker = read_worker
        self._write_worker = write_worker
        self._queue = side_queue
        self.agent = None  # wired after the main agent is built
        self._lock = threading.Lock()
        self._pool: ThreadPoolExecutor | None = None
        self._answers: list[tuple[str, str]] = []
        self._pending_writes: list[str] = []

    def checkpoint(self) -> None:
        """Called from the agent loop at each natural pause."""
        if self._pool is None:
            return
        for text in self._queue.poll():
            if classify_side_quest(text) == "write":
                with self._lock:
                    self._pending_writes.append(text)
            else:
                self._pool.submit(self._answer_read, text)

    def run(self, task: str) -> AgentResult:
        self._answers = []
        self._pending_writes = []
        with ThreadPoolExecutor(max_workers=2) as pool:
            self._pool = pool
            try:
                main = self.agent.run(task)
            finally:
                self._pool = None
        # The ``with`` block above waits for in-flight read answers. Now that the
        # main run is over, it is safe to execute deferred write side questions.
        for text in self._pending_writes:
            self._answer_write(text)
        self._print_answers()
        return main

    def _answer_read(self, text: str) -> None:
        self._record(text, self._read_worker.run(text))

    def _answer_write(self, text: str) -> None:
        self._record(text, self._write_worker.run(text))

    def _record(self, text: str, result: AgentResult) -> None:
        with self._lock:
            self._answers.append((text, result.summary))

    def _print_answers(self) -> None:
        if not self._answers:
            return
        print("\n" + "-" * 64)
        print("/btw answers:")
        for text, summary in self._answers:
            print(f"  Q: {text}")
            print(f"  A: {summary}")
        print("-" * 64)


def build_side_quest_workers(
    root: Path,
    llm: LLMClient,
    router: ModelRouter,
    event_bus: EventBus | None = None,
    permission_mode: str = "default",
    interactive: bool = False,
) -> tuple[SideQuestWorker, SideQuestWorker]:
    from src.safety.permissions import PermissionChecker, default_input_approver

    checker = PermissionChecker.from_mode(
        permission_mode,
        approver=default_input_approver() if interactive else None,
    )
    read = SideQuestWorker(
        router.route(TaskType.EXPLORATION), root, read_only=True,
        event_bus=event_bus, permission_checker=checker,
    )
    write = SideQuestWorker(
        router.route(TaskType.CODING), root, read_only=False,
        event_bus=event_bus, permission_checker=checker,
    )
    return read, write
