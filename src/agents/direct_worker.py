"""DirectWorker: a lightweight single ReAct loop for simple steps (V2-5).

Unlike a role SubAgent it registers NO ``submit_report`` terminal tool, so the
loop stops as soon as the model answers in plain text. For a trivial step this
saves a whole forced structured-report round-trip (an extra LLM turn plus a
larger, schema-shaped output) that a tiny step does not need.

It keeps the FULL toolset (list/read/search/patch/write/execute), because a
"simple" step may still need to write a file (e.g. "write version.txt").
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from src.agents.registries import build_coding_registry
from src.context.environment import build_environment_context
from src.core.events import EventBus
from src.llm.base import LLMClient
from src.loops.react_loop import ReactLoop
from src.tools.executor import ToolExecutor

DIRECT_SYSTEM = (
    "You are a coding agent completing one focused step. Use your tools "
    "(list_files, read_file, search_text, patch_file, write_file, execute_command) "
    "to finish the step, verifying your work where possible. Then answer "
    "concisely in plain text — do not invent a report format."
)


class DirectWorker:
    def __init__(
        self,
        llm: LLMClient,
        root: Path,
        event_bus: EventBus | None = None,
        max_steps: int = 20,
        permission_checker: Any = None,
    ) -> None:
        executor = ToolExecutor(
            build_coding_registry(root),
            permission_checker=permission_checker,
            event_bus=event_bus,
            agent_id="direct_worker",
        )
        self._loop = ReactLoop(
            llm,
            executor,
            DIRECT_SYSTEM + "\n\n" + build_environment_context(root),
            event_bus=event_bus,
            agent_id="direct_worker",
            report_tool_name=None,
            max_steps=max_steps,
        )

    def run(self, task: str):
        result = self._loop.run(task)
        result.agent_name = "direct_worker"
        return result
