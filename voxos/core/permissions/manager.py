"""
Permission Manager — checks whether a user is allowed to perform an action.

Permissions are stored as JSONB on the User model and follow a simple
deny-first, allowlist approach.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from voxos.observability.logger import get_logger

log = get_logger(__name__)


class Permission(str, Enum):
    """Coarse-grained permission scopes."""

    # Tool categories
    FILESYSTEM_READ = "filesystem.read"
    FILESYSTEM_WRITE = "filesystem.write"
    FILESYSTEM_DELETE = "filesystem.delete"
    TERMINAL_EXECUTE = "terminal.execute"
    BROWSER_NAVIGATE = "browser.navigate"
    BROWSER_FORM = "browser.form"
    APPLICATION_CONTROL = "application.control"
    SYSTEM_INFO = "system.info"
    SYSTEM_MODIFY = "system.modify"
    WEB_SEARCH = "web.search"
    DOCUMENT_READ = "document.read"
    DOCUMENT_WRITE = "document.write"
    MEMORY_READ = "memory.read"
    MEMORY_WRITE = "memory.write"

    # Admin
    ADMIN = "admin"


# Default permissions for a standard user
DEFAULT_PERMISSIONS: set[Permission] = {
    Permission.FILESYSTEM_READ,
    Permission.FILESYSTEM_WRITE,
    Permission.BROWSER_NAVIGATE,
    Permission.APPLICATION_CONTROL,
    Permission.SYSTEM_INFO,
    Permission.WEB_SEARCH,
    Permission.DOCUMENT_READ,
    Permission.DOCUMENT_WRITE,
    Permission.MEMORY_READ,
    Permission.MEMORY_WRITE,
}

# Tool name → required permission
TOOL_PERMISSION_MAP: dict[str, Permission] = {
    "filesystem.list": Permission.FILESYSTEM_READ,
    "filesystem.read": Permission.FILESYSTEM_READ,
    "filesystem.create": Permission.FILESYSTEM_WRITE,
    "filesystem.write": Permission.FILESYSTEM_WRITE,
    "filesystem.copy": Permission.FILESYSTEM_WRITE,
    "filesystem.move": Permission.FILESYSTEM_WRITE,
    "filesystem.rename": Permission.FILESYSTEM_WRITE,
    "filesystem.delete": Permission.FILESYSTEM_DELETE,
    "filesystem.archive": Permission.FILESYSTEM_WRITE,
    "terminal.execute": Permission.TERMINAL_EXECUTE,
    "terminal.list_processes": Permission.SYSTEM_INFO,
    "terminal.kill_process": Permission.SYSTEM_MODIFY,
    "browser.open": Permission.BROWSER_NAVIGATE,
    "browser.search": Permission.WEB_SEARCH,
    "browser.click": Permission.BROWSER_NAVIGATE,
    "browser.type": Permission.BROWSER_FORM,
    "browser.screenshot": Permission.BROWSER_NAVIGATE,
    "application.open": Permission.APPLICATION_CONTROL,
    "application.close": Permission.APPLICATION_CONTROL,
    "application.list": Permission.SYSTEM_INFO,
    "system.get_info": Permission.SYSTEM_INFO,
    "system.get_time": Permission.SYSTEM_INFO,
    "system.get_resources": Permission.SYSTEM_INFO,
    "desktop.screenshot": Permission.SYSTEM_INFO,
    "desktop.click": Permission.APPLICATION_CONTROL,
    "web_search.search": Permission.WEB_SEARCH,
    "document.read": Permission.DOCUMENT_READ,
    "document.create": Permission.DOCUMENT_WRITE,
    "document.summarize": Permission.DOCUMENT_READ,
    "memory.store": Permission.MEMORY_WRITE,
    "memory.retrieve": Permission.MEMORY_READ,
    "memory.delete": Permission.MEMORY_WRITE,
}


class PermissionManager:
    """
    Checks whether a given set of permissions allows a tool call.

    In production, permissions come from the User model (DB).
    In development, uses DEFAULT_PERMISSIONS.
    """

    def __init__(self, user_permissions: set[Permission] | None = None) -> None:
        self._permissions = user_permissions or DEFAULT_PERMISSIONS

    def can_use_tool(self, tool_name: str) -> bool:
        """Return True if the user's permissions allow this tool."""
        required = TOOL_PERMISSION_MAP.get(tool_name)
        if required is None:
            # Unknown tool — allow by default (registered tools declare their own risk)
            log.debug("permission_unknown_tool", tool=tool_name)
            return True

        if Permission.ADMIN in self._permissions:
            return True

        allowed = required in self._permissions
        if not allowed:
            log.warning(
                "permission_denied",
                tool=tool_name,
                required=required.value,
                user_has=[p.value for p in self._permissions],
            )
        return allowed

    def has_permission(self, permission: Permission) -> bool:
        return permission in self._permissions or Permission.ADMIN in self._permissions

    def grant(self, permission: Permission) -> None:
        self._permissions.add(permission)

    def revoke(self, permission: Permission) -> None:
        self._permissions.discard(permission)

    @classmethod
    def from_user_dict(cls, permissions_dict: dict[str, Any]) -> "PermissionManager":
        """Build from the User model's JSONB permissions field."""
        perm_set: set[Permission] = set()
        for p in permissions_dict.get("granted", []):
            try:
                perm_set.add(Permission(p))
            except ValueError:
                log.warning("unknown_permission_value", value=p)
        return cls(perm_set)


# Default singleton for unauthenticated/dev use
_default_manager: PermissionManager | None = None


def get_permission_manager() -> PermissionManager:
    global _default_manager
    if _default_manager is None:
        _default_manager = PermissionManager()
    return _default_manager
