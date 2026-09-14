"""
File System Tools.
Allows VoxOS to interact with the host file system securely.

Supported tools:
    os.read_file     — Read file contents
    os.write_file    — Write/append to a file
    os.list_dir      — List directory contents
    os.create_folder — Create a directory
    os.copy_file     — Copy a file
    os.move_file     — Move or rename a file
    os.rename_file   — Rename a file in-place
    os.delete_file   — Delete a file (HIGH risk, requires approval)
    os.search_files  — Search for files by name, extension, or date
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path

from pydantic import BaseModel, Field

from voxos.config import get_settings
from voxos.observability.logger import get_logger
from voxos.security.sandbox import SandboxViolationError, resolve_and_verify_path
from voxos.tools.base import BaseTool, RiskLevel, ToolResult, ToolMetadata, VerificationResult, VerificationType

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Windows special folder resolution
# ---------------------------------------------------------------------------

SPECIAL_FOLDERS: dict[str, Path] = {
    "desktop": Path.home() / "Desktop",
    "onedrive desktop": Path(os.environ.get("USERPROFILE", str(Path.home()))) / "OneDrive" / "Desktop",
    "documents": Path.home() / "Documents",
    "downloads": Path.home() / "Downloads",
    "pictures": Path.home() / "Pictures",
    "music": Path.home() / "Music",
    "videos": Path.home() / "Videos",
    "home": Path.home(),
    "~": Path.home(),
}

# Also try OneDrive paths (common on Windows)
_onedrive = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "OneDrive"
if _onedrive.exists():
    SPECIAL_FOLDERS["onedrive"] = _onedrive
    SPECIAL_FOLDERS["onedrive documents"] = _onedrive / "Documents"
    SPECIAL_FOLDERS["onedrive desktop"] = _onedrive / "Desktop"


def _resolve_safe_path(requested_path: str) -> Path:
    """Resolve a path safely, supporting special folder names."""
    settings = get_settings()

    # Check for special folder names
    lower = requested_path.lower().strip()
    for special_name, special_path in SPECIAL_FOLDERS.items():
        if lower == special_name:
            special_path.mkdir(parents=True, exist_ok=True)
            return special_path

    # Check if path starts with a special folder name
    for special_name, special_path in SPECIAL_FOLDERS.items():
        prefix = special_name + "/"
        prefix2 = special_name + "\\"
        if lower.startswith(prefix) or lower.startswith(prefix2):
            rel = requested_path[len(special_name) + 1:]
            resolved = (special_path / rel).resolve()
            # Still verify it's under the special folder
            try:
                resolved.relative_to(special_path)
            except ValueError:
                pass  # Will be caught by sandbox
            else:
                return resolved

    # Standard path resolution
    allowed_roots = [settings.workspace.workspace_path]
    if not settings.workspace.filesystem_strict_mode:
        allowed_roots.extend([
            Path(os.getcwd()).resolve(),
            Path.home() / "Desktop",
            Path.home() / "Documents",
            Path.home() / "Downloads",
            Path.home(),
            # OneDrive paths
            Path(os.environ.get("USERPROFILE", str(Path.home()))) / "OneDrive",
        ])

    for root in allowed_roots:
        root.mkdir(parents=True, exist_ok=True)

    return resolve_and_verify_path(requested_path, allowed_roots)


# ---------------------------------------------------------------------------
# Read File Tool
# ---------------------------------------------------------------------------

class ReadFileArgs(BaseModel):
    file_path: str = Field(..., description="Path to the file to read. Supports 'Desktop/file.txt' etc.")


class ReadFileTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="os.read_file",
            description="Read the text contents of a file.",
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
                return ToolResult.fail(
                    error=f"File not found: {args.file_path}",
                    error_code="FILE_NOT_FOUND",
                    status="NOT_FOUND",
                )

            content = safe_path.read_text(encoding="utf-8", errors="replace")
            log.info("fs_read_file", path=str(safe_path), bytes=len(content))
            return ToolResult.ok(data={"content": content, "path": str(safe_path)})

        except SandboxViolationError as e:
            return ToolResult.fail(error=str(e), error_code="SANDBOX_VIOLATION", status="PERMISSION_DENIED")
        except Exception as e:
            log.exception("fs_read_error", path=args.file_path, error=str(e))
            return ToolResult.fail(error=str(e), error_code="FS_ERROR")


# ---------------------------------------------------------------------------
# Write File Tool
# ---------------------------------------------------------------------------

class WriteFileArgs(BaseModel):
    file_path: str = Field(..., description="Path to the file to write. Parent directories created automatically.")
    content: str = Field(..., description="Content to write to the file.")
    append: bool = Field(False, description="If True, append instead of overwrite.")


class WriteFileTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="os.write_file",
            description="Write or append text content to a file. Creates parent directories if needed.",
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
            safe_path.parent.mkdir(parents=True, exist_ok=True)

            mode = "a" if args.append else "w"
            with open(safe_path, mode, encoding="utf-8") as f:
                f.write(args.content)

            log.info("fs_write_file", path=str(safe_path), append=args.append)
            return ToolResult.ok(data={
                "path": str(safe_path),
                "bytes_written": len(args.content.encode("utf-8")),
                "mode": mode,
            })

        except SandboxViolationError as e:
            return ToolResult.fail(error=str(e), error_code="SANDBOX_VIOLATION", status="PERMISSION_DENIED")
        except Exception as e:
            log.exception("fs_write_error", path=args.file_path, error=str(e))
            return ToolResult.fail(error=str(e), error_code="FS_ERROR")

    async def verify(self, args: WriteFileArgs, result: ToolResult) -> VerificationResult:
        if not result.success:
            return VerificationResult.fail("Write failed")
        try:
            safe_path = _resolve_safe_path(args.file_path)
            if not safe_path.exists():
                return VerificationResult.fail(f"File not found after write: {safe_path}")
            return VerificationResult.ok(checks=[{"check": "file_exists", "passed": True}])
        except Exception as e:
            return VerificationResult.fail(str(e))


# ---------------------------------------------------------------------------
# List Directory Tool
# ---------------------------------------------------------------------------

class ListDirArgs(BaseModel):
    dir_path: str = Field(..., description="Directory path to list. Use 'Desktop', 'Documents', etc.")


class ListDirTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="os.list_dir",
            description="List files and folders inside a directory. Supports 'Desktop', 'Documents', etc.",
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
                    error=f"Directory not found: {args.dir_path}",
                    error_code="DIR_NOT_FOUND",
                    status="NOT_FOUND",
                )

            items = []
            for item in sorted(safe_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                stat = item.stat()
                items.append({
                    "name": item.name,
                    "is_dir": item.is_dir(),
                    "size_bytes": stat.st_size if item.is_file() else 0,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                })

            log.info("fs_list_dir", path=str(safe_path), count=len(items))
            return ToolResult.ok(data={"path": str(safe_path), "items": items, "count": len(items)})

        except SandboxViolationError as e:
            return ToolResult.fail(error=str(e), error_code="SANDBOX_VIOLATION", status="PERMISSION_DENIED")
        except Exception as e:
            log.exception("fs_list_error", path=args.dir_path, error=str(e))
            return ToolResult.fail(error=str(e), error_code="FS_ERROR")


# ---------------------------------------------------------------------------
# Create Folder Tool
# ---------------------------------------------------------------------------

class CreateFolderArgs(BaseModel):
    dir_path: str = Field(..., description="Path of the folder to create. e.g. 'Desktop/VoxTest'")


class CreateFolderTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="os.create_folder",
            description="Create a new folder. Parent directories are created automatically.",
            risk_level=RiskLevel.MEDIUM,
            requires_confirmation=False,
            verification_type=VerificationType.AUTOMATIC,
            timeout_seconds=15,
            category="filesystem",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return CreateFolderArgs

    async def _execute(self, args: CreateFolderArgs) -> ToolResult:
        try:
            safe_path = _resolve_safe_path(args.dir_path)
            safe_path.mkdir(parents=True, exist_ok=True)
            log.info("fs_create_folder", path=str(safe_path))
            return ToolResult.ok(data={"path": str(safe_path), "created": True})
        except SandboxViolationError as e:
            return ToolResult.fail(error=str(e), error_code="SANDBOX_VIOLATION", status="PERMISSION_DENIED")
        except Exception as e:
            log.exception("fs_create_folder_error", path=args.dir_path)
            return ToolResult.fail(error=str(e), error_code="FS_ERROR")

    async def verify(self, args: CreateFolderArgs, result: ToolResult) -> VerificationResult:
        if not result.success:
            return VerificationResult.fail("Create failed")
        try:
            safe_path = _resolve_safe_path(args.dir_path)
            if safe_path.is_dir():
                return VerificationResult.ok(checks=[{"check": "folder_exists", "passed": True}])
            return VerificationResult.fail(f"Folder not found after create: {safe_path}")
        except Exception as e:
            return VerificationResult.fail(str(e))


# ---------------------------------------------------------------------------
# Copy File Tool
# ---------------------------------------------------------------------------

class CopyFileArgs(BaseModel):
    source_path: str = Field(..., description="Source file path")
    dest_path: str = Field(..., description="Destination path (file or directory)")


class CopyFileTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="os.copy_file",
            description="Copy a file from source to destination.",
            risk_level=RiskLevel.MEDIUM,
            requires_confirmation=False,
            verification_type=VerificationType.AUTOMATIC,
            timeout_seconds=60,
            category="filesystem",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return CopyFileArgs

    async def _execute(self, args: CopyFileArgs) -> ToolResult:
        try:
            src = _resolve_safe_path(args.source_path)
            dst = _resolve_safe_path(args.dest_path)

            if not src.exists():
                return ToolResult.fail(f"Source not found: {args.source_path}", "FILE_NOT_FOUND", status="NOT_FOUND")

            if dst.is_dir():
                dst = dst / src.name

            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dst))
            log.info("fs_copy_file", src=str(src), dst=str(dst))
            return ToolResult.ok(data={"source": str(src), "destination": str(dst)})

        except SandboxViolationError as e:
            return ToolResult.fail(error=str(e), error_code="SANDBOX_VIOLATION", status="PERMISSION_DENIED")
        except Exception as e:
            log.exception("fs_copy_error", src=args.source_path, dst=args.dest_path)
            return ToolResult.fail(error=str(e), error_code="FS_ERROR")

    async def verify(self, args: CopyFileArgs, result: ToolResult) -> VerificationResult:
        if not result.success:
            return VerificationResult.fail("Copy failed")
        try:
            dst_str = result.data.get("destination") if result.data else None
            if dst_str and Path(dst_str).exists():
                return VerificationResult.ok(checks=[{"check": "destination_exists", "passed": True}])
            return VerificationResult.fail("Destination file not found after copy")
        except Exception as e:
            return VerificationResult.fail(str(e))


# ---------------------------------------------------------------------------
# Move File Tool
# ---------------------------------------------------------------------------

class MoveFileArgs(BaseModel):
    source_path: str = Field(..., description="Source file or folder path")
    dest_path: str = Field(..., description="Destination path")


class MoveFileTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="os.move_file",
            description="Move a file or folder to a new location.",
            risk_level=RiskLevel.MEDIUM,
            requires_confirmation=False,
            verification_type=VerificationType.AUTOMATIC,
            timeout_seconds=60,
            category="filesystem",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return MoveFileArgs

    async def _execute(self, args: MoveFileArgs) -> ToolResult:
        try:
            src = _resolve_safe_path(args.source_path)
            dst = _resolve_safe_path(args.dest_path)

            if not src.exists():
                return ToolResult.fail(f"Source not found: {args.source_path}", "FILE_NOT_FOUND", status="NOT_FOUND")

            if dst.is_dir():
                dst = dst / src.name

            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            log.info("fs_move_file", src=str(src), dst=str(dst))
            return ToolResult.ok(data={"source": str(src), "destination": str(dst)})

        except SandboxViolationError as e:
            return ToolResult.fail(error=str(e), error_code="SANDBOX_VIOLATION", status="PERMISSION_DENIED")
        except Exception as e:
            log.exception("fs_move_error", src=args.source_path, dst=args.dest_path)
            return ToolResult.fail(error=str(e), error_code="FS_ERROR")

    async def verify(self, args: MoveFileArgs, result: ToolResult) -> VerificationResult:
        if not result.success:
            return VerificationResult.fail("Move failed")
        try:
            data = result.data or {}
            dst = data.get("destination")
            src = data.get("source")
            dst_exists = dst and Path(dst).exists()
            src_gone = not src or not Path(src).exists()
            if dst_exists and src_gone:
                return VerificationResult.ok(checks=[
                    {"check": "destination_exists", "passed": True},
                    {"check": "source_gone", "passed": True},
                ])
            return VerificationResult.fail("Move verification failed: source or destination state incorrect")
        except Exception as e:
            return VerificationResult.fail(str(e))


# ---------------------------------------------------------------------------
# Rename File Tool
# ---------------------------------------------------------------------------

class RenameFileArgs(BaseModel):
    file_path: str = Field(..., description="Current file path")
    new_name: str = Field(..., description="New filename (not path — just the name)")


class RenameFileTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="os.rename_file",
            description="Rename a file or folder.",
            risk_level=RiskLevel.MEDIUM,
            requires_confirmation=False,
            verification_type=VerificationType.AUTOMATIC,
            timeout_seconds=15,
            category="filesystem",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return RenameFileArgs

    async def _execute(self, args: RenameFileArgs) -> ToolResult:
        try:
            src = _resolve_safe_path(args.file_path)

            if not src.exists():
                return ToolResult.fail(f"File not found: {args.file_path}", "FILE_NOT_FOUND", status="NOT_FOUND")

            dst = src.parent / args.new_name
            src.rename(dst)
            log.info("fs_rename_file", src=str(src), dst=str(dst))
            return ToolResult.ok(data={"old_path": str(src), "new_path": str(dst)})

        except SandboxViolationError as e:
            return ToolResult.fail(error=str(e), error_code="SANDBOX_VIOLATION", status="PERMISSION_DENIED")
        except Exception as e:
            log.exception("fs_rename_error", path=args.file_path)
            return ToolResult.fail(error=str(e), error_code="FS_ERROR")

    async def verify(self, args: RenameFileArgs, result: ToolResult) -> VerificationResult:
        if not result.success:
            return VerificationResult.fail("Rename failed")
        try:
            data = result.data or {}
            new_path = data.get("new_path")
            if new_path and Path(new_path).exists():
                return VerificationResult.ok(checks=[{"check": "renamed_file_exists", "passed": True}])
            return VerificationResult.fail("Renamed file not found")
        except Exception as e:
            return VerificationResult.fail(str(e))


# ---------------------------------------------------------------------------
# Delete File Tool  (HIGH risk — requires approval)
# ---------------------------------------------------------------------------

class DeleteFileArgs(BaseModel):
    file_path: str = Field(..., description="Path to the file or folder to delete")
    recursive: bool = Field(False, description="If True, delete folder and all its contents.")


class DeleteFileTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="os.delete_file",
            description="Delete a file or folder. Folder deletion requires recursive=True.",
            risk_level=RiskLevel.HIGH,
            requires_confirmation=True,
            verification_type=VerificationType.REQUIRED,
            timeout_seconds=30,
            category="filesystem",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return DeleteFileArgs

    async def _execute(self, args: DeleteFileArgs) -> ToolResult:
        try:
            safe_path = _resolve_safe_path(args.file_path)

            if not safe_path.exists():
                return ToolResult.fail(f"File/folder not found: {args.file_path}", "FILE_NOT_FOUND", status="NOT_FOUND")

            if safe_path.is_dir():
                if not args.recursive:
                    return ToolResult.fail(
                        f"'{args.file_path}' is a directory. Set recursive=True to delete it.",
                        "IS_DIRECTORY"
                    )
                shutil.rmtree(str(safe_path))
            else:
                safe_path.unlink()

            log.info("fs_delete_file", path=str(safe_path), recursive=args.recursive)
            return ToolResult.ok(data={"deleted_path": str(safe_path)})

        except SandboxViolationError as e:
            return ToolResult.fail(error=str(e), error_code="SANDBOX_VIOLATION", status="PERMISSION_DENIED")
        except Exception as e:
            log.exception("fs_delete_error", path=args.file_path)
            return ToolResult.fail(error=str(e), error_code="FS_ERROR")

    async def verify(self, args: DeleteFileArgs, result: ToolResult) -> VerificationResult:
        if not result.success:
            return VerificationResult.fail("Delete failed")
        try:
            data = result.data or {}
            deleted_path = data.get("deleted_path")
            if deleted_path and not Path(deleted_path).exists():
                return VerificationResult.ok(checks=[{"check": "file_deleted", "passed": True}])
            return VerificationResult.fail("File still exists after deletion")
        except Exception as e:
            return VerificationResult.fail(str(e))


# ---------------------------------------------------------------------------
# Search Files Tool
# ---------------------------------------------------------------------------

class SearchFilesArgs(BaseModel):
    search_path: str = Field(default="home", description="Root directory to search in. Defaults to home.")
    pattern: str = Field(default="*", description="Filename pattern (glob). E.g. '*.py', 'report*.pdf'")
    extension: str | None = Field(default=None, description="Filter by extension without dot, e.g. 'py', 'txt'")
    modified_within_days: int | None = Field(default=None, description="Only files modified within N days")
    max_results: int = Field(default=50, description="Maximum number of results to return")


class SearchFilesTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="os.search_files",
            description="Search for files by name pattern, extension, or modification date.",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=60,
            category="filesystem",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return SearchFilesArgs

    async def _execute(self, args: SearchFilesArgs) -> ToolResult:
        try:
            root = _resolve_safe_path(args.search_path)

            if not root.exists():
                return ToolResult.fail(f"Search path not found: {args.search_path}", "DIR_NOT_FOUND", status="NOT_FOUND")

            pattern = args.pattern
            if args.extension:
                ext = args.extension.lstrip(".")
                pattern = f"*.{ext}"

            cutoff = None
            if args.modified_within_days is not None:
                cutoff = datetime.now(timezone.utc) - timedelta(days=args.modified_within_days)

            results = []
            for p in root.rglob(pattern):
                if not p.is_file():
                    continue
                if cutoff:
                    mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
                    if mtime < cutoff:
                        continue
                results.append({
                    "path": str(p),
                    "name": p.name,
                    "size_bytes": p.stat().st_size,
                    "modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat(),
                })
                if len(results) >= args.max_results:
                    break

            log.info("fs_search_files", root=str(root), pattern=pattern, count=len(results))
            return ToolResult.ok(data={"results": results, "count": len(results), "root": str(root)})

        except SandboxViolationError as e:
            return ToolResult.fail(error=str(e), error_code="SANDBOX_VIOLATION", status="PERMISSION_DENIED")
        except Exception as e:
            log.exception("fs_search_error", path=args.search_path)
            return ToolResult.fail(error=str(e), error_code="FS_ERROR")
