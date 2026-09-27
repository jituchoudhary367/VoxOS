"""
Execution Router — classifies user intents into three execution tiers.

Class A — FAST_PATH:  Deterministic, no LLM needed.
Class B — AUTOMATION: Browser/desktop automation, no LLM planning needed.
Class C — REASONING:  Complex, multi-step, ambiguous — use Strategist.
"""

from __future__ import annotations

import re
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
    # Media: pause / play / stop / next / mute whatever is playing
    "media_control":     "browser.media",
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

AUTOMATION_INTENT_TO_TOOL: dict[str, str] = {
    "open_browser": "browser.open",
    "open_youtube": "browser.open",
    "search_web_browser": "browser.search",
    "search_google": "browser.search",
    "search_wikipedia": "browser.search",
    "search_youtube": "browser.search",
    "scroll_page": "browser.scroll",
    "new_tab": "browser.new_tab",
    "browser_back": "browser.back",
}

_COMPOUND_RE = re.compile(r"\b(?:and|then|also|after that|afterwards|followed by)\b|[,;]")


def is_compound_request(text: str) -> bool:
    """True when a request strings several actions together."""
    return bool(_COMPOUND_RE.search(text.lower()))


# Words that point at earlier turns or at what is on screen.
# "it" / "now" alone are too common ("what time is it"), so they only count
# after an action verb ("close it") or in "try now".
_FOLLOW_UP_RE = re.compile(
    r"\b(?:that|this|those|them|these|more|another|anther|other|else|again|same|instead|"
    r"previous|earlier|back)\b"
    r"|\b(?:close|open|play|stop|pause|delete|save|search|send|share|do|redo|undo|read|show|use)\s+it\b"
    r"|\btry\s+(?:now|again|it)\b|^(?:retry|redo|undo)\b"
    # Questions about what is on screen depend on the screen, not the words
    # ("what is shown on the amazon page" was guessed as a CPU question).
    r"|\b(?:screen|page|tab|window|showing|shown|visible|playing)\b"
)


_LOGIN_RE = re.compile(r"\b(?:log\s*-?\s*in|login|sign\s*-?\s*in|signin|sign me in)\b", re.IGNORECASE)


def asks_to_log_in(text: str) -> bool:
    """Logins need the agents (saved credentials, several steps); "log in to my
    amazon account" was guessed as opening an app called Amazon."""
    return bool(_LOGIN_RE.search(text))


_SCREEN_RE = re.compile(r"\b(?:screen|page|tab|window|showing|shown|visible|displayed|playing)\b")


def asks_about_screen(text: str) -> bool:
    """True for questions only answerable by looking ("what is shown on the page").

    The listener tends to call these chat, and chat answers from memory.
    """
    return bool(_SCREEN_RE.search(text.lower()))


def is_follow_up(text: str) -> bool:
    """True when a request only makes sense with the conversation or screen.

    "search more laptops from another company", "play that", "try now":
    none of these can be run from the words alone.
    """
    return bool(_FOLLOW_UP_RE.search(text.lower()))


def _arguments_valid(tool_name: str, entities: dict[str, Any]) -> bool:
    from voxos.tools.registry import get_tool_registry

    tool = get_tool_registry().get(tool_name)
    if tool is None:
        return False
    _, error = tool.validate_args(entities)
    return error is None


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
        *,
        source: str = "rules",
        request_text: str = "",
    ) -> ExecutionClass:
        """
        Determine the execution class for a given intent.

        Fast-path requires confidence >= 0.80.
        Automation requires confidence >= 0.70.
        Anything else goes to REASONING.

        Rule-based classifications are trusted. An intent name guessed by the
        LLM only gets a deterministic route when its entities are valid
        arguments for the mapped tool and the request is a single action —
        otherwise the entities are free-form and a compound request would be
        collapsed into one step, so the Strategist has to plan it.
        """
        if intent in FAST_PATH_INTENT_TO_TOOL and confidence >= 0.80:
            exec_class = ExecutionClass.FAST_PATH
        elif intent in AUTOMATION_INTENTS and confidence >= 0.70:
            exec_class = ExecutionClass.AUTOMATION
        else:
            return ExecutionClass.REASONING

        if source != "rules":
            tool_name = self.fast_path_tool(intent) or self.automation_tool(intent)
            if (
                not tool_name
                or is_compound_request(request_text)
                or is_follow_up(request_text)
                or asks_to_log_in(request_text)
                or not _arguments_valid(tool_name, entities or {})
            ):
                log.info("llm_intent_demoted_to_reasoning", intent=intent, tool=tool_name)
                return ExecutionClass.REASONING

        return exec_class

    def fast_path_tool(self, intent: str) -> str | None:
        """Return the registered tool name for a fast-path intent."""
        return FAST_PATH_INTENT_TO_TOOL.get(intent)

    def automation_tool(self, intent: str) -> str | None:
        """Return the registered tool for a deterministic browser action."""
        return AUTOMATION_INTENT_TO_TOOL.get(intent)

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
