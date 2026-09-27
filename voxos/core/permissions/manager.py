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
    # Use saved logins (the user consented by adding them with the vault CLI;
    # the tool itself enforces the login request and the domain).
    CREDENTIAL_USE = "credential.use"
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
    Permission.TERMINAL_EXECUTE,
    Permission.CREDENTIAL_USE,
}

# Tool name → required permission
TOOL_PERMISSION_MAP: dict[str, Permission] = {
    "os.list_dir": Permission.FILESYSTEM_READ,
    "os.read_file": Permission.FILESYSTEM_READ,
    "os.write_file": Permission.FILESYSTEM_WRITE,
    "os.create_folder": Permission.FILESYSTEM_WRITE,
    "os.copy_file": Permission.FILESYSTEM_WRITE,
    "os.move_file": Permission.FILESYSTEM_WRITE,
    "os.rename_file": Permission.FILESYSTEM_WRITE,
    "os.delete_file": Permission.FILESYSTEM_DELETE,
    "os.search_files": Permission.FILESYSTEM_READ,
    "os.archive": Permission.FILESYSTEM_WRITE,
    "os.extract": Permission.FILESYSTEM_WRITE,
    "os.shell_command": Permission.TERMINAL_EXECUTE,
    "terminal.execute": Permission.TERMINAL_EXECUTE,
    "terminal.list_processes": Permission.SYSTEM_INFO,
    "terminal.kill_process": Permission.SYSTEM_MODIFY,
    "browser.open": Permission.BROWSER_NAVIGATE,
    "browser.search": Permission.WEB_SEARCH,
    "browser.extract": Permission.BROWSER_NAVIGATE,
    "browser.click": Permission.BROWSER_NAVIGATE,
    "browser.type": Permission.BROWSER_FORM,
    "browser.screenshot": Permission.BROWSER_NAVIGATE,
    "browser.youtube_search": Permission.WEB_SEARCH,
    "browser.youtube_play": Permission.BROWSER_NAVIGATE,
    "browser.new_tab": Permission.BROWSER_NAVIGATE,
    "browser.close_tab": Permission.BROWSER_NAVIGATE,
    "browser.back": Permission.BROWSER_NAVIGATE,
    "browser.scroll": Permission.BROWSER_NAVIGATE,
    "browser.media": Permission.BROWSER_NAVIGATE,
    "browser.fill_credential": Permission.CREDENTIAL_USE,
    "vision.look": Permission.SYSTEM_INFO,
    "application.open": Permission.APPLICATION_CONTROL,
    "application.close": Permission.APPLICATION_CONTROL,
    "application.list": Permission.SYSTEM_INFO,
    "system.get_info": Permission.SYSTEM_INFO,
    "system.get_time": Permission.SYSTEM_INFO,
    "system.get_resources": Permission.SYSTEM_INFO,
    "desktop.screenshot": Permission.SYSTEM_INFO,
    "desktop.list_apps": Permission.SYSTEM_INFO,
    "desktop.open_app": Permission.APPLICATION_CONTROL,
    "desktop.close_app": Permission.APPLICATION_CONTROL,
    "desktop.list_windows": Permission.SYSTEM_INFO,
    "desktop.focus_window": Permission.APPLICATION_CONTROL,
    "desktop.minimize_window": Permission.APPLICATION_CONTROL,
    "desktop.maximize_window": Permission.APPLICATION_CONTROL,
    "desktop.type_text": Permission.APPLICATION_CONTROL,
    "desktop.hotkey": Permission.APPLICATION_CONTROL,
    "desktop.press_key": Permission.APPLICATION_CONTROL,
    "desktop.mouse_click": Permission.APPLICATION_CONTROL,
    "desktop.mouse_scroll": Permission.APPLICATION_CONTROL,
    "desktop.clipboard_read": Permission.DOCUMENT_READ,
    "desktop.clipboard_write": Permission.DOCUMENT_WRITE,
    "web.search": Permission.WEB_SEARCH,
    "web.extract": Permission.WEB_SEARCH,
    "document.read": Permission.DOCUMENT_READ,
    "memory.save": Permission.MEMORY_WRITE,
    "memory.search": Permission.MEMORY_READ,
    "code.python_repl": Permission.TERMINAL_EXECUTE,
}


class PermissionManager:
    """
    Checks whether a given set of permissions allows a tool call.

    In production, permissions come from the User model (DB).
    In development, uses DEFAULT_PERMISSIONS.
    """

    def __init__(self, user_permissions: set[Permission] | None = None) -> None:
        self._permissions = set(DEFAULT_PERMISSIONS if user_permissions is None else user_permissions)

    def allows_tool(self, tool_name: str) -> bool:
        """can_use_tool without logging: for listing tools, not enforcing."""
        required = TOOL_PERMISSION_MAP.get(tool_name)
        if required is None:
            return False
        return Permission.ADMIN in self._permissions or required in self._permissions

    def can_use_tool(self, tool_name: str) -> bool:
        """Return True if the user's permissions allow this tool."""
        required = TOOL_PERMISSION_MAP.get(tool_name)
        if required is None:
            # Unknown tools must be denied until explicitly mapped.
            log.debug("permission_unknown_tool", tool=tool_name)
            return False

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
