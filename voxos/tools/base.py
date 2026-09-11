"""
Tool Base Classes and Contracts.

Every VoxOS tool must:
1. Inherit from BaseTool
2. Declare its risk level
3. Define typed input/output schemas (Pydantic)
4. Implement execute()
5. Optionally implement verify()

SECURITY RULE: The LLM never directly calls tools. The Executor agent
mediates all tool calls through the ToolRegistry.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeVar

from pydantic import BaseModel

from voxos.observability.logger import get_logger
from voxos.observability.metrics import get_metrics

log = get_logger(__name__)
metrics = get_metrics()


class RiskLevel(str, Enum):
    """Risk classification for tool operations."""

    LOW = "LOW"           # Read-only, no side effects (e.g., get_info, list_files)
    SAFE = "LOW"           # Backwards-compatible alias for LOW
    MEDIUM = "MEDIUM"     # Reversible changes (e.g., create_file, install_package)
    HIGH = "HIGH"         # Potentially destructive (e.g., delete_file, execute_script)
    CRITICAL = "CRITICAL" # Irreversible/dangerous (e.g., format_disk, rm -rf /)


class VerificationType(str, Enum):
    """How the Overseer should verify this tool's result."""

    NONE = "NONE"         # No verification needed (e.g., get_time)
    AUTOMATIC = "AUTOMATIC"  # Verify automatically (e.g., file exists after create)
    REQUIRED = "REQUIRED"    # Must verify (e.g., confirm deletion happened)


class ToolStatus(str, Enum):
    """Execution status of a tool call."""

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"


InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)


@dataclass
class ToolResult:
    """Typed result returned by every tool execution."""

    status: ToolStatus
    data: Any | None = None
    error: str | None = None
    error_code: str | None = None
    duration_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.status == ToolStatus.SUCCESS

    @classmethod
    def ok(cls, data: Any, metadata: dict[str, Any] | None = None) -> "ToolResult":
        return cls(status=ToolStatus.SUCCESS, data=data, metadata=metadata or {})

    @classmethod
    def fail(
        cls,
        error: str,
        error_code: str = "TOOL_ERROR",
        status: ToolStatus | str = ToolStatus.FAILED,
        data: Any | None = None,
    ) -> "ToolResult":
        if isinstance(status, str):
            status = ToolStatus(status)
        return cls(status=status, error=error, error_code=error_code, data=data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "data": self.data,
            "error": self.error,
            "error_code": self.error_code,
            "duration_ms": self.duration_ms,
            "metadata": self.metadata,
        }


@dataclass
class VerificationResult:
    """Result from Overseer verification of a tool action."""

    passed: bool
    checks: list[dict[str, Any]] = field(default_factory=list)
    failure_reason: str | None = None

    @classmethod
    def ok(cls, checks: list[dict[str, Any]] | None = None) -> "VerificationResult":
        return cls(passed=True, checks=checks or [])

    @classmethod
    def fail(cls, reason: str, checks: list[dict[str, Any]] | None = None) -> "VerificationResult":
        return cls(passed=False, failure_reason=reason, checks=checks or [])


@dataclass
class ToolMetadata:
    """Metadata exposed by every tool for registration and discovery."""

    name: str
    description: str
    risk_level: RiskLevel
    requires_confirmation: bool
    verification_type: VerificationType
    timeout_seconds: int
    category: str  # filesystem | terminal | browser | desktop | system | etc.
    tags: list[str] = field(default_factory=list)


class BaseTool(ABC):
    """
    Abstract base class for all VoxOS tools.

    Subclasses must implement:
        - metadata (property)
        - input_schema (property) → Pydantic model class
        - execute(args) → ToolResult

    Optionally override:
        - verify(args, result) → VerificationResult
    """

    @property
    def metadata(self) -> ToolMetadata:
        """Tool metadata for registry and risk engine.

        The fallback keeps older third-party tools compatible while built-in
        tools should continue to provide explicit metadata.
        """
        return ToolMetadata(
            name=self.name,
            description=self.description,
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=30,
            category="general",
        )

    @property
    def name(self) -> str:
        """Compatibility name for older tools that do not define metadata."""
        return self.__class__.__name__

    @property
    def description(self) -> str:
        """Compatibility description for older tools."""
        return self.__class__.__doc__ or self.name

    @property
    @abstractmethod
    def input_schema(self) -> type[BaseModel]:
        """Pydantic model for validating tool arguments."""

    @abstractmethod
    async def _execute(self, args: BaseModel) -> ToolResult:
        """
        Core execution logic. Implement this in each tool.
        Called by execute() which wraps with timing and logging.
        """

    async def execute(self, args: BaseModel | dict[str, Any]) -> ToolResult:
        """
        Execute the tool with timing, error handling, and logging.
        
        Do NOT override this — override _execute() instead.
        """
        meta = self.metadata
        log.info(
            "tool_started",
            tool=meta.name,
            risk=meta.risk_level,
        )

        start = time.monotonic()
        try:
            if isinstance(args, dict):
                args = self.input_schema.model_validate(args)
            result = await self._execute(args)
            result.duration_ms = (time.monotonic() - start) * 1000

            metrics.increment("tool.calls", tool=meta.name, status=result.status.value)
            metrics.record_latency("tool.latency", result.duration_ms, tool=meta.name)

            log.info(
                "tool_completed",
                tool=meta.name,
                status=result.status,
                duration_ms=round(result.duration_ms, 1),
            )
            return result

        except TimeoutError:
            log.warning("tool_timeout", tool=meta.name, timeout_s=meta.timeout_seconds)
            metrics.increment("tool.calls", tool=meta.name, status="TIMEOUT")
            return ToolResult.fail(
                error=f"Tool '{meta.name}' timed out after {meta.timeout_seconds}s",
                error_code="TIMEOUT",
                status=ToolStatus.TIMEOUT,
            )
        except ValueError as e:
            log.warning("tool_validation_error", tool=meta.name, error=str(e))
            return ToolResult.fail(
                error=f"Invalid arguments for '{meta.name}': {str(e)}",
                error_code="VALIDATION_ERROR",
                status=ToolStatus.VALIDATION_ERROR,
            )
        except Exception as e:
            log.error("tool_error", tool=meta.name, error=str(e), exc_info=True)
            metrics.increment("tool.calls", tool=meta.name, status="FAILED")
            return ToolResult.fail(
                error=f"Tool '{meta.name}' failed: {str(e)}",
                error_code="INTERNAL_ERROR",
            )

    async def verify(self, args: BaseModel, result: ToolResult) -> VerificationResult:
        """
        Verify that the tool's action was successful.
        
        Override in tools that need verification.
        Default: skip verification (NONE type).
        """
        if self.metadata.verification_type == VerificationType.NONE:
            return VerificationResult.ok(checks=[{"check": "no_verification_required", "passed": True}])
        return VerificationResult.ok()

    def validate_args(self, raw_args: dict[str, Any]) -> tuple[BaseModel, str | None]:
        """
        Validate raw arguments against the input schema.
        
        Returns:
            (validated_model, error_message) — error is None on success
        """
        try:
            validated = self.input_schema.model_validate(raw_args)
            return validated, None
        except Exception as e:
            return self.input_schema.model_construct(), str(e)

    def to_openai_tool_spec(self) -> dict[str, Any]:
        """Generate OpenAI function tool specification for LLM tool-calling."""
        schema = self.input_schema.model_json_schema()
        return {
            "type": "function",
            "function": {
                "name": self.metadata.name.replace(".", "_"),
                "description": self.metadata.description,
                "parameters": schema,
            },
        }
