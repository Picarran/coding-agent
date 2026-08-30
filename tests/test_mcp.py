"""Tests for MCP integration (V2-9): config, stdio client, manager, risk, wiring.

No real MCP server is required: a tiny in-process Python script plays the server
role over stdin/stdout, so the JSON-RPC round trip is exercised deterministically.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from src.core.models import ToolCall
from src.mcp.client import MCPClient, MCPError
from src.mcp.config import (
    MCPServerConfig,
    default_config_path,
    load_mcp_config,
)
from src.mcp.manager import MCPManager, tool_name
from src.safety.permissions import Decision, PermissionChecker, PermissionMode, RiskScorer
from src.tools.definitions import ToolDefinition

# A minimal MCP server written as a standalone script. It answers initialize,
# notifications/initialized, tools/list (one `echo` tool) and tools/call.
_FAKE_SERVER = r'''
import json
import sys

def read():
    line = sys.stdin.readline()
    if not line:
        return None
    return json.loads(line)

def write(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

while True:
    req = read()
    if req is None:
        break
    method = req.get("method")
    mid = req.get("id")
    if method == "initialize":
        write({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake", "version": "1.0"},
        }})
    elif method == "notifications/initialized":
        continue
    elif method == "tools/list":
        write({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
            {
                "name": "echo",
                "description": "echo a message back",
                "inputSchema": {
                    "type": "object",
                    "properties": {"message": {"type": "string"}},
                    "required": ["message"],
                },
            }
        ]}})
    elif method == "tools/call":
        if req["params"].get("name") != "echo":
            write({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": "no such tool"}],
                "isError": True,
            }})
        else:
            args = req["params"].get("arguments", {})
            write({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": "echo: " + args.get("message", "")}],
                "isError": False,
            }})
    else:
        write({"jsonrpc": "2.0", "id": mid, "error": {
            "code": -32601, "message": "method not found",
        }})
'''


def _write_fake_server(directory: str) -> Path:
    path = Path(directory) / "fake_mcp_server.py"
    path.write_text(_FAKE_SERVER, encoding="utf-8")
    return path


class ConfigTest(unittest.TestCase):
    def test_loads_valid_config(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "mcp.json"
            p.write_text(
                json.dumps(
                    {
                        "servers": {
                            "fs": {
                                "command": "npx",
                                "args": ["-y", "@modelcontextprotocol/server-filesystem", "/x"],
                                "env": {"A": "B"},
                                "timeout": 30,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            configs = load_mcp_config(p)
        self.assertEqual(len(configs), 1)
        cfg = configs[0]
        self.assertEqual(cfg.name, "fs")
        self.assertEqual(cfg.command, "npx")
        self.assertEqual(cfg.timeout, 30)
        self.assertEqual(cfg.env["A"], "B")

    def test_missing_servers_raises(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "mcp.json"
            p.write_text(json.dumps({"other": 1}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_mcp_config(p)

    def test_missing_command_raises(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "mcp.json"
            p.write_text(json.dumps({"servers": {"x": {"args": []}}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_mcp_config(p)

    def test_default_config_path(self):
        self.assertEqual(
            default_config_path(Path("/tmp/ws")),
            Path("/tmp/ws") / ".coding-agent" / "mcp.json",
        )


class ToolNameTest(unittest.TestCase):
    def test_namespaced_by_server(self):
        self.assertEqual(tool_name("github", "list_issues"), "mcp__github__list_issues")


class ClientTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.server = _write_fake_server(self._tmp.name)
        self.client = MCPClient(
            "fake", sys.executable, [str(self.server)], timeout=10
        )

    def tearDown(self):
        self.client.close()
        self._tmp.cleanup()

    def test_initialize_list_and_call_round_trip(self):
        self.client.start()
        tools = self.client.list_tools()
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].name, "echo")
        self.assertEqual(tools[0].input_schema["type"], "object")
        self.assertEqual(self.client.call("echo", {"message": "hi"}), "echo: hi")

    def test_unknown_tool_call_raises(self):
        self.client.start()
        with self.assertRaises(MCPError) as ctx:
            self.client.call("missing", {})
        # The server's own error text must surface, not a bare "isError".
        self.assertIn("no such tool", str(ctx.exception))


class ManagerTest(unittest.TestCase):
    def test_start_wraps_tools_and_routes_calls(self):
        with tempfile.TemporaryDirectory() as d:
            server = _write_fake_server(d)
            manager = MCPManager()
            tools = manager.start(
                [
                    MCPServerConfig(
                        name="fake", command=sys.executable, args=[str(server)], timeout=10
                    )
                ]
            )
            try:
                self.assertEqual(len(tools), 1)
                self.assertEqual(tools[0].name, "mcp__fake__echo")
                self.assertIn("[MCP:fake]", tools[0].description)
                self.assertEqual(tools[0].func(message="yo"), "echo: yo")
                self.assertIn("mcp__fake__echo", manager.describe()[0])
            finally:
                manager.close()

    def test_broken_server_is_skipped_not_fatal(self):
        manager = MCPManager()
        tools = manager.start(
            [
                MCPServerConfig(
                    name="bad", command="definitely-not-a-real-command", args=[]
                )
            ]
        )
        try:
            self.assertEqual(tools, [])
        finally:
            manager.close()


class RiskTest(unittest.TestCase):
    def test_mcp_tool_has_shell_base_risk(self):
        scorer = RiskScorer()
        self.assertEqual(scorer.score(ToolCall(id="1", name="mcp__x__y")), 3)

    def test_mcp_tool_asks_in_default_mode(self):
        checker = PermissionChecker.from_mode(PermissionMode.DEFAULT)
        self.assertEqual(
            checker.check(ToolCall(id="1", name="mcp__x__y")).decision, Decision.ASK
        )

    def test_mcp_tool_auto_allows_in_autonomous(self):
        checker = PermissionChecker.from_mode(PermissionMode.AUTONOMOUS)
        self.assertEqual(
            checker.check(ToolCall(id="1", name="mcp__x__y")).decision, Decision.AUTO_ALLOW
        )


class WiringTest(unittest.TestCase):
    def test_extra_tools_registered_in_fast_single_agent(self):
        from src.agents.single_agent import build_single_agent

        extra = ToolDefinition(
            name="mcp__fake__echo",
            description="[MCP:fake] echo",
            parameters={"type": "object", "properties": {"message": {"type": "string"}}},
            func=lambda message="": "ok",
        )
        with tempfile.TemporaryDirectory() as d:
            agent = build_single_agent(
                Path(d), _StubLLM(), max_steps=5, extra_tools=[extra]
            )
            names = [
                s["function"]["name"] for s in agent._loop._executor.tool_schemas
            ]
        self.assertIn("mcp__fake__echo", names)

    def test_extra_tools_registered_in_main_agent_roles(self):
        from src.main import build_main_agent

        extra = ToolDefinition(
            name="mcp__fake__echo",
            description="[MCP:fake] echo",
            parameters={"type": "object", "properties": {"message": {"type": "string"}}},
            func=lambda message="": "ok",
        )
        with tempfile.TemporaryDirectory() as d:
            agent = build_main_agent(
                Path(d), _StubLLM(), max_steps=5, extra_tools=[extra]
            )
            coding = agent._agents["coding"]
            names = [s["function"]["name"] for s in coding._loop._executor.tool_schemas]
        self.assertIn("mcp__fake__echo", names)


class _StubLLM:
    def chat(self, messages, tools=None):
        raise AssertionError("LLM must not be called during these tests")


if __name__ == "__main__":
    unittest.main()
