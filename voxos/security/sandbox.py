"""
Path Sandboxing.

Ensures that the agent cannot read or write files outside of
approved directory structures (e.g., the workspace).
Prevents directory traversal attacks.
"""

from __future__ import annotations

import os
from pathlib import Path

from voxos.observability.logger import get_logger

log = get_logger(__name__)


class SandboxViolationError(Exception):
    """Raised when an operation attempts to access paths outside the sandbox."""


def resolve_and_verify_path(requested_path: str | Path, allowed_roots: list[str | Path]) -> Path:
    """
    Resolve a path and ensure it falls strictly within one of the allowed roots.
    
    Args:
        requested_path: The path the agent wants to access
        allowed_roots: List of safe root directories
        
    Returns:
        The fully resolved, absolute Path
        
    Raises:
        SandboxViolationError: If the path escapes the allowed roots
    """
    target = Path(requested_path).resolve()
    
    for root in allowed_roots:
        root_path = Path(root).resolve()
        try:
            # If target is relative to root_path, it's inside it
            target.relative_to(root_path)
            return target
        except ValueError:
            continue
            
    # If we get here, it didn't match any allowed root
    log.warning(
        "sandbox_violation",
        target=str(target),
        allowed=[str(Path(r).resolve()) for r in allowed_roots],
    )
    raise SandboxViolationError(f"Access to path '{target}' is denied by the sandbox.")
