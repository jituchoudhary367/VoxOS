"""
File System Tools.
Allows VoxOS to interact with the host file system securely.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from voxos.config import get_settings
from voxos.observability.logger import get_logger
from voxos.security.sandbox import SandboxViolationError, resolve_and_verify_path
from voxos.tools.base import BaseTool, RiskLevel, ToolResult, ToolMetadata, VerificationType

log = get_logger(__name__)


# --- Helper to resolve paths against the workspace ---

def _resolve_safe_path(requested_path: str) -> Path:
    """Resolve a path safely against the configured workspace."""
    settings = get_settings()
    
    # In strict mode, only allow the configured workspace root
    # For a real desktop agent, we might want to also allow the actual Desktop, Documents, etc.
    # We will include os.getcwd() (the project root) and the workspace root.
    allowed_roots = [settings.workspace.workspace_path]
    if not settings.workspace.filesystem_strict_mode:
        allowed_roots.append(Path(os.getcwd()).resolve())
        # Add Desktop as a safe root for this project
        allowed_roots.append(Path.home() / "Desktop")
    
    # Ensure roots exist
    for root in allowed_roots:
        root.mkdir(parents=True, exist_ok=True)
        
    return resolve_and_verify_path(requested_path, allowed_roots)


# --- Read File Tool ---

class ReadFileArgs(BaseModel):
    file_path: str = Field(..., description="Path to the file to read")


class ReadFileTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="os.read_file",
            description="Read the contents of a file.",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=30,
            category="filesystem",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return ReadFileArgs

    async def _execute(self, args: ReadFileArgs) -> ToolResult:
        try:
            safe_path = _resolve_safe_path(args.file_path)
            
            if not safe_path.is_file():
                return ToolResult(
                    success=False,
                    error=f"File not found or is not a file: {safe_path}",
                    error_code="FILE_NOT_FOUND",
                    status="NOT_FOUND",
                )
                
            content = safe_path.read_text(encoding="utf-8")
            log.info("fs_read_file", path=str(safe_path), bytes=len(content))
            return ToolResult.ok(data={"content": content})
            
        except SandboxViolationError as e:
            return ToolResult.fail(error=str(e), error_code="SANDBOX_VIOLATION", status="PERMISSION_DENIED")
        except UnicodeDecodeError:
            return ToolResult.fail(
                error="File is not a valid UTF-8 text file.",
                error_code="ENCODING_ERROR",
            )
        except Exception as e:
            log.exception("fs_read_error", path=args.file_path, error=str(e))
            return ToolResult.fail(error=str(e), error_code="FS_ERROR")


# --- Write File Tool ---

class WriteFileArgs(BaseModel):
    file_path: str = Field(..., description="Path to the file to write")
    content: str = Field(..., description="Content to write to the file")
    append: bool = Field(False, description="If True, append to the file instead of overwriting")


class WriteFileTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="os.write_file",
            description="Write or append text content to a file.",
            risk_level=RiskLevel.MEDIUM,
            requires_confirmation=False,
            verification_type=VerificationType.AUTOMATIC,
            timeout_seconds=30,
            category="filesystem",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return WriteFileArgs

    async def _execute(self, args: WriteFileArgs) -> ToolResult:
        try:
            safe_path = _resolve_safe_path(args.file_path)
            
            # Ensure parent directories exist
            safe_path.parent.mkdir(parents=True, exist_ok=True)
            
            mode = "a" if args.append else "w"
            with open(safe_path, mode, encoding="utf-8") as f:
                f.write(args.content)
                
            log.info("fs_write_file", path=str(safe_path), append=args.append)
            return ToolResult.ok(
                data={
                    "path": str(safe_path),
                    "bytes_written": len(args.content.encode("utf-8")),
                    "mode": mode,
                }
            )
            
        except SandboxViolationError as e:
            return ToolResult.fail(error=str(e), error_code="SANDBOX_VIOLATION", status="PERMISSION_DENIED")
        except Exception as e:
            log.exception("fs_write_error", path=args.file_path, error=str(e))
            return ToolResult.fail(error=str(e), error_code="FS_ERROR")


# --- List Directory Tool ---

class ListDirArgs(BaseModel):
    dir_path: str = Field(..., description="Path to the directory to list")


class ListDirTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="os.list_dir",
            description="List contents of a directory.",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=30,
            category="filesystem",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return ListDirArgs

    async def _execute(self, args: ListDirArgs) -> ToolResult:
        try:
            safe_path = _resolve_safe_path(args.dir_path)
            
            if not safe_path.is_dir():
                return ToolResult.fail(
                    error=f"Directory not found or is not a directory: {safe_path}",
                    error_code="DIR_NOT_FOUND",
                    status="NOT_FOUND",
                )
                
            items = []
            for item in safe_path.iterdir():
                items.append({
                    "name": item.name,
                    "is_dir": item.is_dir(),
                    "size": item.stat().st_size if item.is_file() else 0,
                })
                
            log.info("fs_list_dir", path=str(safe_path), count=len(items))
            return ToolResult.ok(data={"items": items})
            
        except SandboxViolationError as e:
            return ToolResult.fail(error=str(e), error_code="SANDBOX_VIOLATION", status="PERMISSION_DENIED")
        except Exception as e:
            log.exception("fs_list_error", path=args.dir_path, error=str(e))
            return ToolResult.fail(error=str(e), error_code="FS_ERROR")
