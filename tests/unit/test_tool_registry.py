"""Test Tool Registry."""

import pytest
from voxos.tools.registry import ToolRegistry
from voxos.tools.base import BaseTool, ToolResult, ToolStatus
from pydantic import BaseModel


class DummyArgs(BaseModel):
    name: str


class DummyTool(BaseTool):
    @property
    def name(self) -> str:
        return "dummy_tool"
        
    @property
    def description(self) -> str:
        return "A dummy tool."
        
    @property
    def input_schema(self) -> type[BaseModel]:
        return DummyArgs

    async def _execute(self, args: DummyArgs) -> ToolResult:
        return ToolResult(status=ToolStatus.SUCCESS, data={"hello": args.name})


def test_registry_registration():
    registry = ToolRegistry()
    tool = DummyTool()
    registry.register(tool)
    
    assert registry.tool_count() == 1
    assert registry.has_tool("dummy_tool")
    
    retrieved = registry.get("dummy_tool")
    assert retrieved is not None
    assert retrieved.name == "dummy_tool"


def test_registry_get_tools_metadata():
    registry = ToolRegistry()
    registry.register(DummyTool())
    
    metadata = registry.get_all_tools_metadata()
    assert len(metadata) == 1
    assert metadata[0]["name"] == "dummy_tool"
    assert "schema" in metadata[0]
