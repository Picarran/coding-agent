"""Entry point: run the Main Agent (multi-agent, plan-and-execute).

Usage:
  python -m src.main "task"     # one-shot
  python -m src.main            # interactive Main Agent
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.agents.coding_agent import CodingAgent
from src.agents.explorer_agent import ExplorerAgent
from src.agents.main_agent import MainAgent
from src.agents.main_agent_session import MainAgentSession
from src.agents.test_agent import TestAgent
from src.context.environment import build_environment_context
from src.core.events import ConsoleTracer
from src.core.models import AgentResult, AgentStatus
from src.llm.deepseek_client import DeepSeekClient
from src.planning.planner import Planner
from src.planning.replanner import Replanner


def build_main_agent(root: Path, llm: DeepSeekClient, max_steps: int) -> MainAgent:
    env = build_environment_context(root)
    agents = {
        "explorer": ExplorerAgent(llm, root, tracer=ConsoleTracer(), max_steps=max_steps),
        "coding": CodingAgent(llm, root, tracer=ConsoleTracer(), max_steps=max_steps),
        "test": TestAgent(llm, root, tracer=ConsoleTracer(), max_steps=max_steps),
    }
    return MainAgent(
        Planner(llm, environment=env),
        Replanner(llm, environment=env),
        agents,
        llm=llm,
        on_progress=print,
    )


def print_result(result: AgentResult) -> None:
    print("\n" + "=" * 64)
    print(f"Status: {result.status.value}")
    print(f"Final state: {result.artifacts.get('final_state')}")
    print(f"Replans: {result.artifacts.get('replans')}")
    print("-" * 64)
    for step in result.artifacts.get("plan", []):
        print(f"  {step['id']} [{step['status']}] {step['description']}")
    print("-" * 64)
    print("Answer:")
    print(result.summary)


def interactive(agent: MainAgent) -> int:
    session = MainAgentSession(agent)
    print("=" * 64)
    print("Coding Agent — interactive mode (multi-agent)")
    print("Type a task and press Enter; type exit/quit to leave.")
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
        "--verbose", action="store_true", help="Enable debug logging."
    )
    return parser.parse_args(argv)


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
    agent = build_main_agent(root, llm, max_steps=args.max_steps)
    if args.task:
        print(f"Task: {args.task}")
        print(f"Workspace: {root}")
        result = agent.run(args.task)
        print_result(result)
        return 0 if result.status == AgentStatus.SUCCESS else 1
    print(f"Workspace: {root}")
    return interactive(agent)


if __name__ == "__main__":
    raise SystemExit(main())
