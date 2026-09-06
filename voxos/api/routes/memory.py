"""
Memory API Endpoints.
"""

from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from voxos.api.deps import get_db_session
from voxos.core.memory.manager import get_memory_manager
from voxos.database.repositories.memory_repo import MemoryRepository

router = APIRouter(prefix="/memory", tags=["Memory"])


@router.get("/")
async def list_memories(
    user_id: str = "default",
    limit: int = 50,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """List memories for a user."""
    repo = MemoryRepository(db)
    memories = await repo.list_for_user(user_id=user_id, limit=limit)
    return {
        "memories": [
            {
                "id": m.id,
                "type": m.memory_type,
                "content": m.content,
                "importance": m.importance,
                "created_at": m.created_at,
            }
            for m in memories
        ]
    }


@router.delete("/{memory_id}")
async def delete_memory(
    memory_id: str,
) -> dict[str, Any]:
    """Delete a memory entry."""
    manager = get_memory_manager()
    deleted = await manager.delete(memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"status": "deleted", "id": memory_id}


@router.post("/search")
async def search_memories(
    query: str,
    user_id: str = "default",
    limit: int = 5,
) -> dict[str, Any]:
    """Search for memories."""
    manager = get_memory_manager()
    memories = await manager.retrieve(query=query, user_id=user_id, limit=limit)
    return {
        "memories": [
            {
                "id": m.id,
                "type": m.memory_type,
                "content": m.content,
                "importance": m.importance,
            }
            for m in memories
        ]
    }
