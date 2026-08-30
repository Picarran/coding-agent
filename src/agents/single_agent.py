"""Single-agent builder (V2-5.2): one ReAct loop with the full toolset.

This is the ``FAST`` orchestration mode — no planner, no SubAgents, no structured
report ceremony. Used directly by the CLI (``--orchestration fast``) and by the
eval harness as the lower baseline.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from src.agents.base_agent import BaseAgent
from src.agents.registries import build_coding_registry
from src.context.environment import build_environment_context
from src.core.events import EventBus
from src.llm.base import LLMClient
from src.llm.router import ModelRouter, TaskType
from src.safety.permissions import (
    PermissionChecker,
    PermissionMode,
    default_input_approver,
)
from src.skills.registry import SkillRegistry
from src.tools.definitions import ToolDefinition

SINGLE_AGENT_SYSTEM = (
    "You are a coding agent. Complete the user's task directly using your tools "
    "(list_files, read_file, search_text, patch_file, write_file, execute_command). "
    "When finished, submit your report with submit_report."
)


def build_single_agent(
    root: Path,
    llm: LLMClient,
    max_steps: int,
    permission_mode: PermissionMode | str = PermissionMode.AUTONOMOUS,
    interactive: bool = False,
    event_bus: EventBus | None = None,
    router: ModelRouter | None = None,
    checkpoint_cb: Callable[[], None] | None = None,
    skill_registry: SkillRegistry | None = None,
    extra_tools: list[ToolDefinition] | None = None,
    streaming: bool = False,
    approver: Callable[[str], bool] | None = None,
) -> BaseAgent:
    """A single ReAct loop with the full toolset — no planner, no sub-agents."""
    r = router or ModelRouter(llm)
    checker = PermissionChecker.from_mode(
        permission_mode,
        approver=(
            approver
            if approver is not None
            else (default_input_approver() if interactive else None)
        ),
    )
    env = build_environment_context(root)
    registry = build_coding_registry(root)
    for tool in extra_tools or []:
        registry.register(tool)
    return BaseAgent(
        "single_agent",
        r.route(TaskType.CODING),
        registry,
        SINGLE_AGENT_SYSTEM + "\n\n" + env,
        {},  # report fields: only the required "summary"
        event_bus=event_bus,
        max_steps=max_steps,
        permission_checker=checker,
        summarizer_llm=r.route(TaskType.SUMMARIZATION),
        checkpoint_cb=checkpoint_cb,
        skill_registry=skill_registry,
        streaming=streaming,
    )
