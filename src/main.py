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
from src.llm.router import ModelRouter, TaskType, build_model_router
from src.mcp.config import default_config_path, load_mcp_config
from src.mcp.manager import MCPManager
from src.orchestration import OrchestrationMode
from src.planning.delegation import DelegationPolicy
from src.planning.planner import Planner
from src.planning.replanner import Replanner
from src.safety.permissions import (
    PermissionChecker,
    PermissionMode,
    default_input_approver,
)
from src.skills.registry import SkillRegistry, discover_skill_dirs
from src.session.store import default_session_path, load_session, save_session
from src.task_router import TaskRouter
from src.tools.definitions import ToolDefinition

DEFAULT_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def build_main_agent(
    root: Path,
    llm: DeepSeekClient,
    max_steps: int,
    permission_mode: PermissionMode | str = PermissionMode.DEFAULT,
    interactive: bool = False,
    event_bus: EventBus | None = None,
    router: ModelRouter | None = None,
    checkpoint_cb=None,
    skill_registry: SkillRegistry | None = None,
    extra_tools: list[ToolDefinition] | None = None,
    streaming: bool = False,
    approver=None,
) -> MainAgent:
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
    skill_registry = skill_registry or SkillRegistry.load(DEFAULT_SKILLS_DIR)
    if skill_registry.all():
        # Progressive disclosure: the Planner only sees the name + description
        # catalog; the full body is loaded only when a skill is matched.
        env = env + "\n\n" + skill_registry.catalog()
    summarizer = r.route(TaskType.SUMMARIZATION)
    agents = {
        "explorer": ExplorerAgent(
            r.route(TaskType.EXPLORATION), root, event_bus=event_bus,
            max_steps=max_steps, permission_checker=checker, summarizer_llm=summarizer,
            checkpoint_cb=checkpoint_cb, extra_tools=extra_tools, streaming=streaming,
        ),
        "coding": CodingAgent(
            r.route(TaskType.CODING), root, event_bus=event_bus,
            max_steps=max_steps, permission_checker=checker, summarizer_llm=summarizer,
            checkpoint_cb=checkpoint_cb, extra_tools=extra_tools, streaming=streaming,
        ),
        "test": TestAgent(
            r.route(TaskType.TESTING), root, event_bus=event_bus,
            max_steps=max_steps, permission_checker=checker, summarizer_llm=summarizer,
            checkpoint_cb=checkpoint_cb, extra_tools=extra_tools, streaming=streaming,
        ),
    }
    return MainAgent(
        Planner(r.route(TaskType.PLANNING), environment=env),
        Replanner(r.route(TaskType.PLANNING), environment=env),
        agents,
        llm=r.route(TaskType.SYNTHESIS),
        event_bus=event_bus,
        delegation_policy=DelegationPolicy(),
        checkpoint_cb=checkpoint_cb,
        skill_registry=skill_registry,
    )


def build_agent(
    root: Path,
    llm,
    max_steps: int,
    orchestration: OrchestrationMode | str = OrchestrationMode.AUTO,
    permission_mode: PermissionMode | str = PermissionMode.DEFAULT,
    interactive: bool = False,
    event_bus: EventBus | None = None,
    router: ModelRouter | None = None,
    checkpoint_cb=None,
    skill_registry: SkillRegistry | None = None,
    extra_tools: list[ToolDefinition] | None = None,
    streaming: bool = False,
    approver=None,
):
    """Build the agent topology selected by ``orchestration`` (fast/auto/thorough).

    - fast: single ReAct loop (no planner).
    - thorough: MainAgent (planner + SubAgents), never degrades to fast.
    - auto: TaskRouter — task_score picks fast vs multi, with a fast-first cascade.

    ``extra_tools`` (e.g. MCP tools) are registered into every agent's registry;
    ``streaming`` enables token-level LLM streaming (STREAM_DELTA events);
    ``approver`` overrides the interactive approval callback (used by the web UI).
    """
    r = router or ModelRouter(llm)
    mode = OrchestrationMode(orchestration)
    if mode == OrchestrationMode.FAST:
        return build_single_agent(
            root,
            llm,
            max_steps,
            permission_mode=permission_mode,
            interactive=interactive,
            event_bus=event_bus,
            router=r,
            checkpoint_cb=checkpoint_cb,
            skill_registry=skill_registry,
            extra_tools=extra_tools,
            streaming=streaming,
            approver=approver,
        )
    multi = build_main_agent(
        root,
        llm,
        max_steps,
        permission_mode=permission_mode,
        interactive=interactive,
        event_bus=event_bus,
        router=r,
        checkpoint_cb=checkpoint_cb,
        skill_registry=skill_registry,
        extra_tools=extra_tools,
        streaming=streaming,
        approver=approver,
    )
    if mode == OrchestrationMode.THOROUGH:
        return multi
    single = build_single_agent(
        root,
        llm,
        max_steps,
        permission_mode=permission_mode,
        interactive=interactive,
        event_bus=event_bus,
        router=r,
        checkpoint_cb=checkpoint_cb,
        skill_registry=skill_registry,
        extra_tools=extra_tools,
        streaming=streaming,
        approver=approver,
    )
    return TaskRouter(single, multi, llm=r.route(TaskType.SUMMARIZATION), event_bus=event_bus)


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


def interactive(
    root: Path,
    llm,
    router: ModelRouter,
    max_steps: int,
    orchestration: str,
    permission_mode: str,
    bus: EventBus,
    memory_path: Path | None = None,
    extra_tools: list[ToolDefinition] | None = None,
) -> int:
    import threading

    from src.memory.retrieval import RetrievalMemory
    from src.session.side_quest import (
        SideQuestCoordinator,
        SideQuestQueue,
        build_side_quest_workers,
    )

    memory = RetrievalMemory.load(memory_path) if memory_path else RetrievalMemory()
    btw_q = SideQuestQueue()
    skill_registry = SkillRegistry.load_dirs(discover_skill_dirs(root))
    session_path = default_session_path(root)
    loaded = load_session(session_path)

    # Wire the /btw side-quest machinery BEFORE building the agent, so the agent's
    # loops can poll the queue at their checkpoints.
    read_worker, write_worker = build_side_quest_workers(
        root, llm, router, event_bus=bus, permission_mode=permission_mode, interactive=True
    )
    coordinator = SideQuestCoordinator(read_worker, write_worker, btw_q)
    agent = build_agent(
        root,
        llm,
        max_steps,
        orchestration=orchestration,
        permission_mode=permission_mode,
        interactive=True,
        event_bus=bus,
        router=router,
        checkpoint_cb=coordinator.checkpoint,
        skill_registry=skill_registry,
        extra_tools=extra_tools,
    )
    coordinator.agent = agent
    session = MainAgentSession(coordinator, llm=llm, memory=memory, skill_registry=skill_registry)
    session.set_history(loaded["history"])
    session.set_last_plan(loaded["last_plan"])

    print("=" * 64)
    print(f"Coding Agent — interactive mode (orchestration: {orchestration})")
    if loaded["history"]:
        print(
            f"Resumed session: {len(loaded['history'])} history entries from {session_path}"
        )
    print("Type a task and press Enter; /help for commands, exit/quit to leave.")
    print("While a task runs, type '/btw <question>' to ask in parallel.")
    print("=" * 64)

    try:
        while True:
            try:
                line = input("\n> ").strip()
            except EOFError:
                print()
                break
            if not line:
                continue
            if line.lower() in ("exit", "quit", "q"):
                break
            response = session.handle_command(line)
            if response is not None:
                print(response)
                continue
            if line.startswith("/btw"):
                print("(nothing running — type a task first, then /btw while it runs)")
                continue

            # Run the task on a background thread so the MAIN thread keeps owning
            # stdin (Ctrl+C stays clean on Windows) and can read /btw meanwhile.
            holder: dict = {}
            done = threading.Event()

            def _run() -> None:
                holder["result"] = session.send(line)
                done.set()
                print("\n[task finished — press Enter to continue]")

            worker = threading.Thread(target=_run, daemon=True)
            worker.start()
            exiting = False
            while not done.is_set():
                try:
                    side = input("[while running] > ").strip()
                except EOFError:
                    exiting = True
                    break
                if not side:
                    continue
                if side.lower() in ("exit", "quit", "q"):
                    exiting = True
                    break
                if side.startswith("/btw"):
                    btw_q.put(side[len("/btw") :].strip() or "(no question)")
                else:
                    print("(task still running — use /btw to ask in parallel)")
            worker.join()
            result = holder.get("result")
            if result is not None:
                print_result(result)
            # Persist after every turn, so a crash/Ctrl+C loses at most this turn.
            save_session(session_path, session.history, session.last_plan)
            if exiting:
                break
    except KeyboardInterrupt:
        print("\n(interrupted)")
    finally:
        if memory_path:
            memory.save(memory_path)
        save_session(session_path, session.history, session.last_plan)
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
        "--max-steps", type=int, default=50, help="Max ReAct steps per SubAgent turn."
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
        "--mcp-config",
        default=None,
        help=(
            "Path to an MCP config file (JSON). Defaults to "
            "<workspace>/.coding-agent/mcp.json if present."
        ),
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
    print(f"  Parallel batches: {summary['parallel_batches']}")
    print(f"  Escalations     : {summary['escalations']}")
    print(f"  Fast routes     : {summary['fast_routes']}")
    print(f"  Multi routes    : {summary['multi_routes']}")
    print(f"  Avg task score  : {summary['avg_task_score']}")
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
    router = build_model_router(llm)  # strong + optional fast (DEEPSEEK_FAST_MODEL)
    skill_registry = SkillRegistry.load_dirs(discover_skill_dirs(root))

    # MCP (V2-9): load external tool servers if a config file is present.
    mcp_manager = MCPManager()
    mcp_tools: list[ToolDefinition] = []
    mcp_config_path = (
        Path(args.mcp_config).resolve() if args.mcp_config else default_config_path(root)
    )
    if mcp_config_path.exists():
        try:
            configs = load_mcp_config(mcp_config_path)
        except (OSError, ValueError) as exc:
            print(
                f"Error: failed to load MCP config {mcp_config_path}: {exc}",
                file=sys.stderr,
            )
            return 1
        mcp_tools = mcp_manager.start(configs)
        if mcp_tools:
            print(f"MCP: {len(mcp_tools)} tool(s) from {mcp_config_path}")
            for line in mcp_manager.describe():
                print(line)
        else:
            print(f"MCP: no tools loaded from {mcp_config_path}")
    else:
        print(f"MCP: no config at {mcp_config_path} (skipped)")

    bus = EventBus([ConsoleTracer()], session_id=uuid.uuid4().hex)
    audit_logger: JsonlAuditLogger | None = None
    if args.audit_log:
        audit_logger = JsonlAuditLogger(args.audit_log)
        bus.subscribe(audit_logger)
    metrics = MetricsCollector()
    bus.subscribe(metrics)

    bus.emit_simple(
        EventType.SESSION_START,
        payload={
            "workspace": str(root),
            "permission_mode": args.permission,
            "orchestration": args.orchestration,
        },
    )
    exit_code = 0
    try:
        if args.task:
            agent = build_agent(
                root,
                llm,
                max_steps=args.max_steps,
                orchestration=args.orchestration,
                permission_mode=args.permission,
                interactive=False,
                event_bus=bus,
                router=router,
                skill_registry=skill_registry,
                extra_tools=mcp_tools,
            )
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
            exit_code = interactive(
                root,
                llm,
                router,
                max_steps=args.max_steps,
                orchestration=args.orchestration,
                permission_mode=args.permission,
                bus=bus,
                memory_path=root / ".coding-agent" / "memory.json",
                extra_tools=mcp_tools,
            )
            bus.emit_simple(EventType.SESSION_END, status="ENDED")
    finally:
        mcp_manager.close()

    print_metrics(metrics.summary())
    if audit_logger is not None:
        audit_logger.close()
        print(f"Audit log: {args.audit_log}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
