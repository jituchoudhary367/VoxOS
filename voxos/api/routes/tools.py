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
        tools_list.append({
            "name": tool_name,
            "description": tool.description,
            "category": tool.category,
            "risk_level": tool.risk_level.value,
        })
        
    return {"tools": tools_list, "total": len(tools_list)}
