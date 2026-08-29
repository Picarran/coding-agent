"""Entry point: run the Main Agent (multi-agent, plan-and-execute).

Usage:
  python -m src.main "task"     # one-shot
  python -m src.main            # interactive Main Agent
"""
from __future__ import annotations

import argparse
import logging
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv

from src.agents.coding_agent import CodingAgent
from src.agents.direct_worker import DirectWorker
from src.agents.explorer_agent import ExplorerAgent
from src.agents.main_agent import MainAgent
from src.agents.main_agent_session import MainAgentSession
from src.agents.single_agent import build_single_agent
from src.agents.test_agent import TestAgent
from src.context.environment import build_environment_context
from src.core.events import (
    ConsoleTracer,
    EventBus,
    EventType,
    JsonlAuditLogger,
    MetricsCollector,
)
from src.core.models import AgentResult, AgentStatus
from src.llm.deepseek_client import DeepSeekClient
from src.orchestration import OrchestrationMode
from src.planning.delegation import DelegationPolicy
from src.planning.planner import Planner
from src.planning.replanner import Replanner
from src.safety.permissions import (
    PermissionChecker,
    PermissionMode,
    default_input_approver,
)


def build_main_agent(
    root: Path,
    llm: DeepSeekClient,
    max_steps: int,
    permission_mode: PermissionMode | str = PermissionMode.DEFAULT,
    interactive: bool = False,
    event_bus: EventBus | None = None,
    direct_enabled: bool = True,
) -> MainAgent:
    checker = PermissionChecker.from_mode(
        permission_mode,
        approver=default_input_approver() if interactive else None,
    )
    env = build_environment_context(root)
    agents = {
        "explorer": ExplorerAgent(
            llm, root, event_bus=event_bus, max_steps=max_steps, permission_checker=checker
        ),
        "coding": CodingAgent(
            llm, root, event_bus=event_bus, max_steps=max_steps, permission_checker=checker
        ),
        "test": TestAgent(
            llm, root, event_bus=event_bus, max_steps=max_steps, permission_checker=checker
        ),
    }
    return MainAgent(
        Planner(llm, environment=env),
        Replanner(llm, environment=env),
        agents,
        llm=llm,
        event_bus=event_bus,
        delegation_policy=DelegationPolicy(direct_enabled=direct_enabled),
        direct_worker=DirectWorker(
            llm, root, event_bus=event_bus, max_steps=max_steps, permission_checker=checker
        ),
    )


def build_agent(
    root: Path,
    llm,
    max_steps: int,
    orchestration: OrchestrationMode | str = OrchestrationMode.AUTO,
    permission_mode: PermissionMode | str = PermissionMode.DEFAULT,
    interactive: bool = False,
    event_bus: EventBus | None = None,
):
    """Build the agent topology selected by ``orchestration`` (fast/auto/thorough)."""
    mode = OrchestrationMode(orchestration)
    if mode == OrchestrationMode.FAST:
        return build_single_agent(
            root,
            llm,
            max_steps,
            permission_mode=permission_mode,
            interactive=interactive,
            event_bus=event_bus,
        )
    return build_main_agent(
        root,
        llm,
        max_steps,
        permission_mode=permission_mode,
        interactive=interactive,
        event_bus=event_bus,
        direct_enabled=(mode == OrchestrationMode.AUTO),
    )


def print_result(result: AgentResult) -> None:
    print("\n" + "=" * 64)
    print(f"Status: {result.status.value}")
    print(f"Final state: {result.artifacts.get('final_state')}")
    if "replans" in result.artifacts:
        print(f"Replans: {result.artifacts['replans']}")
    plan = result.artifacts.get("plan") or []
    if plan:
        print("-" * 64)
        for step in plan:
            print(f"  {step['id']} [{step['status']}] {step['description']}")
    print("-" * 64)
    print("Answer:")
    print(result.summary)


def interactive(agent, llm, mode_label: str = "auto") -> int:
    session = MainAgentSession(agent, llm=llm)
    print("=" * 64)
    print(f"Coding Agent — interactive mode (orchestration: {mode_label})")
    print("Type a task and press Enter; type /help for commands, exit/quit to leave.")
    print("=" * 64)
    while True:
        try:
            task = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not task:
            continue
        if task.lower() in ("exit", "quit", "q"):
            break
        response = session.handle_command(task)
        if response is not None:
            print(response)
            continue
        result = session.send(task)
        print_result(result)
    print("Bye.")
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Coding agent (multi-agent).")
    parser.add_argument(
        "task",
        nargs="?",
        default=None,
        help="Task; omit to enter interactive mode.",
    )
    parser.add_argument(
        "--workspace",
        default="demo_workspace",
        help="Workspace root (default: demo_workspace).",
    )
    parser.add_argument(
        "--max-steps", type=int, default=20, help="Max ReAct steps per SubAgent turn."
    )
    parser.add_argument(
        "--max-replans", type=int, default=3, help="Max replans."
    )
    parser.add_argument(
        "--permission",
        choices=[m.value for m in PermissionMode],
        default=PermissionMode.DEFAULT.value,
        help=(
            "Permission mode: plan (read-only), safe, default, or autonomous. "
            "Controls tool whitelist + risk threshold for approval."
        ),
    )
    parser.add_argument(
        "--orchestration",
        choices=[m.value for m in OrchestrationMode],
        default=OrchestrationMode.AUTO.value,
        help=(
            "Agent topology: fast (single ReAct loop), auto (MainAgent + "
            "DelegationPolicy), or thorough (MainAgent, every step a SubAgent)."
        ),
    )
    parser.add_argument(
        "--audit-log",
        default=None,
        help="Path to write a JSONL audit log of all events.",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Enable debug logging."
    )
    return parser.parse_args(argv)


def print_metrics(summary: dict) -> None:
    print("\n" + "-" * 64)
    print("Metrics:")
    print(f"  LLM calls       : {summary['llm_calls']}")
    print(f"  Total tokens    : {summary['total_tokens']}")
    print(f"  LLM avg latency : {summary['llm_avg_ms']} ms")
    print(f"  Tool calls      : {summary['tool_calls']}")
    print(f"  Tool errors     : {summary['tool_errors']}")
    print(f"  Tool success    : {summary['tool_success_rate']}")
    print(f"  Replans         : {summary['replans']}")
    print(f"  SubAgents       : {summary['subagents']}")
    print(f"  Direct steps    : {summary['direct_steps']}")
    print(f"  Parallel batches: {summary['parallel_batches']}")
    print(f"  Escalations     : {summary['escalations']}")
    print(f"  Avg complexity  : {summary['avg_complexity']}")
    print(f"  Approvals       : {summary['approvals']}")
    print(f"  Duration        : {summary['duration_ms']} ms")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_dotenv()

    root = Path(args.workspace).resolve()
    if not root.is_dir():
        print(f"Error: workspace not found: {root}", file=sys.stderr)
        return 1

    llm = DeepSeekClient()

    bus = EventBus([ConsoleTracer()], session_id=uuid.uuid4().hex)
    audit_logger: JsonlAuditLogger | None = None
    if args.audit_log:
        audit_logger = JsonlAuditLogger(args.audit_log)
        bus.subscribe(audit_logger)
    metrics = MetricsCollector()
    bus.subscribe(metrics)

    interactive_mode = args.task is None
    agent = build_agent(
        root,
        llm,
        max_steps=args.max_steps,
        orchestration=args.orchestration,
        permission_mode=args.permission,
        interactive=interactive_mode,
        event_bus=bus,
    )

    bus.emit_simple(
        EventType.SESSION_START,
        payload={
            "workspace": str(root),
            "permission_mode": args.permission,
            "orchestration": args.orchestration,
        },
    )
    exit_code = 0
    if args.task:
        print(f"Task: {args.task}")
        print(f"Workspace: {root}")
        print(f"Permission mode: {args.permission}")
        print(f"Orchestration: {args.orchestration}")
        result = agent.run(args.task)
        print_result(result)
        bus.emit_simple(EventType.SESSION_END, status=result.status.value)
        exit_code = 0 if result.status == AgentStatus.SUCCESS else 1
    else:
        print(f"Workspace: {root}")
        print(f"Permission mode: {args.permission}")
        exit_code = interactive(agent, llm, mode_label=args.orchestration)
        bus.emit_simple(EventType.SESSION_END, status="ENDED")

    print_metrics(metrics.summary())
    if audit_logger is not None:
        audit_logger.close()
        print(f"Audit log: {args.audit_log}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
