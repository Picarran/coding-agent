"""Entry point: assemble the LLM, tools, and ReAct loop, then run tasks.

Usage:
  python -m src.main                # interactive REPL (type tasks; "exit" to quit)
  python -m src.main "task..."      # one-shot task (for scripts / demo)
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.core.events import ConsoleTracer
from src.core.models import AgentResult, AgentStatus
from src.llm.deepseek_client import DeepSeekClient
from src.loops.react_loop import ReactLoop
from src.tools.command_tools import build_command_tools
from src.tools.executor import ToolExecutor
from src.tools.file_tools import build_file_tools
from src.tools.patch_tools import build_patch_tools
from src.tools.registry import ToolRegistry
from src.tools.search_tools import build_search_tools

SYSTEM_PROMPT = (
    "You are a coding agent working inside a local workspace.\n"
    "Available tools:\n"
    "- list_files: list files and directories.\n"
    "- read_file: read a file (optionally a line range).\n"
    "- search_text: search for a literal substring across files.\n"
    "- patch_file: replace an exact text snippet in a file (old_text must be unique).\n"
    "- write_file: create or overwrite a file.\n"
    "- execute_command: run a shell command (with a timeout).\n"
    "Working principles:\n"
    "1. Act step by step: call tools to gather information, observe each tool result, "
    "then decide the next action.\n"
    "2. Read files with read_file and search with search_text; never guess contents.\n"
    "3. Use execute_command to run tests/commands to verify real behavior.\n"
    "4. Prefer patch_file (exact replace) over write_file for small edits; "
    "patch_file requires a unique old_text.\n"
    "5. After modifying files, re-run the tests/commands to verify the fix.\n"
    "6. Once done, stop calling tools and give a clear final answer in the user's language.\n"
    "7. Stay strictly inside the workspace; never access paths outside it."
)


def build_registry(root: Path) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in build_file_tools(root):
        registry.register(tool)
    for tool in build_search_tools(root):
        registry.register(tool)
    for tool in build_patch_tools(root):
        registry.register(tool)
    for tool in build_command_tools(root):
        registry.register(tool)
    return registry


def build_agent(root: Path, max_steps: int) -> ReactLoop:
    llm = DeepSeekClient()
    executor = ToolExecutor(build_registry(root))
    return ReactLoop(
        llm,
        executor,
        SYSTEM_PROMPT,
        max_steps=max_steps,
        tracer=ConsoleTracer(),
    )


def print_result(result: AgentResult) -> None:
    print("\n" + "=" * 64)
    print(f"Status: {result.status.value}")
    print(f"Steps: {result.artifacts.get('steps')}")
    print(f"Final state: {result.artifacts.get('final_state')}")
    print("-" * 64)
    print(result.summary)


def run_once(loop: ReactLoop, task: str) -> int:
    print(f"Task: {task}")
    print("-" * 64)
    result = loop.run(task)
    print_result(result)
    return 0 if result.status != AgentStatus.FAILED else 1


def interactive(loop: ReactLoop) -> int:
    print("=" * 64)
    print("Coding Agent — interactive mode")
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
        run_once(loop, task)
    print("Bye.")
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Minimal coding agent (Phase 2).")
    parser.add_argument(
        "task",
        nargs="?",
        default=None,
        help="One-shot task; omit to enter interactive mode.",
    )
    parser.add_argument(
        "--workspace",
        default="demo_workspace",
        help="Workspace root (default: demo_workspace).",
    )
    parser.add_argument(
        "--max-steps", type=int, default=20, help="Max ReAct steps per task."
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Enable debug logging."
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    # Never let an unmappable console character crash the run.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    # Default: quiet logging (the trace shows the essentials); --verbose: full debug.
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_dotenv()

    root = Path(args.workspace).resolve()
    if not root.is_dir():
        print(f"Error: workspace not found: {root}", file=sys.stderr)
        return 1

    loop = build_agent(root, max_steps=args.max_steps)
    print(f"Workspace: {root}")
    if args.task:
        return run_once(loop, args.task)
    return interactive(loop)


if __name__ == "__main__":
    raise SystemExit(main())
