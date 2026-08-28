"""Command tool: ``execute_command``, run inside the workspace with a timeout."""
from __future__ import annotations

import locale
import subprocess
from functools import partial
from pathlib import Path

from src.tools.definitions import ToolDefinition


def _decode_output(data: bytes) -> str:
    """Decode subprocess output, preferring the platform's native encoding.

    Windows ``cmd``/``dir`` emit GBK (cp936), while many tools emit UTF-8; we try
    the locale-preferred encoding first, then UTF-8, then GBK, then a lossy
    fallback, so Chinese text is no longer garbled.
    """
    if not data:
        return ""
    preferred = locale.getpreferredencoding(False) or "utf-8"
    encodings = [preferred, "utf-8", "gbk", "cp1252"]
    seen: set[str] = set()
    for encoding in encodings:
        if encoding in seen:
            continue
        seen.add(encoding)
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def execute_command(root: Path, command: str, timeout: int = 60) -> str:
    if not command or not command.strip():
        return "Error: empty command"
    timeout = max(1, min(int(timeout or 60), 300))

    timed_out = False
    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=str(root),
            capture_output=True,
            timeout=timeout,
        )
        exit_code = completed.returncode
        stdout = _decode_output(completed.stdout or b"")
        stderr = _decode_output(completed.stderr or b"")
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = None
        stdout = _decode_output(exc.stdout or b"")
        stderr = _decode_output(exc.stderr or b"")

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
