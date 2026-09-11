"""
Desktop Vision Tools.

Provides tools for capturing the desktop screen.
"""

from __future__ import annotations

import base64
import os
import tempfile
from datetime import datetime

from pydantic import BaseModel, Field

from voxos.tools.base import BaseTool, RiskLevel, ToolMetadata, ToolResult, VerificationType


class ScreenshotArgs(BaseModel):
    format: str = Field(
        default="png",
        description="Format to save the screenshot in (e.g., 'png', 'jpeg')."
    )
    return_base64: bool = Field(
        default=False,
        description="If True, returns the base64 encoded image string instead of a file path."
    )


class DesktopScreenshotTool(BaseTool):
    """
    Takes a screenshot of the primary monitor.
    """

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="desktop.screenshot",
            description="Take a screenshot of the entire desktop. Returns the saved file path.",
            category="desktop",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=30,
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return ScreenshotArgs

    async def _execute(self, arguments: ScreenshotArgs) -> ToolResult:
        try:
            import mss
        except ImportError:
            return ToolResult.fail("mss package is not installed. Run: pip install mss")

        fmt = arguments.format.lower()
        return_base64 = arguments.return_base64
        
        if fmt not in ("png", "jpeg", "jpg"):
            fmt = "png"

        try:
            with mss.mss() as sct:
                # monitor 1 is the primary monitor. monitor 0 is all monitors combined.
                monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                
                # Save to a temporary file
                temp_dir = tempfile.gettempdir()
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filepath = os.path.join(temp_dir, f"jarvis_screenshot_{timestamp}.{fmt}")
                
                sct_img = sct.grab(monitor)
                mss.tools.to_png(sct_img.rgb, sct_img.size, output=filepath)
                
                if return_base64:
                    with open(filepath, "rb") as image_file:
                        encoded = base64.b64encode(image_file.read()).decode("utf-8")
                    return ToolResult.ok({"base64": encoded, "format": fmt, "filepath": filepath})
                else:
                    return ToolResult.ok({"filepath": filepath})
                    
        except Exception as e:
            return ToolResult.fail(f"Failed to capture screenshot: {str(e)}", "SCREENSHOT_ERROR")
