"""
Overseer Verification Strategies.

Each strategy defines how to verify that a specific type of tool
execution actually produced the expected outcome.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from voxos.tools.base import ToolResult
from voxos.observability.logger import get_logger

log = get_logger(__name__)


class VerificationStrategy(ABC):
    """Abstract base for verification strategies."""

    @abstractmethod
    def can_verify(self, tool_name: str) -> bool:
        """Return True if this strategy handles the given tool."""

    @abstractmethod
    async def verify(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: ToolResult,
    ) -> tuple[bool, str | None]:
        """
        Verify the result.

        Returns:
            (True, None) — verification passed
            (False, reason) — verification failed with reason
        """


class ResultPresentStrategy(VerificationStrategy):
    """
    Basic verification: tool returned success=True with non-empty data.
    
    Covers most read-only tools where we just need a result.
    """

    def can_verify(self, tool_name: str) -> bool:
        return True  # Fallback for all tools

    async def verify(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: ToolResult,
    ) -> tuple[bool, str | None]:
        if not result.success:
            return False, f"Tool reported failure: {result.error}"
        if result.data is None:
            return False, "Tool returned no data"
        return True, None


class FilesystemVerificationStrategy(VerificationStrategy):
    """Verify filesystem operations by checking the actual filesystem."""

    FILESYSTEM_TOOLS = {
        "filesystem.create", "filesystem.write",
        "filesystem.copy", "filesystem.move", "filesystem.rename",
    }

    def can_verify(self, tool_name: str) -> bool:
        return tool_name in self.FILESYSTEM_TOOLS

    async def verify(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: ToolResult,
    ) -> tuple[bool, str | None]:
        import asyncio
        from pathlib import Path

        if not result.success:
            return False, f"Tool reported failure: {result.error}"

        # Check that the target file/directory actually exists
        target_path = (
            arguments.get("destination")
            or arguments.get("path")
            or arguments.get("new_path")
        )

        if target_path:
            p = Path(str(target_path))
            exists = await asyncio.to_thread(p.exists)
            if not exists:
                return False, f"Expected path does not exist after operation: {target_path}"

        return True, None


class CommandVerificationStrategy(VerificationStrategy):
    """Verify terminal commands by checking exit code and output."""

    def can_verify(self, tool_name: str) -> bool:
        return tool_name == "terminal.execute"

    async def verify(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: ToolResult,
    ) -> tuple[bool, str | None]:
        if not result.success:
            return False, f"Command failed: {result.error}"

        data = result.data or {}
        exit_code = data.get("exit_code", 0)
        if exit_code != 0:
            stderr = data.get("stderr", "")
            return False, f"Command exited with code {exit_code}: {stderr[:200]}"

        return True, None


class ApplicationVerificationStrategy(VerificationStrategy):
    """Verify application operations by checking the app list."""

    def can_verify(self, tool_name: str) -> bool:
        return tool_name in {"application.open", "application.close"}

    async def verify(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: ToolResult,
    ) -> tuple[bool, str | None]:
        # For now, trust the tool's success report
        # In Stage 7, we'll use vision to verify app windows
        if not result.success:
            return False, f"Application operation failed: {result.error}"
        return True, None


class VerificationStrategyRegistry:
    """
    Routes verification to the appropriate strategy.

    Checks strategies in priority order; falls back to ResultPresentStrategy.
    """

    def __init__(self) -> None:
        self._strategies: list[VerificationStrategy] = [
            FilesystemVerificationStrategy(),
            CommandVerificationStrategy(),
            ApplicationVerificationStrategy(),
            ResultPresentStrategy(),  # Fallback — must be last
        ]

    async def verify(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: ToolResult,
    ) -> tuple[bool, str | None]:
        """Find the right strategy and run verification."""
        for strategy in self._strategies:
            if strategy.can_verify(tool_name):
                try:
                    passed, reason = await strategy.verify(tool_name, arguments, result)
                    log.debug(
                        "verification_result",
                        strategy=type(strategy).__name__,
                        tool=tool_name,
                        passed=passed,
                        reason=reason,
                    )
                    return passed, reason
                except Exception as e:
                    log.warning(
                        "verification_strategy_error",
                        strategy=type(strategy).__name__,
                        tool=tool_name,
                        error=str(e),
                    )
                    # Fall through to next strategy
        return True, None  # No strategy could verify — assume pass


_registry: VerificationStrategyRegistry | None = None


def get_verifier_registry() -> VerificationStrategyRegistry:
    global _registry
    if _registry is None:
        _registry = VerificationStrategyRegistry()
    return _registry
