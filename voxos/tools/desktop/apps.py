"""
Desktop Application Control Tools.

Provides tools for launching apps, listing windows, and closing them.
Uses the AppDiscovery engine for robust application resolution.
"""

from __future__ import annotations

import asyncio
import os
import platform
import subprocess

import psutil
from pydantic import BaseModel, Field

import re

from voxos.observability.logger import get_logger

_ASKS_TO_CLOSE = re.compile(r"\b(?:close|quit|exit|kill|shut|terminate|stop)\b", re.IGNORECASE)
from voxos.tools.base import BaseTool, RiskLevel, ToolMetadata, ToolResult, VerificationResult, VerificationType

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# List Apps Tool
# ---------------------------------------------------------------------------

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
            description="List titles of all currently visible open windows.",
            category="desktop",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=10,
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return ListAppsArgs

    async def _execute(self, arguments: ListAppsArgs) -> ToolResult:
        try:
            import pygetwindow as gw
        except ImportError:
            return ToolResult.fail("pygetwindow is not installed.")

        windows = gw.getAllTitles()
        visible_windows = [w for w in windows if w.strip()]
        return ToolResult.ok({"windows": visible_windows, "count": len(visible_windows)})


# ---------------------------------------------------------------------------
# Open App Tool
# ---------------------------------------------------------------------------

from pydantic import model_validator

class OpenAppArgs(BaseModel):
    name: str | None = Field(
        default=None,
        description="Name of the application to open (e.g. 'notepad', 'chrome', 'vs code')."
    )
    application: str | None = Field(
        default=None,
        description="Alias for name, used by Fast Path listener."
    )
    url: str | None = Field(
        default=None,
        description="Optional URL or file path to open with the application."
    )

    @model_validator(mode="before")
    @classmethod
    def populate_name(cls, data: dict) -> dict:
        if "app_path_or_name" in data and not data.get("name"):
            data["name"] = data["app_path_or_name"]
        if "app_name" in data and not data.get("name"):
            data["name"] = data["app_name"]
        if "application" in data and not data.get("name"):
            data["name"] = data["application"]
        if not data.get("name"):
            raise ValueError("Application name is required.")
        return data


class OpenAppTool(BaseTool):
    """
    Opens an application by name using the Windows App Discovery engine.
    Resolves common names, aliases, registry entries, and Start Menu shortcuts.
    """

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="desktop.open_app",
            description="Launch an application by name. Supports Chrome, Notepad, VS Code, Calculator, and any installed app.",
            category="desktop",
            risk_level=RiskLevel.MEDIUM,
            requires_confirmation=False,
            verification_type=VerificationType.AUTOMATIC,
            timeout_seconds=15,
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return OpenAppArgs

    async def _execute(self, arguments: OpenAppArgs) -> ToolResult:
        from voxos.tools.desktop.discovery import get_app_discovery
        discovery = get_app_discovery()

        target, error = discovery.resolve_launch_target(arguments.name)
        if error:
            log.warning("app_not_found", name=arguments.name)
            return ToolResult.fail(error, "APP_NOT_FOUND")

        log.info("launching_app", name=arguments.name, target=target, url=arguments.url)
        try:
            if platform.system() == "Windows":
                url_to_open = arguments.url
                if not url_to_open and any(browser in arguments.name.lower() for browser in ["chrome", "edge"]):
                    url_to_open = "about:blank"

                if url_to_open:
                    subprocess.Popen(["cmd", "/c", "start", "", target, url_to_open], shell=True)
                else:
                    os.startfile(target)
            elif platform.system() == "Darwin":
                cmd = ["open", "-a", target]
                if arguments.url:
                    cmd.append(arguments.url)
                subprocess.Popen(cmd)
            else:
                cmd = [target]
                if arguments.url:
                    cmd.append(arguments.url)
                subprocess.Popen(cmd)

            # Brief wait for process to appear
            await asyncio.sleep(0.5)

            return ToolResult.ok({
                "status": f"Launched {arguments.name}",
                "target": target,
                "app_name": arguments.name,
            })

        except FileNotFoundError:
            return ToolResult.fail(
                f"Could not find the executable for '{arguments.name}': {target}",
                "APP_NOT_FOUND"
            )
        except Exception as e:
            return ToolResult.fail(f"Failed to open '{arguments.name}': {str(e)}", "OPEN_FAILED")

    async def verify(self, args: OpenAppArgs, result: ToolResult) -> VerificationResult:
        if not result.success:
            return VerificationResult.fail("Launch failed — nothing to verify")

        from voxos.tools.desktop.discovery import _normalize, ALIASES
        app_name = args.name.lower().strip()

        # Build list of process names to look for
        exe_names: list[str] = []
        norm = _normalize(app_name)

        from voxos.tools.desktop.discovery import get_app_discovery
        entry = get_app_discovery().find(app_name)
        if entry:
            exe = entry.executable.lower()
            if exe.endswith(".exe"):
                exe_names.append(exe.split("\\")[-1].split("/")[-1])

        # Fallback: treat the name itself as an exe
        if not exe_names:
            if not app_name.endswith(".exe"):
                exe_names.append(app_name + ".exe")
            else:
                exe_names.append(app_name)

        # Also check window title (fast path via pygetwindow)
        window_found = False
        try:
            import pygetwindow as gw
            all_titles = [t.lower() for t in gw.getAllTitles() if t.strip()]
            window_found = any(app_name in t for t in all_titles)
        except Exception:
            pass

        if window_found:
            return VerificationResult.ok(checks=[{"check": "window_appeared", "passed": True}])

        # Check process list
        try:
            running = [p.name().lower() for p in psutil.process_iter(["name"])]
            for exe in exe_names:
                if exe in running:
                    return VerificationResult.ok(checks=[{"check": "process_appeared", "passed": True}])
        except Exception:
            pass

        # ms-settings: and similar URI schemes can't be verified by process
        target_str = result.data.get("target", "") if result.data else ""
        if target_str.startswith("ms-") or target_str.endswith(":"):
            return VerificationResult.ok(checks=[{"check": "uri_launched", "passed": True}])

        return VerificationResult.fail(
            f"Could not confirm '{args.name}' opened — no matching window or process found",
            checks=[{"check": "window_or_process_appeared", "passed": False}],
        )


# ---------------------------------------------------------------------------
# Close App Tool
# ---------------------------------------------------------------------------

class CloseAppArgs(BaseModel):
    window_title: str = Field(
        description="Partial or full title of the window to close (e.g. 'Notepad', 'Chrome')."
    )
    force: bool = Field(
        default=False,
        description="If True, force-kill the process instead of graceful close. Requires higher approval."
    )


class CloseAppTool(BaseTool):
    """
    Closes a window by title. Supports partial matching.
    Use force=True only to kill an unresponsive process (higher risk).
    """

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="desktop.close_app",
            description="Close an application window by its title. Use force=True to kill an unresponsive process.",
            category="desktop",
            risk_level=RiskLevel.MEDIUM,
            requires_confirmation=False,
            verification_type=VerificationType.AUTOMATIC,
            timeout_seconds=15,
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return CloseAppArgs

    async def _execute(self, arguments: CloseAppArgs) -> ToolResult:
        title = arguments.window_title.strip()

        # Closing matches every window whose title contains `title`, which
        # can include the user's own browser with unsaved work. An agent once
        # closed an "Amazon" window after the user only said "don't buy it".
        # So it needs the user's own words to ask for closing, or a yes.
        from voxos.core.conversation.pending import user_said_yes
        from voxos.core.request_context import current_user_request

        request = current_user_request()
        if request and not (_ASKS_TO_CLOSE.search(request) or user_said_yes(request)):
            log.warning("close_app_needs_confirmation", window=title)
            return ToolResult.fail(
                f"The user did not ask to close anything. Ask before closing '{title}'.",
                "NEEDS_CONFIRMATION",
            )

        try:
            import pygetwindow as gw
        except ImportError:
            return ToolResult.fail("pygetwindow is not installed.")

        # Find matching windows (case-insensitive partial match)
        all_windows = gw.getAllWindows()
        matched = [w for w in all_windows if title.lower() in w.title.lower() and w.title.strip()]

        if not matched:
            return ToolResult.fail(f"No window found matching '{title}'", "NOT_FOUND")

        closed_count = 0
        errors: list[str] = []

        for win in matched:
            try:
                if arguments.force:
                    # Force kill via psutil
                    pid = win._hWnd  # handle; psutil needs pid
                    killed = False
                    for proc in psutil.process_iter(["pid", "name"]):
                        try:
                            if title.lower() in proc.name().lower():
                                proc.terminate()
                                killed = True
                                closed_count += 1
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                    if not killed:
                        win.close()
                        closed_count += 1
                else:
                    win.close()
                    closed_count += 1
            except Exception as e:
                errors.append(str(e))

        if errors and closed_count == 0:
            return ToolResult.fail(f"Failed to close windows: {errors}", "CLOSE_FAILED")

        return ToolResult.ok({
            "status": f"Closed {closed_count} window(s) matching '{title}'.",
            "closed_count": closed_count,
            "target_title": title,
        })

    async def verify(self, args: CloseAppArgs, result: ToolResult) -> VerificationResult:
        if not result.success:
            return VerificationResult.fail("Close failed — nothing to verify")
            
        import pygetwindow as gw
        await asyncio.sleep(0.5)  # give it time to close
        
        title = args.window_title.strip().lower()
        all_windows = gw.getAllWindows()
        still_open = [w for w in all_windows if title in w.title.lower() and w.title.strip()]
        
        if still_open:
            return VerificationResult.fail(
                f"Window '{title}' is still open",
                checks=[{"check": "window_closed", "passed": False}]
            )
            
        return VerificationResult.ok(checks=[{"check": "window_closed", "passed": True}])
