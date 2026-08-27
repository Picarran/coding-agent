"""Entry point: assemble the LLM, tools, and ReAct loop, then run a task."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.core.events import ConsoleTracer
from src.core.models import AgentStatus
from src.llm.deepseek_client import DeepSeekClient
from src.loops.react_loop import ReactLoop
from src.tools.command_tools import build_command_tools
from src.tools.executor import ToolExecutor
from src.tools.file_tools import build_file_tools
from src.tools.registry import ToolRegistry

SYSTEM_PROMPT = (
    "You are a coding agent working inside a local workspace.\n"
    "Available tools: list_files (list files), read_file (read a file), "
    "execute_command (run a shell command).\n"
    "Working principles:\n"
    "1. Act step by step: call tools to gather information, observe each tool "
    "result, then decide the next action.\n"
    "2. Use read_file to read file contents when needed; never guess them.\n"
    "3. Use execute_command to run commands or tests to obtain real execution results.\n"
    "4. Once you have enough information, stop calling tools and give a clear, "
    "well-structured final answer in the user's language.\n"
    "5. Stay strictly inside the workspace; never access paths outside it.\n"
    "At this stage you can only read files and run commands; you cannot modify files."
)

DEFAULT_TASK = (
    "请分析 demo_workspace 目录中的代码：先用 list_files 查看文件结构，"
    "再用 read_file 阅读 calculator.py 和 test_calculator.py，"
    "然后运行 `python test_calculator.py` 查看测试结果，"
    "最后用中文说明测试失败的原因，并给出具体的修复建议。"
)


def build_registry(root: Path) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in build_file_tools(root):
        registry.register(tool)
    for tool in build_command_tools(root):
        registry.register(tool)
    return registry


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Minimal coding agent (Phase 1).")
    parser.add_argument(
        "task", nargs="?", default=DEFAULT_TASK, help="Task for the agent."
    )
    parser.add_argument(
        "--workspace",
        default="demo_workspace",
        help="Workspace root (default: demo_workspace).",
    )
    parser.add_argument(
        "--max-steps", type=int, default=20, help="Max ReAct steps."
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

    llm = DeepSeekClient()
    executor = ToolExecutor(build_registry(root))
    loop = ReactLoop(
        llm,
        executor,
        SYSTEM_PROMPT,
        max_steps=args.max_steps,
        tracer=ConsoleTracer(),
    )

    print(f"Task: {args.task}")
    print(f"Workspace: {root}")
    print("-" * 64)
    result = loop.run(args.task)

    print("\n" + "=" * 64)
    print(f"Status: {result.status.value}")
    print(f"Steps: {result.artifacts.get('steps')}")
    print(f"Final state: {result.artifacts.get('final_state')}")
    print("-" * 64)
    print(result.summary)
    return 0 if result.status != AgentStatus.FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
