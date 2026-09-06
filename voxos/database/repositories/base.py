"""Base repository providing generic CRUD operations."""

from typing import Any, Generic, Sequence, Type, TypeVar
import uuid

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import NoResultFound

from voxos.database.models.base import Base

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    """Generic repository with common database operations."""

    def __init__(self, model: Type[ModelType], session: AsyncSession):
        self.model = model
        self.session = session

    async def get(self, id: uuid.UUID | str) -> ModelType | None:
        """Get a single record by ID."""
        if isinstance(id, str):
            id = uuid.UUID(id)
        stmt = select(self.model).where(self.model.id == id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all(self, skip: int = 0, limit: int = 100) -> Sequence[ModelType]:
        """Get a list of records."""
        stmt = select(self.model).offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def create(self, obj_in: dict[str, Any] | ModelType) -> ModelType:
        """Create a new record."""
        if isinstance(obj_in, dict):
            db_obj = self.model(**obj_in)
        else:
            db_obj = obj_in
        self.session.add(db_obj)
        await self.session.flush()
        return db_obj

    async def update(self, id: uuid.UUID | str, obj_in: dict[str, Any]) -> ModelType | None:
        """Update a record by ID."""
        if isinstance(id, str):
            id = uuid.UUID(id)
        
        stmt = update(self.model).where(self.model.id == id).values(**obj_in).returning(self.model)
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.scalar_one_or_none()

    async def delete(self, id: uuid.UUID | str) -> bool:
        """Delete a record by ID."""
        if isinstance(id, str):
            id = uuid.UUID(id)
            
        stmt = delete(self.model).where(self.model.id == id)
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount > 0
