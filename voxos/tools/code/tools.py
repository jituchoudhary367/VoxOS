"""
Code execution and linting tools.
"""

from __future__ import annotations

import asyncio
import os
import tempfile

from pydantic import BaseModel, Field

from voxos.observability.logger import get_logger
from voxos.tools.base import BaseTool, ToolMetadata, ToolResult, RiskLevel, VerificationType

log = get_logger(__name__)


class RunPythonArgs(BaseModel):
    code: str = Field(..., description="Python code to execute")


class RunPythonTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="code.run_python",
            description="Execute a Python snippet and get the output",
            risk_level=RiskLevel.HIGH,
            requires_confirmation=False,
            verification_type=VerificationType.AUTOMATIC,
            timeout_seconds=60,
            category="code",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return RunPythonArgs

    async def _execute(self, arguments: RunPythonArgs) -> ToolResult:
        code = arguments.code
        if not code:
            return ToolResult.fail("Code is required", "INVALID_ARGUMENT")
            
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
                
                data = {
                    "exit_code": proc.returncode,
                    "stdout": stdout.decode("utf-8", errors="replace"),
                    "stderr": stderr.decode("utf-8", errors="replace"),
                }
                return ToolResult.ok(data) if proc.returncode == 0 else ToolResult.fail(
                    "Python process exited with a non-zero status",
                    "NON_ZERO_EXIT",
                    data=data,
                )
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
        except Exception as e:
            return ToolResult.fail(str(e), "EXECUTION_ERROR")


def register_code_tools(registry) -> None:
    registry.register(RunPythonTool())
    log.info("code_tools_registered")
