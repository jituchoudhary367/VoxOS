"""
Application Control Tools (Windows).
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from voxos.observability.logger import get_logger
from voxos.tools.base import BaseTool, ToolMetadata, ToolResult, RiskLevel, VerificationType

log = get_logger(__name__)


class ApplicationArgs(BaseModel):
    name: str = Field(..., description="Application name or executable")


class ApplicationOpenTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="application.open",
            description="Open an application by name (e.g. 'chrome', 'notepad')",
            risk_level=RiskLevel.MEDIUM,
            requires_confirmation=False,
            verification_type=VerificationType.AUTOMATIC,
            timeout_seconds=30,
            category="applications",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return ApplicationArgs

    async def _execute(self, arguments: ApplicationArgs) -> ToolResult:
        app_name = arguments.name
        if not app_name:
            return ToolResult.fail("App name required", "INVALID_ARGUMENT")
            
        try:
            # We'll use start command to open apps
            proc = await asyncio.create_subprocess_shell(
                f"start {app_name}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()
            
            if proc.returncode != 0:
                return ToolResult.fail(
                    f"Failed to open '{app_name}': {stderr.decode()}",
                    "OPEN_FAILED",
                )
                
            return ToolResult.ok(data={"status": "opened", "app": app_name})
        except Exception as e:
            return ToolResult.fail(str(e), "OPEN_FAILED")


class ApplicationCloseTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="application.close",
            description="Close an application by name",
            risk_level=RiskLevel.MEDIUM,
            requires_confirmation=False,
            verification_type=VerificationType.AUTOMATIC,
            timeout_seconds=30,
            category="applications",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return ApplicationArgs

    async def _execute(self, arguments: ApplicationArgs) -> ToolResult:
        app_name = arguments.name
        if not app_name:
            return ToolResult.fail("App name required", "INVALID_ARGUMENT")
            
        try:
            # Taskkill is more robust for closing by image name
            # Adding .exe if missing
            exe_name = app_name if app_name.lower().endswith(".exe") else f"{app_name}.exe"
            proc = await asyncio.create_subprocess_shell(
                f"taskkill /IM {exe_name} /F",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            
            if proc.returncode != 0:
                if b"not found" in stderr.lower() or b"not found" in stdout.lower():
                    return ToolResult.fail(
                        f"Application '{exe_name}' is not running",
                        "NOT_FOUND",
                    )
                return ToolResult.fail(
                    f"Failed to close '{exe_name}': {stderr.decode()}",
                    "CLOSE_FAILED",
                )
                
            return ToolResult.ok(data={"status": "closed", "app": exe_name})
        except Exception as e:
            return ToolResult.fail(str(e), "CLOSE_FAILED")


def register_application_tools(registry) -> None:
    registry.register(ApplicationOpenTool())
    registry.register(ApplicationCloseTool())
    log.info("application_tools_registered")
