"""Entry point: assemble the LLM, tools, and ReAct loop, then run a task."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.core.models import AgentStatus
from src.llm.deepseek_client import DeepSeekClient
from src.loops.react_loop import ReactLoop
from src.tools.command_tools import build_command_tools
from src.tools.executor import ToolExecutor
from src.tools.file_tools import build_file_tools
from src.tools.registry import ToolRegistry

SYSTEM_PROMPT = (
    "你是一个在本地工作区中工作的编程智能体（coding agent）。\n"
    "你可以使用以下工具完成任务：list_files（列出文件）、read_file（读取文件）、"
    "execute_command（执行命令）。\n"
    "工作准则：\n"
    "1. 分步行动：先调用工具收集信息，观察每次工具返回的结果，再决定下一步。\n"
    "2. 需要文件内容时用 read_file 读取，不要凭空猜测。\n"
    "3. 用 execute_command 运行命令或测试来获取真实执行结果。\n"
    "4. 在信息足够后，停止调用工具，用中文给出结构清晰的最终结论。\n"
    "5. 所有操作都限定在工作区内，不要试图访问工作区以外的路径。\n"
    "当前阶段你只能读取和执行命令，不能修改文件。"
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
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_dotenv()

    root = Path(args.workspace).resolve()
    if not root.is_dir():
        print(f"Error: workspace not found: {root}", file=sys.stderr)
        return 1

    llm = DeepSeekClient()
    executor = ToolExecutor(build_registry(root))
    loop = ReactLoop(llm, executor, SYSTEM_PROMPT, max_steps=args.max_steps)

    print(f"Task: {args.task}")
    print(f"Workspace: {root}")
    print("-" * 60)
    result = loop.run(args.task)

    print("=" * 60)
    print(f"Status: {result.status.value}")
    print(f"Steps: {result.artifacts.get('steps')}")
    print(f"Final state: {result.artifacts.get('final_state')}")
    print("-" * 60)
    print(result.summary)
    return 0 if result.status != AgentStatus.FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
