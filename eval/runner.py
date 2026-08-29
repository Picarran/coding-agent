"""Eval runner: seed a temp workspace, run the agent, grade deterministically, aggregate.

Usage:
  python -m eval.runner                              # real LLM (needs DEEPSEEK_API_KEY)
  python -m eval.runner --dry-run                    # scripted mock LLM, no API (wiring smoke test)
  python -m eval.runner --tasks fix_divide_bug,create_greet
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv

from eval.tasks import TASKS, Task
from src.core.events import EventBus, EventType, JsonlAuditLogger, MetricsCollector
from src.core.models import ToolCall
from src.llm.base import LLMClient, LLMResponse
from src.main import build_main_agent


class ScriptedLLM(LLMClient):
    """Returns a fixed script of responses, then a plain stop (dry-run only)."""

    def __init__(self, script: list[LLMResponse]) -> None:
        self._script = list(script)

    def chat(self, messages, tools=None):
        if not self._script:
            return LLMResponse(content="done", tool_calls=None, finish_reason="stop")
        return self._script.pop(0)


def _dry_run_llm_factory(agent_mode: str) -> Callable[[], LLMClient]:
    """A mode-aware scripted LLM.

    - multi: planner (submit_plan) -> sub-agent (submit_report) -> synthesis (text).
    - single: the ReAct loop ends immediately with submit_report.
    """
    report = LLMResponse(
        content=None,
        tool_calls=[
            ToolCall(
                id="r1",
                name="submit_report",
                arguments={"summary": "completed"},
                arguments_json='{"summary": "completed"}',
            )
        ],
        finish_reason="tool_calls",
    )
    plan = LLMResponse(
        content=None,
        tool_calls=[
            ToolCall(
                id="p1",
                name="submit_plan",
                arguments={
                    "goal": "g",
                    "steps": [
                        {"id": "step-1", "description": "do it", "agent": "coding"}
                    ],
                },
                arguments_json=(
                    '{"goal": "g", "steps": [{"id": "step-1", '
                    '"description": "do it", "agent": "coding"}]}'
                ),
            )
        ],
        finish_reason="tool_calls",
    )
    final = LLMResponse(content="final answer", tool_calls=None, finish_reason="stop")
    # multi: the planner produces a single simple step, which V2-5 routes to the
    # DIRECT worker (a plain-text stop), then the supervisor synthesizes.
    direct = LLMResponse(content="directly completed", tool_calls=None, finish_reason="stop")
    script = [plan, direct, final] if agent_mode == "multi" else [report]

    def factory() -> LLMClient:
        return ScriptedLLM(script)

    return factory


def _real_llm_factory() -> Callable[[], LLMClient]:
    from src.llm.deepseek_client import DeepSeekClient

    def factory() -> LLMClient:
        return DeepSeekClient()

    return factory


SINGLE_AGENT_SYSTEM = (
    "You are a coding agent. Complete the user's task directly using your tools "
    "(list_files, read_file, search_text, patch_file, write_file, execute_command). "
    "When finished, submit your report with submit_report."
)


def build_single_agent(root: Path, llm: LLMClient, max_steps: int, bus: EventBus):
    """A single ReAct loop with the full toolset — no planner, no sub-agents."""
    from src.agents.base_agent import BaseAgent
    from src.agents.registries import build_coding_registry
    from src.context.environment import build_environment_context
    from src.safety.permissions import PermissionChecker

    checker = PermissionChecker.from_mode("autonomous")
    env = build_environment_context(root)
    return BaseAgent(
        "single_agent",
        llm,
        build_coding_registry(root),
        SINGLE_AGENT_SYSTEM + "\n\n" + env,
        {},  # report fields: only the required "summary"
        event_bus=bus,
        max_steps=max_steps,
        permission_checker=checker,
    )


def build_agent(root: Path, llm: LLMClient, max_steps: int, agent_mode: str, bus: EventBus):
    if agent_mode == "single":
        return build_single_agent(root, llm, max_steps, bus)
    return build_main_agent(
        root,
        llm,
        max_steps=max_steps,
        permission_mode="autonomous",
        interactive=False,
        event_bus=bus,
    )


def seed_workspace(root: Path, seed: dict[str, str]) -> None:
    for rel, content in seed.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def run_task(
    task: Task,
    llm_factory: Callable[[], LLMClient],
    max_steps: int,
    agent_mode: str = "multi",
    audit_dir: Path | None = None,
) -> dict:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        seed_workspace(root, task.seed)

        bus = EventBus([], session_id=task.name)
        metrics = MetricsCollector()
        bus.subscribe(metrics)
        if audit_dir is not None:
            audit_dir.mkdir(parents=True, exist_ok=True)
            bus.subscribe(JsonlAuditLogger(audit_dir / f"{task.name}.jsonl"))

        agent = build_agent(root, llm_factory(), max_steps, agent_mode, bus)
        bus.emit_simple(EventType.SESSION_START)
        result = agent.run(task.task)
        bus.emit_simple(EventType.SESSION_END, status=result.status.value)

        passed, reason = task.verify(root)
        m = metrics.summary()
        return {
            "task": task.name,
            "complex": task.complex,
            "agent_status": result.status.value,
            "passed": passed,
            "verify_reason": reason,
            "plan_steps": len(result.artifacts.get("plan", [])),
            "replans": result.artifacts.get("replans", 0),
            "llm_calls": m["llm_calls"],
            "total_tokens": m["total_tokens"],
            "tool_calls": m["tool_calls"],
            "tool_errors": m["tool_errors"],
            "tool_cache_hits": m["tool_cache_hits"],
            "context_compactions": m["context_compactions"],
            "direct_steps": m["direct_steps"],
            "parallel_batches": m["parallel_batches"],
            "duration_ms": m["duration_ms"],
        }


def aggregate(records: list[dict]) -> dict:
    n = len(records)
    passed = sum(1 for r in records if r["passed"])

    def avg(key: str) -> float | None:
        vals = [r[key] for r in records if isinstance(r.get(key), (int, float))]
        return round(sum(vals) / len(vals), 1) if vals else None

    total_tokens = sum(r.get("total_tokens") or 0 for r in records)
    total_tools = sum(r.get("tool_calls") or 0 for r in records)

    return {
        "tasks": n,
        "passed": passed,
        "success_rate": round(passed / n, 3) if n else None,
        "avg_plan_steps": avg("plan_steps"),
        "avg_tool_calls": avg("tool_calls"),
        "avg_tokens": avg("total_tokens"),
        "tokens_per_tool_call": round(total_tokens / total_tools, 1) if total_tools else None,
        "context_compactions": sum(r.get("context_compactions") or 0 for r in records),
        "tool_cache_hits": sum(r.get("tool_cache_hits") or 0 for r in records),
        "direct_steps": sum(r.get("direct_steps") or 0 for r in records),
        "parallel_batches": sum(r.get("parallel_batches") or 0 for r in records),
        "avg_duration_ms": avg("duration_ms"),
    }


def run_eval(
    tasks: list[Task],
    max_steps: int,
    dry_run: bool,
    agent_mode: str = "multi",
    audit_dir: Path | None = None,
    progress_cb: Callable[[dict], None] | None = None,
) -> tuple[list[dict], dict]:
    """Run a set of tasks and return (records, aggregate).

    ``agent_mode`` selects the agent topology: ``"multi"`` (MainAgent + sub-agents)
    or ``"single"`` (one ReAct loop with the full toolset). ``progress_cb``
    receives ``{"phase": "task_start"/"task_done", "task": ..., "record": ...}``.
    """
    llm_factory = _dry_run_llm_factory(agent_mode) if dry_run else _real_llm_factory()
    records: list[dict] = []
    for task in tasks:
        if progress_cb:
            progress_cb({"phase": "task_start", "task": task.name})
        record = run_task(task, llm_factory, max_steps, agent_mode=agent_mode, audit_dir=audit_dir)
        records.append(record)
        if progress_cb:
            progress_cb({"phase": "task_done", "task": task.name, "record": record})
    return records, aggregate(records)


def print_summary(records: list[dict], agg: dict) -> None:
    print("\n" + "=" * 64)
    print("EVAL RESULTS")
    print("=" * 64)
    for r in records:
        mark = "PASS" if r["passed"] else "FAIL"
        print(
            f"  [{mark}] {r['task']:<18} status={r['agent_status']:<10} "
            f"steps={r['plan_steps']} tools={r['tool_calls']} "
            f"tokens={r['total_tokens']} time={r['duration_ms']}ms"
        )
    print("-" * 64)
    print(
        f"Success rate: {agg['passed']}/{agg['tasks']} = {agg['success_rate']}"
    )
    print(
        f"Avg: steps={agg['avg_plan_steps']} tools={agg['avg_tool_calls']} "
        f"tokens={agg['avg_tokens']} tok/tool={agg['tokens_per_tool_call']} "
        f"time={agg['avg_duration_ms']}ms"
    )
    print(f"Context compactions: {agg['context_compactions']}")
    print(f"Direct steps: {agg['direct_steps']}  Parallel batches: {agg['parallel_batches']}")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the coding-agent eval suite.")
    parser.add_argument(
        "--tasks",
        default=None,
        help="Comma-separated task names; default = all.",
    )
    parser.add_argument("--max-steps", type=int, default=20, help="Max ReAct steps per SubAgent.")
    parser.add_argument(
        "--agent",
        choices=["multi", "single"],
        default="multi",
        help="Agent topology: multi (MainAgent + sub-agents) or single (one ReAct loop).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use a scripted mock LLM (no API); validates the harness, not agent quality.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Report JSON path (default: eval/reports/report-<timestamp>.json).",
    )
    parser.add_argument(
        "--audit-dir",
        default=None,
        help="Write one JSONL audit log per task into this directory.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.dry_run:
        load_dotenv()  # pick up DEEPSEEK_API_KEY from .env for real runs
    if args.tasks:
        wanted = {t.strip() for t in args.tasks.split(",") if t.strip()}
        tasks = [t for t in TASKS if t.name in wanted]
    else:
        tasks = list(TASKS)

    if args.dry_run:
        print("[dry-run] scripted mock LLM — pass/fail is NOT meaningful, wiring only.")

    audit_dir = Path(args.audit_dir) if args.audit_dir else None
    records, agg = run_eval(
        tasks, args.max_steps, args.dry_run, agent_mode=args.agent, audit_dir=audit_dir
    )
    print_summary(records, agg)

    out_path = Path(args.output) if args.output else (
        Path(__file__).parent / "reports" / f"report-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "dry-run" if args.dry_run else "real",
        "agent_mode": args.agent,
        "tasks": records,
        "aggregate": agg,
    }
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nReport: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
