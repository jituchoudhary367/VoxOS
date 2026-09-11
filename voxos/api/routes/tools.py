"""Tools API route."""

from __future__ import annotations

from fastapi import APIRouter

from voxos.tools.registry import get_tool_registry

router = APIRouter()


@router.get("/tools")
async def list_tools():
    """List all registered tools available to the Executor."""
    registry = get_tool_registry()
    
    tools_list = []
    for tool_name, tool in registry._tools.items():
        metadata = tool.metadata
        tools_list.append({
            "name": tool_name,
            "description": metadata.description,
            "category": metadata.category,
            "risk_level": metadata.risk_level.value,
        })
        
    return {"tools": tools_list, "total": len(tools_list)}
