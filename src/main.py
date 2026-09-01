"""Entry point: run the Main Agent (multi-agent, plan-and-execute).

Usage:
  python -m src.main "task"     # one-shot
  python -m src.main            # interactive Main Agent
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv

from src.agents.coding_agent import CodingAgent
from src.agents.explorer_agent import ExplorerAgent
from src.agents.main_agent import MainAgent
from src.agents.main_agent_session import COMMANDS, MainAgentSession
from src.agents.single_agent import build_single_agent
from src.agents.test_agent import TestAgent
from src.context.environment import build_environment_context
from src.core.events import (
    ConsoleTracer,
    EventBus,
    EventType,
    JsonlAuditLogger,
    SessionMetrics,
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


def _context_limit() -> int:
    try:
        return int(os.environ.get("DEEPSEEK_CONTEXT_LIMIT", 64000))
    except ValueError:
        return 64000


class ContextTracker:
    """Tracks the latest ``prompt_tokens`` for a live context-usage meter.

    On ``LLM_CALL`` it snapshots the real prompt size; on ``CONTEXT_COMPACT``
    (e.g. ``/compact``) it lowers the estimate by the removed characters, so the
    meter drops immediately instead of waiting for the next call.
    """

    def __init__(self) -> None:
        self.prompt_tokens = 0

    def on_event(self, event) -> None:
        if event.event_type == EventType.LLM_CALL:
            tokens = (event.payload or {}).get("prompt_tokens")
            if tokens is not None:
                self.prompt_tokens = int(tokens)
        elif event.event_type == EventType.CONTEXT_COMPACT:
            removed = (event.payload or {}).get("removed_chars")
            if removed is not None:
                self.prompt_tokens = max(0, self.prompt_tokens - int(removed) // 4)


def _fmt_ctx(tokens: int, limit: int) -> str:
    """Compact context usage, e.g. ``3.2k/64k``."""
    limit_k = max(1, limit // 1000)
    if tokens >= 1000:
        return f"{tokens / 1000:.1f}k/{limit_k}k"
    return f"{tokens}/{limit_k}k"


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
    metrics: SessionMetrics | None = None,
    ctx_tracker: ContextTracker | None = None,
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
    session = MainAgentSession(
        coordinator, llm=llm, memory=memory, skill_registry=skill_registry, event_bus=bus
    )
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
                ctx = ctx_tracker.prompt_tokens if ctx_tracker else 0
                prompt = f"\n> [ctx {_fmt_ctx(ctx, _context_limit())}] "
                line = input(prompt).strip()
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
            if line.startswith("/"):
                # A slash input that is not a known command: give a hint instead
                # of silently treating it as a task.
                if line == "/":
                    print("可用命令：" + "  ".join(sorted(COMMANDS)) + "  /btw")
                else:
                    print(f"未知命令：{line.split()[0]}（输入 / 查看全部命令，/help 查看说明）")
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
            if metrics is not None:
                metrics.finish_task(line)
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
        "--quiet",
        action="store_true",
        help="Only print step-level progress + the final answer (hide per-tool noise).",
    )
    parser.add_argument(
        "--no-color", action="store_true", help="Disable ANSI colors in the CLI trace."
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Enable debug logging."
    )
    return parser.parse_args(argv)


def print_metrics(summary: dict) -> None:
    print("\n" + "-" * 64)
    print("Metrics:")
    print(f"  LLM calls       : {summary['llm_calls']}")
    print(f"  Tokens in/out   : {summary['prompt_tokens']} / {summary['completion_tokens']}")
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


def print_task_metrics(tasks: list[dict]) -> None:
    """Per-task metric rows (V3-11): tokens + latency for each task."""
    if not tasks:
        return
    print("\n" + "-" * 64)
    print("Per-task metrics:")
    header = f"  {'task':<28} {'calls':>5} {'tok_in':>7} {'tok_out':>7} {'ms':>8}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for t in tasks:
        label = (t.get("label") or "")[:28]
        print(
            f"  {label:<28} {t.get('llm_calls', 0):>5} "
            f"{t.get('prompt_tokens', 0):>7} {t.get('completion_tokens', 0):>7} "
            f"{t.get('duration_ms') or 0:>8}"
        )


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

    bus = EventBus(
        [ConsoleTracer(quiet=args.quiet, color=(False if args.no_color else None))],
        session_id=uuid.uuid4().hex,
    )
    audit_logger: JsonlAuditLogger | None = None
    if args.audit_log:
        audit_logger = JsonlAuditLogger(args.audit_log)
        bus.subscribe(audit_logger)
    metrics = SessionMetrics()
    bus.subscribe(metrics)
    ctx_tracker = ContextTracker()
    bus.subscribe(ctx_tracker)

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
            metrics.finish_task(args.task)
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
                metrics=metrics,
                ctx_tracker=ctx_tracker,
            )
            bus.emit_simple(EventType.SESSION_END, status="ENDED")
    finally:
        mcp_manager.close()

    print_metrics(metrics.summary())
    print_task_metrics(metrics.tasks())
    if audit_logger is not None:
        audit_logger.close()
        print(f"Audit log: {args.audit_log}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
