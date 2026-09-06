"""
FastAPI Dependencies.
"""

from __future__ import annotations

from typing import AsyncGenerator
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from voxos.database.engine import get_async_session
from voxos.core.permissions.manager import PermissionManager, get_permission_manager
from voxos.observability.logger import get_logger

log = get_logger(__name__)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that yields a database session."""
    async for session in get_async_session():
        yield session


async def get_current_user_permissions(
    x_user_id: str | None = Header(default=None),
) -> PermissionManager:
    """
    Mock authentication for now.
    In a real app, this would validate a JWT token and load permissions.
    """
    if x_user_id == "admin":
        # Admin user
        from voxos.core.permissions.manager import Permission
        manager = PermissionManager()
        manager.grant(Permission.ADMIN)
        return manager
        
    return get_permission_manager()
