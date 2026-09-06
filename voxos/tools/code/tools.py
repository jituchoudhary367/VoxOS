"""
Code execution and linting tools.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Any

from voxos.observability.logger import get_logger
from voxos.tools.base import BaseTool, ToolMetadata, ToolResult, RiskLevel, VerificationType

log = get_logger(__name__)


class RunPythonTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="code.run_python",
            description="Execute a Python snippet and get the output",
            risk_level=RiskLevel.HIGH,
            requires_confirmation=False,
            parameters={
                "code": {"type": "string", "description": "Python code to execute"},
            },
            required_parameters=["code"],
            verification_type=VerificationType.AUTOMATIC,
        )

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        code = arguments.get("code", "")
        if not code:
            return ToolResult(success=False, error="Code is required", tool_name=self.metadata.name)
            
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
                f.write(code)
                tmp_path = f.name
                
            try:
                proc = await asyncio.create_subprocess_exec(
                    "python", tmp_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await proc.communicate()
                
                return ToolResult(
                    success=proc.returncode == 0,
                    data={
                        "exit_code": proc.returncode,
                        "stdout": stdout.decode("utf-8", errors="replace"),
                        "stderr": stderr.decode("utf-8", errors="replace"),
                    },
                    tool_name=self.metadata.name
                )
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
        except Exception as e:
            return ToolResult(success=False, error=str(e), tool_name=self.metadata.name)


def register_code_tools(registry) -> None:
    registry.register(RunPythonTool())
    log.info("code_tools_registered")
