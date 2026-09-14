"""
Desktop Input Automation Tools.

Keyboard typing, hotkeys, key presses, and mouse control
via pyautogui (cross-platform Win32 wrapper).
"""

from __future__ import annotations

import asyncio
from typing import Optional

from pydantic import BaseModel, Field

from voxos.observability.logger import get_logger
from voxos.tools.base import BaseTool, RiskLevel, ToolResult, ToolMetadata, VerificationType

log = get_logger(__name__)

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
    _PYAUTOGUI_AVAILABLE = True
except ImportError:
    _PYAUTOGUI_AVAILABLE = False


def _check_pyautogui() -> ToolResult | None:
    if not _PYAUTOGUI_AVAILABLE:
        return ToolResult.fail("pyautogui is not installed. Run: pip install pyautogui", "MISSING_DEPENDENCY")
    return None


# ---------------------------------------------------------------------------
# Keyboard Type Tool
# ---------------------------------------------------------------------------

class KeyboardTypeArgs(BaseModel):
    text: str = Field(..., description="Text to type into the active window.")
    interval: float = Field(0.03, description="Delay between keystrokes in seconds.")


class KeyboardTypeTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="desktop.type_text",
            description="Type text using the keyboard into the currently focused window.",
            risk_level=RiskLevel.MEDIUM,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=30,
            category="desktop",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return KeyboardTypeArgs

    async def _execute(self, args: KeyboardTypeArgs) -> ToolResult:
        err = _check_pyautogui()
        if err:
            return err
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: pyautogui.write(args.text, interval=args.interval))
            return ToolResult.ok(data={"typed": args.text})
        except Exception as e:
            return ToolResult.fail(str(e), "INPUT_ERROR")


# ---------------------------------------------------------------------------
# Hotkey Tool
# ---------------------------------------------------------------------------

class HotkeyArgs(BaseModel):
    keys: list[str] = Field(..., description="Keys to press simultaneously. E.g. ['ctrl', 'c'] for Ctrl+C.")


class HotkeyTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="desktop.hotkey",
            description="Press a keyboard shortcut (e.g. Ctrl+C, Alt+Tab, Win+D).",
            risk_level=RiskLevel.MEDIUM,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=10,
            category="desktop",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return HotkeyArgs

    async def _execute(self, args: HotkeyArgs) -> ToolResult:
        err = _check_pyautogui()
        if err:
            return err
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: pyautogui.hotkey(*args.keys))
            return ToolResult.ok(data={"hotkey": "+".join(args.keys)})
        except Exception as e:
            return ToolResult.fail(str(e), "INPUT_ERROR")


# ---------------------------------------------------------------------------
# Key Press Tool
# ---------------------------------------------------------------------------

class KeyPressArgs(BaseModel):
    key: str = Field(..., description="Key to press (e.g. 'enter', 'esc', 'tab', 'delete', 'f5').")
    presses: int = Field(1, description="Number of times to press the key.")


class KeyPressTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="desktop.press_key",
            description="Press a single keyboard key one or more times.",
            risk_level=RiskLevel.MEDIUM,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=10,
            category="desktop",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return KeyPressArgs

    async def _execute(self, args: KeyPressArgs) -> ToolResult:
        err = _check_pyautogui()
        if err:
            return err
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: pyautogui.press(args.key, presses=args.presses))
            return ToolResult.ok(data={"key": args.key, "presses": args.presses})
        except Exception as e:
            return ToolResult.fail(str(e), "INPUT_ERROR")


# ---------------------------------------------------------------------------
# Mouse Click Tool
# ---------------------------------------------------------------------------

class MouseClickArgs(BaseModel):
    x: Optional[int] = Field(None, description="X coordinate (screen position). If None, clicks at current position.")
    y: Optional[int] = Field(None, description="Y coordinate (screen position). If None, clicks at current position.")
    button: str = Field("left", description="Mouse button: 'left', 'right', 'middle'.")
    clicks: int = Field(1, description="Number of clicks (1=single, 2=double).")


class MouseClickTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="desktop.mouse_click",
            description="Click the mouse at a screen position. Use UI automation tools instead when possible.",
            risk_level=RiskLevel.MEDIUM,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=10,
            category="desktop",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return MouseClickArgs

    async def _execute(self, args: MouseClickArgs) -> ToolResult:
        err = _check_pyautogui()
        if err:
            return err
        try:
            loop = asyncio.get_running_loop()
            if args.x is not None and args.y is not None:
                await loop.run_in_executor(
                    None,
                    lambda: pyautogui.click(args.x, args.y, button=args.button, clicks=args.clicks)
                )
            else:
                await loop.run_in_executor(
                    None,
                    lambda: pyautogui.click(button=args.button, clicks=args.clicks)
                )
            return ToolResult.ok(data={"clicked": {"x": args.x, "y": args.y, "button": args.button}})
        except Exception as e:
            return ToolResult.fail(str(e), "INPUT_ERROR")


# ---------------------------------------------------------------------------
# Mouse Scroll Tool
# ---------------------------------------------------------------------------

class MouseScrollArgs(BaseModel):
    clicks: int = Field(3, description="Number of scroll clicks. Positive = up, negative = down.")


class MouseScrollTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="desktop.mouse_scroll",
            description="Scroll the mouse wheel up (positive) or down (negative).",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=10,
            category="desktop",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return MouseScrollArgs

    async def _execute(self, args: MouseScrollArgs) -> ToolResult:
        err = _check_pyautogui()
        if err:
            return err
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: pyautogui.scroll(args.clicks))
            return ToolResult.ok(data={"scrolled": args.clicks})
        except Exception as e:
            return ToolResult.fail(str(e), "INPUT_ERROR")
