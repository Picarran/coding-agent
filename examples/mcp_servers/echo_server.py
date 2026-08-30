"""A tiny example MCP server for the coding-agent's MCP support (V2-9).

Speaks the MCP stdio JSON-RPC protocol: ``initialize`` -> ``notifications/initialized``
-> ``tools/list`` -> ``tools/call``. Exposes three tools: ``echo``, ``now``, ``add``.

Register it in ``<workspace>/.coding-agent/mcp.json``::

    {
      "servers": {
        "demo": {
          "command": "python",
          "args": ["examples/mcp_servers/echo_server.py"]
        }
      }
    }

The agent resolves relative script paths against its working directory, so run
``python -m src.main ...`` from the project root (or use an absolute path here).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime


def _write(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


TOOLS = [
    {
        "name": "echo",
        "description": "Echo a message back (for verifying MCP wiring).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to echo."}
            },
            "required": ["text"],
        },
    },
    {
        "name": "now",
        "description": "Return the current date and time.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "add",
        "description": "Add two numbers together.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "number", "description": "First number."},
                "b": {"type": "number", "description": "Second number."},
            },
            "required": ["a", "b"],
        },
    },
]


def _call(name: str, arguments: dict) -> str | None:
    if name == "echo":
        return str(arguments.get("text", ""))
    if name == "now":
        return datetime.now().isoformat(timespec="seconds")
    if name == "add":
        return str(float(arguments.get("a", 0)) + float(arguments.get("b", 0)))
    return None  # unknown tool -> isError


def main() -> None:
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = req.get("method")
        mid = req.get("id")
        if method == "initialize":
            _write(
                {
                    "jsonrpc": "2.0",
                    "id": mid,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "echo-server", "version": "1.0"},
                    },
                }
            )
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            _write({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            params = req.get("params", {})
            name = params.get("name", "")
            result = _call(name, params.get("arguments", {}))
            if result is None:
                _write(
                    {
                        "jsonrpc": "2.0",
                        "id": mid,
                        "result": {
                            "content": [{"type": "text", "text": f"unknown tool: {name}"}],
                            "isError": True,
                        },
                    }
                )
            else:
                _write(
                    {
                        "jsonrpc": "2.0",
                        "id": mid,
                        "result": {
                            "content": [{"type": "text", "text": result}],
                            "isError": False,
                        },
                    }
                )
        else:
            _write(
                {
                    "jsonrpc": "2.0",
                    "id": mid,
                    "error": {"code": -32601, "message": f"method not found: {method}"},
                }
            )


if __name__ == "__main__":
    main()
