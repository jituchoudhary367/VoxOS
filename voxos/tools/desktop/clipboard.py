"""
Clipboard Tools.

Read and write the Windows clipboard.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from voxos.observability.logger import get_logger
from voxos.tools.base import BaseTool, RiskLevel, ToolResult, ToolMetadata, VerificationType

log = get_logger(__name__)


class ClipboardReadArgs(BaseModel):
    pass


class ClipboardReadTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="desktop.clipboard_read",
            description="Read the current contents of the clipboard.",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=5,
            category="desktop",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return ClipboardReadArgs

    async def _execute(self, args: ClipboardReadArgs) -> ToolResult:
        try:
            import pyperclip
            text = pyperclip.paste()
            return ToolResult.ok(data={"content": text, "length": len(text)})
        except ImportError:
            # Fallback: Windows-specific
            try:
                import subprocess
                result = subprocess.run(
                    ["powershell", "-Command", "Get-Clipboard"],
                    capture_output=True, text=True, timeout=5
                )
                text = result.stdout.strip()
                return ToolResult.ok(data={"content": text, "length": len(text)})
            except Exception as e:
                return ToolResult.fail(str(e), "CLIPBOARD_ERROR")
        except Exception as e:
            return ToolResult.fail(str(e), "CLIPBOARD_ERROR")


class ClipboardWriteArgs(BaseModel):
    content: str = Field(..., description="Text to copy to the clipboard.")


class ClipboardWriteTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="desktop.clipboard_write",
            description="Write text to the clipboard.",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=5,
            category="desktop",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return ClipboardWriteArgs

    async def _execute(self, args: ClipboardWriteArgs) -> ToolResult:
        try:
            import pyperclip
            pyperclip.copy(args.content)
            return ToolResult.ok(data={"copied": True, "length": len(args.content)})
        except ImportError:
            try:
                import subprocess
                # Pipe to clip.exe
                subprocess.run(
                    ["clip"],
                    input=args.content.encode("utf-8"),
                    timeout=5
                )
                return ToolResult.ok(data={"copied": True, "length": len(args.content)})
            except Exception as e:
                return ToolResult.fail(str(e), "CLIPBOARD_ERROR")
        except Exception as e:
            return ToolResult.fail(str(e), "CLIPBOARD_ERROR")
