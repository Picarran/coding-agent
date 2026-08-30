"""MCP (Model Context Protocol) integration (V2-9).

Exposes external MCP servers as first-class agent tools under an ``mcp__``
prefix, reusing the existing ``ToolExecutor`` (validation / permission / audit /
error normalization) unchanged. Zero new dependencies: the client speaks
JSON-RPC over stdio with the standard library only.
"""
from src.mcp.client import MCPClient, MCPError, MCPTool
from src.mcp.config import MCPServerConfig, default_config_path, load_mcp_config
from src.mcp.manager import MCPManager, tool_name

__all__ = [
    "MCPClient",
    "MCPError",
    "MCPTool",
    "MCPServerConfig",
    "default_config_path",
    "load_mcp_config",
    "MCPManager",
    "tool_name",
]
