"""
Application Control Tools (Windows).
"""

from __future__ import annotations

import asyncio
from typing import Any

from voxos.observability.logger import get_logger
from voxos.tools.base import BaseTool, ToolMetadata, ToolResult, RiskLevel, VerificationType

log = get_logger(__name__)


class ApplicationOpenTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="application.open",
            description="Open an application by name (e.g. 'chrome', 'notepad')",
            risk_level=RiskLevel.MEDIUM,
            requires_confirmation=False,
            parameters={
                "name": {"type": "string", "description": "Application name or executable"},
            },
            required_parameters=["name"],
            verification_type=VerificationType.AUTOMATIC,
        )

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        app_name = arguments.get("name", "")
        if not app_name:
            return ToolResult(success=False, error="App name required", tool_name=self.metadata.name)
            
        try:
            # We'll use start command to open apps
            proc = await asyncio.create_subprocess_shell(
                f"start {app_name}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()
            
            if proc.returncode != 0:
                return ToolResult(
                    success=False,
                    error=f"Failed to open '{app_name}': {stderr.decode()}",
                    tool_name=self.metadata.name
                )
                
            return ToolResult(
                success=True, 
                data={"status": "opened", "app": app_name},
                tool_name=self.metadata.name
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e), tool_name=self.metadata.name)


class ApplicationCloseTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="application.close",
            description="Close an application by name",
            risk_level=RiskLevel.MEDIUM,
            requires_confirmation=False,
            parameters={
                "name": {"type": "string", "description": "Application name"},
            },
            required_parameters=["name"],
            verification_type=VerificationType.AUTOMATIC,
        )

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        app_name = arguments.get("name", "")
        if not app_name:
            return ToolResult(success=False, error="App name required", tool_name=self.metadata.name)
            
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
                    return ToolResult(
                        success=False,
                        error=f"Application '{exe_name}' is not running",
                        tool_name=self.metadata.name
                    )
                return ToolResult(
                    success=False,
                    error=f"Failed to close '{exe_name}': {stderr.decode()}",
                    tool_name=self.metadata.name
                )
                
            return ToolResult(
                success=True, 
                data={"status": "closed", "app": exe_name},
                tool_name=self.metadata.name
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e), tool_name=self.metadata.name)


def register_application_tools(registry) -> None:
    registry.register(ApplicationOpenTool())
    registry.register(ApplicationCloseTool())
    log.info("application_tools_registered")
