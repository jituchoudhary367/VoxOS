"""
Central Tool Registry.

All tools must be registered here before they can be used.
The Executor agent only calls tools via this registry — never directly.

Registration flow:
    1. Create a tool class that extends BaseTool
    2. Call registry.register(MyTool())
    3. The Executor looks up tools by name, validates arguments,
       checks risk, and executes through the tool interface.
"""

from __future__ import annotations

from typing import Any

from voxos.tools.base import BaseTool, RiskLevel, ToolMetadata
from voxos.observability.logger import get_logger

log = get_logger(__name__)


class ToolRegistry:
    """
    Centralized registry for all VoxOS tools.
    
    Thread-safe for reads (dict lookups). Registration should happen
    at startup before any concurrent access.
    """

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool. Raises ValueError on name collision."""
        name = tool.metadata.name
        if name in self._tools:
            raise ValueError(f"Tool '{name}' is already registered. Use a unique name.")
        self._tools[name] = tool
        log.info(
            "tool_registered",
            name=name,
            risk=tool.metadata.risk_level,
            category=tool.metadata.category,
        )

    def get(self, name: str) -> BaseTool | None:
        """Look up a tool by name. Returns None if not found."""
        return self._tools.get(name)

    def get_or_raise(self, name: str) -> BaseTool:
        """Look up a tool by name. Raises ValueError if not found."""
        tool = self._tools.get(name)
        if tool is None:
            available = list(self._tools.keys())
            raise ValueError(f"Tool '{name}' not found. Available: {available}")
        return tool

    def list_tools(
        self,
        category: str | None = None,
        risk_level: RiskLevel | None = None,
    ) -> list[ToolMetadata]:
        """List metadata for all registered (optionally filtered) tools."""
        results = []
        for tool in self._tools.values():
            meta = tool.metadata
            if category and meta.category != category:
                continue
            if risk_level and meta.risk_level != risk_level:
                continue
            results.append(meta)
        return sorted(results, key=lambda m: (m.category, m.name))

    def tool_count(self) -> int:
        return len(self._tools)

    def get_all_tools_metadata(self) -> list[dict[str, Any]]:
        """Return JSON-friendly metadata for legacy API consumers."""
        return [
            {
                "name": metadata.name,
                "description": metadata.description,
                "category": metadata.category,
                "risk_level": metadata.risk_level.value,
                "schema": tool.input_schema.model_json_schema(),
            }
            for tool in self._tools.values()
            for metadata in [tool.metadata]
        ]

    def to_openai_tools(
        self,
        names: list[str] | None = None,
        max_risk: RiskLevel = RiskLevel.HIGH,
    ) -> list[dict[str, Any]]:
        """
        Generate OpenAI function tool specs for LLM tool-calling.
        
        Args:
            names: Specific tool names to include (None = all)
            max_risk: Maximum risk level to include
        """
        risk_order = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]
        max_idx = risk_order.index(max_risk)

        specs = []
        for name, tool in self._tools.items():
            if names and name not in names:
                continue
            tool_risk_idx = risk_order.index(tool.metadata.risk_level)
            if tool_risk_idx > max_idx:
                continue
            specs.append(tool.to_openai_tool_spec())
        return specs

    def has_tool(self, name: str) -> bool:
        return name in self._tools


# Module-level singleton
_registry: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    """Return the global tool registry singleton."""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
        _register_default_tools(_registry)
    return _registry


def _register_default_tools(registry: ToolRegistry) -> None:
    """Register all built-in tools at startup."""
    # System
    from voxos.tools.system.tools import (
        GetSystemInfoTool, GetCurrentTimeTool, GetSystemResourcesTool,
    )
    # OS / Shell
    from voxos.tools.os.shell import ShellCommandTool
    # Filesystem — core
    from voxos.tools.os.fs import (
        ReadFileTool, WriteFileTool, ListDirTool,
        CreateFolderTool, CopyFileTool, MoveFileTool,
        RenameFileTool, DeleteFileTool, SearchFilesTool,
    )
    # Web search
    from voxos.tools.web_search.tools import WebSearchTool, WebExtractTool
    # Browser automation
    from voxos.tools.browser.tools import (
        BrowserOpenTool, BrowserExtractTool, BrowserSearchTool,
        BrowserClickTool, BrowserTypeTool, BrowserNewTabTool,
        BrowserBackTool, BrowserScrollTool, BrowserScreenshotTool,
    )
    from voxos.tools.browser.youtube import YouTubeSearchTool, YouTubePlayTool
    from voxos.tools.browser.media import BrowserMediaTool
    from voxos.tools.vision.tools import VisionLookTool
    # Desktop
    from voxos.tools.desktop.screen import DesktopScreenshotTool
    from voxos.tools.desktop.apps import ListAppsTool, OpenAppTool, CloseAppTool
    from voxos.tools.desktop.window import (
        ListWindowsTool, FocusWindowTool, MinimizeWindowTool, MaximizeWindowTool,
    )
    from voxos.tools.desktop.input import (
        KeyboardTypeTool, HotkeyTool, KeyPressTool, MouseClickTool, MouseScrollTool,
    )
    from voxos.tools.desktop.clipboard import ClipboardReadTool, ClipboardWriteTool
    # Documents
    from voxos.tools.documents.tools import DocumentReadTool
    # Memory
    from voxos.tools.memory.tools import MemorySaveTool, MemorySearchTool
    # Code
    from voxos.tools.code.repl import PythonREPLTool

    # ── System ────────────────────────────────────────────────────────────────
    registry.register(GetSystemInfoTool())
    registry.register(GetCurrentTimeTool())
    registry.register(GetSystemResourcesTool())

    # ── Shell ─────────────────────────────────────────────────────────────────
    registry.register(ShellCommandTool())

    # ── Filesystem ────────────────────────────────────────────────────────────
    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    registry.register(ListDirTool())
    registry.register(CreateFolderTool())
    registry.register(CopyFileTool())
    registry.register(MoveFileTool())
    registry.register(RenameFileTool())
    registry.register(DeleteFileTool())
    registry.register(SearchFilesTool())

    # ── Web Search ────────────────────────────────────────────────────────────
    registry.register(WebSearchTool())
    registry.register(WebExtractTool())

    # ── Browser Automation ────────────────────────────────────────────────────
    registry.register(BrowserOpenTool())
    registry.register(BrowserExtractTool())
    registry.register(BrowserSearchTool())
    registry.register(BrowserClickTool())
    registry.register(BrowserTypeTool())
    registry.register(BrowserNewTabTool())
    registry.register(BrowserBackTool())
    registry.register(BrowserScrollTool())
    registry.register(BrowserScreenshotTool())
    registry.register(YouTubeSearchTool())
    registry.register(YouTubePlayTool())
    registry.register(BrowserMediaTool())

    # ── Vision ────────────────────────────────────────────────────────────────
    registry.register(VisionLookTool())

    # ── Desktop — Screenshots & Apps ─────────────────────────────────────────
    registry.register(DesktopScreenshotTool())
    registry.register(ListAppsTool())
    registry.register(OpenAppTool())
    registry.register(CloseAppTool())

    # ── Desktop — Windows ─────────────────────────────────────────────────────
    registry.register(ListWindowsTool())
    registry.register(FocusWindowTool())
    registry.register(MinimizeWindowTool())
    registry.register(MaximizeWindowTool())

    # ── Desktop — Input ───────────────────────────────────────────────────────
    registry.register(KeyboardTypeTool())
    registry.register(HotkeyTool())
    registry.register(KeyPressTool())
    registry.register(MouseClickTool())
    registry.register(MouseScrollTool())

    # ── Desktop — Clipboard ───────────────────────────────────────────────────
    registry.register(ClipboardReadTool())
    registry.register(ClipboardWriteTool())

    # ── Documents ─────────────────────────────────────────────────────────────
    registry.register(DocumentReadTool())

    # ── Memory ────────────────────────────────────────────────────────────────
    registry.register(MemorySaveTool())
    registry.register(MemorySearchTool())

    # ── Code ──────────────────────────────────────────────────────────────────
    registry.register(PythonREPLTool())

    log.info("default_tools_registered", count=registry.tool_count())

