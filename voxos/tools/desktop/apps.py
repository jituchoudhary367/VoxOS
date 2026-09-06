"""
Desktop Application Control Tools.

Provides tools for launching apps, listing windows, and closing them.
"""

from __future__ import annotations

import os
import platform
import subprocess
from typing import Any

from pydantic import BaseModel, Field

from voxos.tools.base import BaseTool, RiskLevel, ToolMetadata, ToolResult, VerificationType


class ListAppsArgs(BaseModel):
    pass


class ListAppsTool(BaseTool):
    """
    Lists currently open windows.
    """

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="desktop.list_apps",
            description="List titles of all currently visible open windows on the desktop.",
            category="desktop",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=10,
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return ListAppsArgs

    async def _execute(self, arguments: dict[str, Any]) -> ToolResult:
        try:
            import pygetwindow as gw
        except ImportError:
            return ToolResult.fail("pygetwindow is not installed.")

        windows = gw.getAllTitles()
        # Filter out empty or hidden windows
        visible_windows = [w for w in windows if w.strip()]
        return ToolResult.ok({"windows": visible_windows})


class OpenAppArgs(BaseModel):
    app_path_or_name: str = Field(
        description="Path to the executable or document, or name of a known application (e.g., 'notepad', 'calc')."
    )


class OpenAppTool(BaseTool):
    """
    Opens an application or file.
    """

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="desktop.open_app",
            description="Launch an application or open a file using its default program.",
            category="desktop",
            risk_level=RiskLevel.MEDIUM,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=30,
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return OpenAppArgs

    async def _execute(self, arguments: dict[str, Any]) -> ToolResult:
        target = arguments["app_path_or_name"]

        try:
            if platform.system() == "Windows":
                os.startfile(target)
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", target])
            else:
                subprocess.Popen(["xdg-open", target])
            
            return ToolResult.ok({"status": f"Successfully launched {target}"})
        except FileNotFoundError:
            return ToolResult.fail(f"Could not find application or file: {target}", "NOT_FOUND")
        except Exception as e:
            return ToolResult.fail(f"Failed to open {target}: {str(e)}", "OPEN_FAILED")


class CloseAppArgs(BaseModel):
    window_title: str = Field(
        description="The exact or partial title of the window to close."
    )


class CloseAppTool(BaseTool):
    """
    Closes a window by title.
    """

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="desktop.close_app",
            description="Close an application window by its title.",
            category="desktop",
            risk_level=RiskLevel.MEDIUM,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=30,
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return CloseAppArgs

    async def _execute(self, arguments: dict[str, Any]) -> ToolResult:
        title = arguments["window_title"]

        try:
            import pygetwindow as gw
        except ImportError:
            return ToolResult.fail("pygetwindow is not installed.")

        windows = gw.getWindowsWithTitle(title)
        if not windows:
            return ToolResult.fail(f"No window found matching '{title}'", "NOT_FOUND")

        closed_count = 0
        errors = []
        for win in windows:
            try:
                win.close()
                closed_count += 1
            except Exception as e:
                errors.append(str(e))

        if errors:
            return ToolResult.fail(f"Failed to close some windows: {errors}", "CLOSE_FAILED")
        
        return ToolResult.ok({"status": f"Closed {closed_count} window(s) matching '{title}'."})
