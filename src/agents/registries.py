"""Role-specific tool registries for the SubAgents (tool permission isolation)."""
from __future__ import annotations

from pathlib import Path

from src.tools.command_tools import build_command_tools
from src.tools.file_tools import build_file_tools
from src.tools.patch_tools import build_patch_tools
from src.tools.registry import ToolRegistry
from src.tools.search_tools import build_search_tools


def _registry_from(root: Path, builders) -> ToolRegistry:
    registry = ToolRegistry()
    for builder in builders:
        for tool in builder(root):
            registry.register(tool)
    return registry


def build_explorer_registry(root: Path) -> ToolRegistry:
    """Read-only + search + check commands (no patch_file / write_file)."""
    return _registry_from(root, [build_file_tools, build_search_tools, build_command_tools])


def build_coding_registry(root: Path) -> ToolRegistry:
    """Full toolset, including modification."""
    return _registry_from(
        root, [build_file_tools, build_search_tools, build_patch_tools, build_command_tools]
    )


def build_test_registry(root: Path) -> ToolRegistry:
    """Run tests and inspect results (no patch_file / write_file)."""
    return _registry_from(root, [build_file_tools, build_search_tools, build_command_tools])
