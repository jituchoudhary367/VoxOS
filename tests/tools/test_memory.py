import pytest
import uuid
from unittest.mock import patch, AsyncMock, MagicMock

from voxos.tools.memory.tools import MemorySaveTool, MemorySearchTool, MemorySaveArgs, MemorySearchArgs
from voxos.core.memory.manager import MemoryEntry

@pytest.mark.asyncio
@patch("voxos.tools.memory.tools.get_memory_manager")
async def test_memory_save(mock_get_manager):
    mock_manager = AsyncMock()
    mock_get_manager.return_value = mock_manager
    
    mock_manager.add_memory.return_value = MemoryEntry(
        id=str(uuid.uuid4()),
        user_id=str(uuid.uuid4()),
        content="User likes blue",
        category="preference",
        importance=3
    )
    
    tool = MemorySaveTool()
    args = MemorySaveArgs(user_id=str(uuid.uuid4()), content="User likes blue", category="preference", importance=3)
    result = await tool.execute(args)
    
    assert result.success is True
    assert result.data["content"] == "User likes blue"
    mock_manager.add_memory.assert_called_once_with(
        user_id=args.user_id,
        content="User likes blue",
        category="preference",
        importance=3
    )


@pytest.mark.asyncio
@patch("voxos.tools.memory.tools.get_memory_manager")
async def test_memory_search(mock_get_manager):
    mock_manager = AsyncMock()
    mock_get_manager.return_value = mock_manager
    
    mock_manager.get_relevant_memories.return_value = [
        MemoryEntry(
            id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            content="User likes blue",
            category="preference",
            importance=3
        )
    ]
    
    tool = MemorySearchTool()
    args = MemorySearchArgs(user_id=str(uuid.uuid4()), query="blue", limit=5)
    result = await tool.execute(args)
    
    assert result.success is True
    assert result.data["count"] == 1
    assert result.data["results"][0]["content"] == "User likes blue"
    mock_manager.get_relevant_memories.assert_called_once_with(
        user_id=args.user_id,
        query="blue",
        limit=5
    )
