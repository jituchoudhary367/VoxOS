"""
FastAPI Dependencies.
"""

from __future__ import annotations

from typing import AsyncGenerator
from fastapi import Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from voxos.database.engine import get_async_session
from voxos.core.permissions.manager import PermissionManager, get_permission_manager
from voxos.observability.logger import get_logger
from voxos.config import get_settings
from voxos.security.auth import verify_token

log = get_logger(__name__)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that yields a database session."""
    async for session in get_async_session():
        yield session


async def get_current_user_permissions(
    x_user_id: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> PermissionManager:
    """
    Resolve permissions from a Bearer JWT, with a development-only admin shortcut.
    """
    settings = get_settings()

    if x_user_id == "admin" and not settings.is_production:
        # Admin user
        from voxos.core.permissions.manager import Permission
        manager = PermissionManager()
        manager.grant(Permission.ADMIN)
        return manager

    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and token:
            payload = verify_token(token)
            if payload is not None:
                raw_permissions = payload.get("permissions", {})
                if isinstance(raw_permissions, list):
                    raw_permissions = {"granted": raw_permissions}
                if not isinstance(raw_permissions, dict):
                    raw_permissions = {}
                return PermissionManager.from_user_dict(raw_permissions)

    if settings.is_production:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid Bearer token is required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return get_permission_manager()
