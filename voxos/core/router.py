"""
Execution Router — classifies user intents into three execution tiers.

Class A — FAST_PATH:  Deterministic, no LLM needed.
Class B — AUTOMATION: Browser/desktop automation, no LLM planning needed.
Class C — REASONING:  Complex, multi-step, ambiguous — use Strategist.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from voxos.observability.logger import get_logger

log = get_logger(__name__)


class ExecutionClass(str, Enum):
    FAST_PATH  = "FAST_PATH"   # Class A — deterministic local operation
    AUTOMATION = "AUTOMATION"  # Class B — browser/desktop automation
    REASONING  = "REASONING"   # Class C — LLM planning required


# ---------------------------------------------------------------------------
# FAST PATH intent → tool name mapping
# All these bypass the Strategist entirely.
# ---------------------------------------------------------------------------

FAST_PATH_INTENT_TO_TOOL: dict[str, str] = {
    # System information
    "get_current_time":  "system.get_time",
    "get_time":          "system.get_time",
    "get_date":          "system.get_time",
    "get_system_info":   "system.get_info",
    "get_system_status": "system.get_info",
    "get_resources":     "system.get_resources",
    "check_cpu":         "system.get_resources",
    "check_memory":      "system.get_resources",
    "check_ram":         "system.get_resources",
    "check_disk":        "system.get_resources",
    "check_battery":     "system.get_resources",
    "get_battery":       "system.get_resources",
    "get_hostname":      "system.get_info",
    "get_uptime":        "system.get_info",
    "get_processes":     "system.get_resources",
    "get_network":       "system.get_resources",
    # Desktop
    "take_screenshot":   "desktop.screenshot",
    "screenshot":        "desktop.screenshot",
    "open_application":  "desktop.open_app",
    "launch_app":        "desktop.open_app",
    "close_application": "desktop.close_app",
    "close_app":         "desktop.close_app",
    "list_apps":         "desktop.list_apps",
    "list_windows":      "desktop.list_windows",
    "list_running":      "desktop.list_apps",
    "focus_window":      "desktop.focus_window",
    "minimize_window":   "desktop.minimize_window",
    "maximize_window":   "desktop.maximize_window",
    # Filesystem
    "search_files":      "os.search_files",
    "list_files":        "os.list_dir",
    "list_directory":    "os.list_dir",
    "list_dir":          "os.list_dir",
    "read_file":         "os.read_file",
    "write_file":        "os.write_file",
    "create_file":       "os.write_file",
    "create_folder":     "os.create_folder",
    "make_directory":    "os.create_folder",
    "copy_file":         "os.copy_file",
    "move_file":         "os.move_file",
    "rename_file":       "os.rename_file",
    "delete_file":       "os.delete_file",
    "search_files":      "os.search_files",
    "find_files":        "os.search_files",
    # Clipboard
    "read_clipboard":    "desktop.clipboard_read",
    "paste_clipboard":   "desktop.clipboard_read",
    "copy_to_clipboard": "desktop.clipboard_write",
    # Input
    "press_key":         "desktop.press_key",
    "type_text":         "desktop.type_text",
    "hotkey":            "desktop.hotkey",
}

# ---------------------------------------------------------------------------
# AUTOMATION intent set — no LLM planning, but multi-step browser/UI plan
# ---------------------------------------------------------------------------

AUTOMATION_INTENTS: set[str] = {
    "browse_website",
    "navigate_browser",
    "open_browser",
    "search_web_browser",
    "search_google",
    "search_wikipedia",
    "search_youtube",
    "play_youtube",
    "youtube_video",
    "open_youtube",
    "scroll_page",
    "click_element",
    "fill_form",
    "new_tab",
    "close_tab",
    "browser_back",
    "download_file",
}

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

class ExecutionRouter:
    """
    Routes an intent to the appropriate execution class.

    Usage:
        router = ExecutionRouter()
        exec_class = router.route("open_application", confidence=0.95)
        tool_name  = router.fast_path_tool("open_application")
    """

    def route(
        self,
        intent: str,
        confidence: float = 1.0,
        entities: dict[str, Any] | None = None,
    ) -> ExecutionClass:
        """
        Determine the execution class for a given intent.

        Fast-path requires confidence >= 0.80.
        Automation requires confidence >= 0.70.
        Anything else goes to REASONING.
        """
        if intent in FAST_PATH_INTENT_TO_TOOL and confidence >= 0.80:
            if intent == "open_application" and entities:
                app = str(entities.get("application", "") or entities.get("name", "")).lower()
                # Bypass desktop launcher for web automation or complex media playback
                if "youtube" in app or "video" in entities or "url" in entities or "search" in entities:
                    return ExecutionClass.STRATEGIST
            return ExecutionClass.FAST_PATH

        if intent in AUTOMATION_INTENTS and confidence >= 0.70:
            return ExecutionClass.AUTOMATION

        return ExecutionClass.REASONING

    def fast_path_tool(self, intent: str) -> str | None:
        """Return the registered tool name for a fast-path intent."""
        return FAST_PATH_INTENT_TO_TOOL.get(intent)

    def is_fast_path(self, intent: str, confidence: float = 1.0) -> bool:
        return self.route(intent, confidence) == ExecutionClass.FAST_PATH

    def is_automation(self, intent: str, confidence: float = 1.0) -> bool:
        return self.route(intent, confidence) == ExecutionClass.AUTOMATION


# Module-level singleton
_router: ExecutionRouter | None = None


def get_execution_router() -> ExecutionRouter:
    global _router
    if _router is None:
        _router = ExecutionRouter()
    return _router
