"""Minimal MCP stdio client (V2-9), zero new dependencies.

Talks JSON-RPC to an MCP server over its stdin/stdout (newline-delimited JSON):
spawn the process -> ``initialize`` handshake -> ``tools/list`` -> ``tools/call``.
Failures are raised as ``MCPError`` so the ``ToolExecutor`` normalizes them like
any other tool error (an observation, not a crash).

A background reader thread drains the server's stdout into a queue so that a
per-call timeout can be enforced even on Windows, where ``select`` does not work
on pipe objects.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# The protocol version we advertise during the initialize handshake.
PROTOCOL_VERSION = "2024-11-05"

# Generous startup timeout: a first ``uvx``/``npx`` run may download the server's
# dependencies before it answers ``initialize`` / ``tools/list`` (observed ~46s
# for a cold ``uvx mcp-server-fetch``).
STARTUP_TIMEOUT = 120


class MCPError(RuntimeError):
    """An MCP transport/protocol-level failure (server error, timeout, crash)."""


@dataclass
class MCPTool:
    """A tool discovered from an MCP server via ``tools/list``."""

    name: str
    description: str
    input_schema: dict[str, Any]


def _truncate_error(detail: str, limit: int = 1500) -> str:
    """Cap an error message so a huge server error doesn't flood the context."""
    if len(detail) <= limit:
        return detail
    return detail[:limit] + f"\n...[truncated {len(detail) - limit} chars]"


def _spawn(command: str, args: list[str], env: dict[str, str], cwd: str | None):
    """Spawn the server process, transparently handling Windows ``.cmd`` shims."""
    kwargs: dict[str, Any] = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.DEVNULL,
        "env": env,
        "cwd": cwd,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
    }
    if os.name == "nt":
        resolved = shutil.which(command)
        if resolved and resolved.lower().endswith((".cmd", ".bat")):
            # `npx`, `node`, etc. resolve to .cmd/.bat shims that cannot be exec'd
            # directly; run them through the shell with a properly quoted line.
            cmdline = subprocess.list2cmdline([command] + list(args))
            return subprocess.Popen(cmdline, shell=True, **kwargs)
    return subprocess.Popen([command] + list(args), shell=False, **kwargs)


class MCPClient:
    """A long-lived connection to one MCP server process (stdio transport)."""

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        timeout: int = 60,
    ) -> None:
        self._name = name
        self._command = command
        self._args = list(args or [])
        self._extra_env = dict(env or {})
        self._cwd = cwd
        self._timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._out_q: "queue.Queue[str | None]" = queue.Queue()
        self._next_id = 0

    @property
    def name(self) -> str:
        return self._name

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        """Spawn the server and complete the initialize handshake."""
        if self._proc is not None:
            return
        env = dict(os.environ)
        env.update(self._extra_env)
        try:
            self._proc = _spawn(self._command, self._args, env=env, cwd=self._cwd)
        except OSError as exc:
            raise MCPError(
                f"failed to start MCP server '{self._name}' ({self._command}): {exc}"
            ) from exc
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        try:
            result = self._request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "coding-agent", "version": "0.1.0"},
                },
                timeout=STARTUP_TIMEOUT,
            )
            self._notify("notifications/initialized", {})
        except MCPError as exc:
            self.close()
            raise MCPError(f"MCP server '{self._name}' initialize failed: {exc}") from exc
        logger.info(
            "MCP server '%s' initialized (protocol %s)",
            self._name,
            result.get("protocolVersion") or PROTOCOL_VERSION,
        )

    def close(self) -> None:
        """Stop the server process and release pipes."""
        if self._proc is None:
            return
        proc, self._proc = self._proc, None
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        except OSError:
            pass
        # Close the remaining pipes so no file descriptors leak (the reader
        # thread's ``for line in stdout`` raises ValueError on a closed stream,
        # which its except clause swallows).
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream:
                    stream.close()
            except (OSError, ValueError):
                pass

    # -- protocol -----------------------------------------------------------
    def list_tools(self) -> list[MCPTool]:
        """Discover the tools the server exposes (``tools/list``)."""
        result = self._request("tools/list", {}, timeout=STARTUP_TIMEOUT)
        tools: list[MCPTool] = []
        for item in result.get("tools", []):
            if not isinstance(item, dict) or not item.get("name"):
                continue
            schema = item.get("inputSchema") or {}
            if not isinstance(schema, dict):
                schema = {}
            schema.setdefault("type", "object")
            schema.setdefault("properties", {})
            tools.append(
                MCPTool(
                    name=item["name"],
                    description=str(item.get("description") or "").strip(),
                    input_schema=schema,
                )
            )
        return tools

    def call(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Invoke a tool (``tools/call``) and return its text content.

        On ``isError`` the server's own error text is surfaced in the exception,
        so the model sees *why* the call failed instead of a bare "isError".
        """
        result = self._request(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
            timeout=self._timeout,
        )
        content = result.get("content") or []
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            else:
                parts.append(json.dumps(block, ensure_ascii=False, default=str))
        text = "\n".join(parts)
        if result.get("isError"):
            detail = (
                text if text.strip()
                else json.dumps(result, ensure_ascii=False, default=str)
            )
            raise MCPError(
                f"server '{self._name}' tool '{tool_name}' failed: {_truncate_error(detail)}"
            )
        return text if text.strip() else json.dumps(result, ensure_ascii=False, default=str)

    # -- plumbing -----------------------------------------------------------
    def _request(self, method: str, params: dict, timeout: int) -> dict:
        if self._proc is None or self._proc.poll() is not None:
            raise MCPError(f"MCP server '{self._name}' is not running")
        self._next_id += 1
        mid = self._next_id
        self._write({"jsonrpc": "2.0", "id": mid, "method": method, "params": params})
        while True:
            line = self._read_line(timeout)
            if line is None:
                raise MCPError(
                    f"MCP server '{self._name}' closed stdout while awaiting '{method}'"
                )
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("MCP server '%s' sent non-JSON line: %r", self._name, line[:200])
                continue
            if msg.get("id") != mid:
                continue  # a notification or out-of-order response; ignore
            if "error" in msg:
                err = msg["error"] or {}
                raise MCPError(
                    f"server '{self._name}' {method} error {err.get('code')}: "
                    f"{err.get('message')}"
                )
            return msg.get("result") or {}

    def _notify(self, method: str, params: dict) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _write(self, msg: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise MCPError(f"MCP server '{self._name}' is not running")
        try:
            self._proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise MCPError(f"write to MCP server '{self._name}' failed: {exc}") from exc

    def _read_loop(self) -> None:
        """Drain server stdout lines into the queue; push None on EOF."""
        proc = self._proc
        if proc is None or proc.stdout is None:
            self._out_q.put(None)
            return
        try:
            for line in proc.stdout:
                self._out_q.put(line)
        except (OSError, ValueError):
            pass
        finally:
            self._out_q.put(None)

    def _read_line(self, timeout: int) -> str | None:
        try:
            return self._out_q.get(timeout=timeout)
        except queue.Empty:
            raise MCPError(
                f"timeout awaiting MCP server '{self._name}' response"
            ) from None
