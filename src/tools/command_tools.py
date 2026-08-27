"""Command tool: ``execute_command``, run inside the workspace with a timeout."""
from __future__ import annotations

import subprocess
from functools import partial
from pathlib import Path

from src.tools.definitions import ToolDefinition


def execute_command(root: Path, command: str, timeout: int = 60) -> str:
    if not command or not command.strip():
        return "Error: empty command"

    timed_out = False
    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        exit_code = completed.returncode
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = None
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""

    return "\n".join(
        [
            f"command: {command}",
            f"exit_code: {exit_code}",
            f"timed_out: {timed_out}",
            "--- stdout ---",
            stdout,
            "--- stderr ---",
            stderr or "(none)",
        ]
    )


def build_command_tools(root: Path) -> list[ToolDefinition]:
    root = Path(root).resolve()
    return [
        ToolDefinition(
            name="execute_command",
            description=(
                "Run a shell command inside the workspace (cwd = workspace root) "
                "and return exit_code, stdout, stderr, and timed_out."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to run.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default 60).",
                    },
                },
                "required": ["command"],
            },
            func=partial(execute_command, root),
        ),
    ]
