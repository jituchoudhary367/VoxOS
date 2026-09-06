"""
Async SQLAlchemy engine and session factory.

All database interaction must use the async session from get_async_session().
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from voxos.config import get_settings
from voxos.observability.logger import get_logger

log = get_logger(__name__)

_engine: AsyncEngine | None = None
_async_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Return the async SQLAlchemy engine (singleton)."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.db.url,
            echo=settings.debug,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            connect_args={
                "command_timeout": 10,
                "server_settings": {"application_name": "voxos"},
            },
        )
        log.info("database_engine_created", url=settings.db.url.split("@")[1] if "@" in settings.db.url else "?")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the async session factory (singleton)."""
    global _async_session_factory
    if _async_session_factory is None:
        _async_session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
            autocommit=False,
        )
    return _async_session_factory


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an AsyncSession per request."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def close_engine() -> None:
    """Dispose of the engine (call on app shutdown)."""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        log.info("database_engine_closed")
