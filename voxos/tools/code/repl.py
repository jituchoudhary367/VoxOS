"""
Python REPL Tool.

Allows VoxOS to execute arbitrary Python code locally.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

from pydantic import BaseModel, Field

from voxos.tools.base import BaseTool, RiskLevel, ToolMetadata, ToolResult, VerificationType


class PythonREPLArgs(BaseModel):
    code: str = Field(
        description="The Python code to execute. Can be a single line or a multi-line script."
    )
    timeout: int = Field(
        default=10,
        description="Maximum execution time in seconds before terminating the script."
    )


class PythonREPLTool(BaseTool):
    """
    Executes Python code and captures the output.
    """

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="code.python_repl",
            description="Execute Python code locally and return stdout/stderr. Used for math, data analysis, or scripting.",
            category="code",
            risk_level=RiskLevel.HIGH,  # High risk due to arbitrary code execution
            requires_confirmation=True,
            verification_type=VerificationType.NONE,
            timeout_seconds=60,
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return PythonREPLArgs

    async def _execute(self, arguments: PythonREPLArgs) -> ToolResult:
        code = arguments.code
        timeout = arguments.timeout

        # Write code to a temporary file
        fd, filepath = tempfile.mkstemp(suffix=".py", prefix="vox_repl_")
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(code)
            
            # Execute the file
            process = await asyncio.create_subprocess_exec(
                sys.executable, filepath,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=os.getcwd()
            )

            try:
                stdout_data, stderr_data = await asyncio.wait_for(process.communicate(), timeout=timeout)
                stdout = stdout_data.decode("utf-8", errors="replace")
                stderr = stderr_data.decode("utf-8", errors="replace")
                
                if process.returncode == 0:
                    return ToolResult.ok({
                        "stdout": stdout,
                        "stderr": stderr,
                        "return_code": 0
                    })
                else:
                    return ToolResult.fail(
                        f"Execution failed with return code {process.returncode}:\nStdout:\n{stdout}\nStderr:\n{stderr}",
                        error_code="EXECUTION_FAILED"
                    )

            except asyncio.TimeoutError:
                process.kill()
                await process.communicate()
                return ToolResult.fail(f"Execution timed out after {timeout} seconds.", "TIMEOUT")

        except Exception as e:
            return ToolResult.fail(f"Failed to execute Python code: {str(e)}", "REPL_ERROR")
        finally:
            try:
                os.remove(filepath)
            except OSError:
                pass
