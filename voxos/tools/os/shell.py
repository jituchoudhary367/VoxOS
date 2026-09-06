"""
Shell Execution Tool.
Allows VoxOS to execute arbitrary shell commands on the host OS.
"""

import asyncio
import os
from typing import Any, Tuple

from pydantic import BaseModel, Field

from voxos.tools.base import BaseTool, RiskLevel, ToolResult, ToolMetadata, VerificationType
from voxos.observability.logger import get_logger

log = get_logger(__name__)


class ShellCommandArgs(BaseModel):
    command: str = Field(..., description="The shell command to execute")
    timeout: int = Field(30, description="Maximum execution time in seconds")
    cwd: str | None = Field(None, description="Current working directory for the command")


class ShellCommandTool(BaseTool):
    """Executes a shell command on the host operating system."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="os.shell_command",
            description="Execute an arbitrary shell command (PowerShell/cmd on Windows, bash on Unix). Returns stdout and stderr.",
            risk_level=RiskLevel.HIGH,
            requires_confirmation=True,
            verification_type=VerificationType.AUTOMATIC,
            timeout_seconds=60,
            category="os",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return ShellCommandArgs

    async def _execute(self, args: ShellCommandArgs) -> ToolResult:
        """Executes the shell command and captures output."""
        log.info("executing_shell_command", command=args.command, cwd=args.cwd)

        # Validate CWD if provided
        if args.cwd and not os.path.isdir(args.cwd):
            return ToolResult.fail(
                error=f"Working directory does not exist: {args.cwd}",
                error_code="INVALID_CWD",
            )

        try:
            # Create subprocess
            process = await asyncio.create_subprocess_shell(
                args.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=args.cwd,
            )

            # Wait for completion with timeout
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=args.timeout
            )

            exit_code = process.returncode
            stdout_str = stdout.decode("utf-8", errors="replace").strip()
            stderr_str = stderr.decode("utf-8", errors="replace").strip()

            log.info(
                "shell_command_completed",
                command=args.command,
                exit_code=exit_code,
                stdout_len=len(stdout_str),
                stderr_len=len(stderr_str),
            )

            if exit_code == 0:
                return ToolResult.ok(
                    data={
                        "exit_code": exit_code,
                        "stdout": stdout_str,
                        "stderr": stderr_str,
                    },
                )
            else:
                return ToolResult.fail(
                    error=f"Command failed with exit code {exit_code}\nStderr: {stderr_str}\nStdout: {stdout_str}",
                    error_code="NON_ZERO_EXIT",
                    data={
                        "exit_code": exit_code,
                        "stdout": stdout_str,
                        "stderr": stderr_str,
                    },
                )

        except asyncio.TimeoutError:
            log.warning("shell_command_timeout", command=args.command, timeout=args.timeout)
            # Try to kill the process if it timed out
            try:
                process.kill()
            except Exception:
                pass
            return ToolResult.fail(
                error=f"Command timed out after {args.timeout} seconds.",
                error_code="TIMEOUT",
            )
        except Exception as e:
            log.exception("shell_command_error", command=args.command, error=str(e))
            return ToolResult.fail(
                error=f"Failed to execute command: {str(e)}",
                error_code="EXECUTION_ERROR",
            )
