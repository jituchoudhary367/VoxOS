"""
Desktop Window Management Tools.

Focus, minimize, maximize, restore, and list windows
using pygetwindow (wraps Win32 APIs).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from voxos.observability.logger import get_logger
from voxos.tools.base import BaseTool, RiskLevel, ToolResult, ToolMetadata, VerificationType

log = get_logger(__name__)


def _find_windows(title_partial: str) -> list:
    """Find windows whose title contains title_partial (case-insensitive)."""
    try:
        import pygetwindow as gw
        all_windows = gw.getAllWindows()
        return [w for w in all_windows if title_partial.lower() in w.title.lower() and w.title.strip()]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# List Windows
# ---------------------------------------------------------------------------

class ListWindowsArgs(BaseModel):
    pass


class ListWindowsTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="desktop.list_windows",
            description="List all currently open windows with their titles.",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=10,
            category="desktop",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return ListWindowsArgs

    async def _execute(self, args: ListWindowsArgs) -> ToolResult:
        try:
            import pygetwindow as gw
            windows = [
                {"title": w.title, "left": w.left, "top": w.top, "width": w.width, "height": w.height}
                for w in gw.getAllWindows()
                if w.title.strip()
            ]
            return ToolResult.ok(data={"windows": windows, "count": len(windows)})
        except ImportError:
            return ToolResult.fail("pygetwindow is not installed.", "MISSING_DEPENDENCY")
        except Exception as e:
            return ToolResult.fail(str(e), "WINDOW_ERROR")


# ---------------------------------------------------------------------------
# Focus Window
# ---------------------------------------------------------------------------

class FocusWindowArgs(BaseModel):
    window_title: str = Field(..., description="Partial title of the window to focus.")


class FocusWindowTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="desktop.focus_window",
            description="Bring a window to the foreground by its title.",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=10,
            category="desktop",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return FocusWindowArgs

    async def _execute(self, args: FocusWindowArgs) -> ToolResult:
        windows = _find_windows(args.window_title)
        if not windows:
            return ToolResult.fail(f"No window found matching '{args.window_title}'", "NOT_FOUND")
        try:
            win = windows[0]
            if win.isMinimized:
                win.restore()
            win.activate()
            return ToolResult.ok(data={"focused": win.title})
        except Exception as e:
            return ToolResult.fail(str(e), "WINDOW_ERROR")


# ---------------------------------------------------------------------------
# Minimize Window
# ---------------------------------------------------------------------------

class MinimizeWindowArgs(BaseModel):
    window_title: str = Field(..., description="Partial title of the window to minimize.")


class MinimizeWindowTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="desktop.minimize_window",
            description="Minimize a window by title.",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=10,
            category="desktop",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return MinimizeWindowArgs

    async def _execute(self, args: MinimizeWindowArgs) -> ToolResult:
        windows = _find_windows(args.window_title)
        if not windows:
            return ToolResult.fail(f"No window found matching '{args.window_title}'", "NOT_FOUND")
        try:
            windows[0].minimize()
            return ToolResult.ok(data={"minimized": windows[0].title})
        except Exception as e:
            return ToolResult.fail(str(e), "WINDOW_ERROR")


# ---------------------------------------------------------------------------
# Maximize Window
# ---------------------------------------------------------------------------

class MaximizeWindowArgs(BaseModel):
    window_title: str = Field(..., description="Partial title of the window to maximize.")


class MaximizeWindowTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="desktop.maximize_window",
            description="Maximize a window by title.",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=10,
            category="desktop",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return MaximizeWindowArgs

    async def _execute(self, args: MaximizeWindowArgs) -> ToolResult:
        windows = _find_windows(args.window_title)
        if not windows:
            return ToolResult.fail(f"No window found matching '{args.window_title}'", "NOT_FOUND")
        try:
            windows[0].maximize()
            return ToolResult.ok(data={"maximized": windows[0].title})
        except Exception as e:
            return ToolResult.fail(str(e), "WINDOW_ERROR")
