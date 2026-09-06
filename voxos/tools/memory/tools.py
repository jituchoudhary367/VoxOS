from __future__ import annotations

from pydantic import BaseModel, Field

from voxos.observability.logger import get_logger
from voxos.tools.base import BaseTool, RiskLevel, ToolResult, ToolMetadata, VerificationType
from voxos.core.memory.manager import get_memory_manager

log = get_logger(__name__)


class MemorySaveArgs(BaseModel):
    user_id: str = Field(..., description="ID of the user to save the memory for")
    content: str = Field(..., description="The fact or preference to remember")
    category: str = Field(default="general", description="Category of memory (e.g. general, preference, fact)")
    importance: int = Field(default=1, description="Importance score from 1-5")


class MemorySaveTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="memory.save",
            description="Save a fact, preference, or context to the long-term memory system.",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=10,
            category="memory",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return MemorySaveArgs

    async def _execute(self, args: MemorySaveArgs) -> ToolResult:
        try:
            manager = get_memory_manager()
            entry = await manager.add_memory(
                user_id=args.user_id,
                content=args.content,
                category=args.category,
                importance=args.importance
            )
            return ToolResult.ok(
                data={
                    "id": entry.id,
                    "status": "saved",
                    "content": entry.content
                }
            )
        except Exception as e:
            log.exception("memory_save_error", error=str(e))
            return ToolResult.fail(error=str(e), error_code="MEMORY_SAVE_ERROR")


class MemorySearchArgs(BaseModel):
    user_id: str = Field(..., description="ID of the user to search memories for")
    query: str = Field(..., description="Search query or keyword to find relevant memories")
    limit: int = Field(default=5, description="Maximum number of memories to return")


class MemorySearchTool(BaseTool):
    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="memory.search",
            description="Search the long-term memory system for relevant facts, preferences, or past context.",
            risk_level=RiskLevel.LOW,
            requires_confirmation=False,
            verification_type=VerificationType.NONE,
            timeout_seconds=10,
            category="memory",
        )

    @property
    def input_schema(self) -> type[BaseModel]:
        return MemorySearchArgs

    async def _execute(self, args: MemorySearchArgs) -> ToolResult:
        try:
            manager = get_memory_manager()
            memories = await manager.get_relevant_memories(
                user_id=args.user_id,
                query=args.query,
                limit=args.limit
            )
            
            results = [
                {
                    "id": m.id,
                    "content": m.content,
                    "category": m.category,
                    "importance": m.importance
                }
                for m in memories
            ]
            
            return ToolResult.ok(
                data={
                    "query": args.query,
                    "results": results,
                    "count": len(results)
                }
            )
        except Exception as e:
            log.exception("memory_search_error", error=str(e))
            return ToolResult.fail(error=str(e), error_code="MEMORY_SEARCH_ERROR")
