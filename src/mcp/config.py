"""MCP configuration (V2-9): read the server list from a JSON file.

Default location is ``<workspace>/.coding-agent/mcp.json``; the CLI
``--mcp-config <path>`` overrides it. Format::

    {
      "servers": {
        "github": {
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-github"],
          "env": {"GITHUB_TOKEN": "..."},
          "timeout": 60
        }
      }
    }

- ``command``: the executable (resolved via PATH).
- ``args``: its arguments (a list of strings).
- ``env``: optional extra environment variables for that server process.
- ``timeout``: per-call timeout in seconds (default 60).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60


@dataclass
class MCPServerConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    timeout: int = DEFAULT_TIMEOUT


def default_config_path(root: Path) -> Path:
    """The conventional location: ``<workspace>/.coding-agent/mcp.json``."""
    return Path(root) / ".coding-agent" / "mcp.json"


def load_mcp_config(path: Path) -> list[MCPServerConfig]:
    """Parse an MCP config file into server configs; raise ValueError if malformed."""
    path = Path(path)
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or not isinstance(data.get("servers"), dict):
        raise ValueError("mcp.json must be an object with a 'servers' object")
    configs: list[MCPServerConfig] = []
    for name, spec in data["servers"].items():
        if not isinstance(spec, dict):
            raise ValueError(f"server '{name}' must be an object")
        command = spec.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ValueError(f"server '{name}' is missing a 'command' string")
        args = spec.get("args") or []
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise ValueError(f"server '{name}' 'args' must be a list of strings")
        env = spec.get("env") or {}
        if not isinstance(env, dict):
            raise ValueError(f"server '{name}' 'env' must be an object")
        timeout = spec.get("timeout", DEFAULT_TIMEOUT)
        try:
            timeout = int(timeout)
        except (TypeError, ValueError):
            raise ValueError(f"server '{name}' 'timeout' must be an integer")
        configs.append(
            MCPServerConfig(
                name=name,
                command=command,
                args=list(args),
                env={str(k): str(v) for k, v in env.items()},
                timeout=timeout,
            )
        )
    return configs
