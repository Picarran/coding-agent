"""MCP manager (V2-9): own the server processes and expose their tools.

Turns a list of ``MCPServerConfig`` into ``ToolDefinition``s registered under
the ``mcp__<server>__<tool>`` namespace, so they flow through the existing
``ToolExecutor`` (validation / permission / audit / error normalization)
unchanged. A server that fails to start is logged and skipped, never aborting
the whole agent.
"""
from __future__ import annotations

import logging
from typing import Any

from src.mcp.client import MCPClient, MCPError, MCPTool
from src.mcp.config import MCPServerConfig
from src.tools.definitions import ToolDefinition

logger = logging.getLogger(__name__)

MCP_PREFIX = "mcp__"


def tool_name(server: str, tool: str) -> str:
    """The agent-visible name of an MCP tool, namespaced by server."""
    return f"{MCP_PREFIX}{server}__{tool}"


class MCPManager:
    """Starts configured servers, wraps their tools, and owns their lifecycle."""

    def __init__(self) -> None:
        self._clients: dict[str, MCPClient] = {}
        self._tools: list[ToolDefinition] = []
        self._discovered: dict[str, list[str]] = {}

    def start(
        self, configs: list[MCPServerConfig], cwd: str | None = None
    ) -> list[ToolDefinition]:
        """Start every configured server and collect its tools (resilient).

        ``cwd`` is the workspace root: relative ``args`` (e.g. a local server
        script path) resolve against it, not the agent process's own cwd.
        """
        self._tools = []
        self._discovered = {}
        for cfg in configs:
            if cfg.name in self._clients:
                logger.warning("MCP server '%s' already started; skipping", cfg.name)
                continue
            client = MCPClient(
                cfg.name, cfg.command, cfg.args, cfg.env, timeout=cfg.timeout, cwd=cwd
            )
            try:
                client.start()
                tools = client.list_tools()
            except MCPError as exc:
                logger.warning("MCP server '%s' failed: %s", cfg.name, exc)
                client.close()
                continue
            self._clients[cfg.name] = client
            self._discovered[cfg.name] = []
            for t in tools:
                self._tools.append(self._wrap(cfg.name, client, t))
                self._discovered[cfg.name].append(tool_name(cfg.name, t.name))
            logger.info("MCP server '%s' contributed %d tool(s)", cfg.name, len(tools))
        return self._tools

    def tools(self) -> list[ToolDefinition]:
        return list(self._tools)

    def describe(self) -> list[str]:
        """Human-readable summary of what was discovered, for startup output."""
        lines: list[str] = []
        for server, names in self._discovered.items():
            lines.append(f"  {server}: {', '.join(names)}")
        return lines

    def close(self) -> None:
        for client in self._clients.values():
            client.close()
        self._clients.clear()
        self._tools = []
        self._discovered = {}

    def _wrap(self, server: str, client: MCPClient, tool: MCPTool) -> ToolDefinition:
        schema = dict(tool.input_schema)
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})

        def call(**kwargs: Any) -> str:
            return client.call(tool.name, kwargs)

        return ToolDefinition(
            name=tool_name(server, tool.name),
            description=f"[MCP:{server}] {tool.description}".strip(),
            parameters=schema,
            func=call,
        )
