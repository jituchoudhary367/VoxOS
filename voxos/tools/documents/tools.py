from __future__ import annotations

import os
from pydantic import BaseModel, Field

from voxos.observability.logger import get_logger
from voxos.config import get_settings
from voxos.tools.base import BaseTool, RiskLevel, ToolResult, ToolMetadata, VerificationType
from voxos.tools.documents.parsers import parse_document
from voxos.security.sandbox import resolve_and_verify_path, SandboxViolationError

log = get_logger(__name__)


class DocumentReadArgs(BaseModel):
    path: str = Field(..., description="Path to the document file (e.g. PDF, DOCX, XLSX, TXT)")


class DocumentReadTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="document.read",
            description="Extract and read text from a document file (.pdf, .docx, .xlsx, .csv, .txt, .json).",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=30,
            category="documents",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return DocumentReadArgs

    async def _execute(self, args: DocumentReadArgs) -> ToolResult:
        try:
            # Validate path against sandbox
            settings = get_settings()
            safe_path = resolve_and_verify_path(args.path, [settings.workspace.workspace_path])
            
            if not os.path.isfile(safe_path):
                return ToolResult.fail(f"File not found: {args.path}", "FILE_NOT_FOUND")
                
            log.info("document_read_started", path=args.path)
            
            # Parse document
            content = parse_document(safe_path)
            
            # Truncate to save tokens (approx 15000 chars)
            max_chars = 15000
            truncated = False
            if len(content) > max_chars:
                content = content[:max_chars] + "\n...[TRUNCATED]..."
                truncated = True
                
            log.info("document_read_completed", path=args.path, chars=len(content), truncated=truncated)
            
            return ToolResult.ok(
                data={
                    "path": args.path,
                    "content": content,
                    "truncated": truncated
                }
            )
            
        except SandboxViolationError as e:
            return ToolResult.fail(error=str(e), error_code="SANDBOX_VIOLATION")
        except ValueError as e:
            # Unsupported extension
            return ToolResult.fail(error=str(e), error_code="UNSUPPORTED_FORMAT")
        except Exception as e:
            log.exception("document_read_error", path=args.path, error=str(e))
            return ToolResult.fail(error=str(e), error_code="DOCUMENT_ERROR")
