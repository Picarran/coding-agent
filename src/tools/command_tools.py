"""Command tool: ``execute_command``, run inside the workspace with a timeout."""
from __future__ import annotations

import hashlib
import locale
import os
import re
import subprocess
import tempfile
from functools import partial
from pathlib import Path

from src.tools.definitions import ToolDefinition

_COMPRESS_THRESHOLD = 2000

_KEY_LINE_RE = re.compile(
    r"(error|traceback|exception|assert|fail|File \".*\", line \d+|line \d+, in)",
    re.IGNORECASE,
)


def compress_command_output(text: str, max_chars: int = 1500) -> str:
    """Keep only the header, key lines (errors/tracebacks/assertions), and the tail.

    This is V1-3-3: long command output is shrunk to the lines the model can act
    on, instead of feeding it thousands of noise lines.
    """
    if len(text) <= max_chars:
        return text
    lines = text.splitlines()
    head = lines[:4]
    key = [ln for ln in lines if _KEY_LINE_RE.search(ln)]
    tail = lines[-12:]
    seen: set[str] = set()
    kept: list[str] = []
    for ln in head + key + tail:
        if ln not in seen:
            seen.add(ln)
            kept.append(ln)
    omitted = len(lines) - len(kept)
    return "\n".join(kept) + f"\n...[omitted {omitted} line(s), full output archived]"


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

    # Capture stdout/stderr to temp FILES, not pipes. A command that spawns a
    # detached background process (e.g. ``start /b python app.py``) inherits the
    # pipe write-end, so ``communicate()`` blocks waiting for EOF until that
    # process exits. A file handle can't block the parent process from exiting.
    fd_out, out_path = tempfile.mkstemp(suffix=".out")
    fd_err, err_path = tempfile.mkstemp(suffix=".err")
    os.close(fd_out)
    os.close(fd_err)

    timed_out = False
    try:
        with open(out_path, "wb") as out, open(err_path, "wb") as err:
            try:
                completed = subprocess.run(
                    command,
                    shell=True,
                    cwd=str(root),
                    stdout=out,
                    stderr=err,
                    timeout=timeout,
                )
                exit_code = completed.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                exit_code = None
        stdout = _decode_output(Path(out_path).read_bytes())
        stderr = _decode_output(Path(err_path).read_bytes())
    finally:
        for path in (out_path, err_path):
            try:
                os.unlink(path)
            except OSError:
                pass  # a lingering background child may still hold the handle

    output = "\n".join(
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
    return _maybe_compress_and_archive(root, command, output)


def _maybe_compress_and_archive(root: Path, command: str, output: str) -> str:
    """Compress long output and archive the raw text to ``.coding-agent/artifacts/``."""
    if len(output) <= _COMPRESS_THRESHOLD:
        return output
    compressed = compress_command_output(output)
    artifacts = Path(root) / ".coding-agent" / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(command.encode("utf-8")).hexdigest()[:8]
    name = f"cmd-{digest}.log"
    (artifacts / name).write_text(output, encoding="utf-8")
    return compressed + f"\n[full output: .coding-agent/artifacts/{name}]"


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
