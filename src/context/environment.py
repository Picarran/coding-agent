"""Startup environment info, injected into system prompts as conversation context.

Gathering platform facts once at startup (and telling the model about them)
avoids platform mistakes such as using Unix ``ls`` on Windows. This mirrors how
mature coding agents inject environment context into the system prompt.
"""
from __future__ import annotations

import platform
from datetime import datetime
from pathlib import Path


def build_environment_context(workspace_root: Path) -> str:
    root = Path(workspace_root).resolve()
    system = platform.system() or "Unknown"
    release = platform.release()
    if system == "Windows":
        shell_note = (
            "Shell is Windows cmd/PowerShell. Use `dir`, `Get-ChildItem`, or Python "
            "for file/process operations; Unix commands like `ls`/`grep` are NOT available."
        )
    else:
        shell_note = "Shell is POSIX (sh). Unix commands like `ls`/`grep` are available."

    return "\n".join(
        [
            "## Environment",
            f"- OS: {system} {release}",
            f"- Python: {platform.python_version()}",
            f"- Workspace: {root}",
            f"- Date: {datetime.now().strftime('%Y-%m-%d')}",
            f"- {shell_note}",
        ]
    )
