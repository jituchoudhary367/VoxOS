import os
import pytest
from pathlib import Path
from voxos.tools.os.fs import ReadFileTool, ReadFileArgs, WriteFileTool, WriteFileArgs, ListDirTool, ListDirArgs

@pytest.fixture
def temp_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("FILESYSTEM_STRICT_MODE", "true")
    # Also need to clear get_settings cache just in case, but since properties instantiate on the fly, it's fine.
    return tmp_path

@pytest.mark.asyncio
async def test_write_and_read_file(temp_workspace):
    test_file = temp_workspace / "test.txt"
    
    write_tool = WriteFileTool()
    write_args = WriteFileArgs(file_path=str(test_file), content="hello fs")
    write_result = await write_tool.execute(write_args)
    assert write_result.success is True
    
    read_tool = ReadFileTool()
    read_args = ReadFileArgs(file_path=str(test_file))
    read_result = await read_tool.execute(read_args)
    assert read_result.success is True
    assert read_result.data["content"] == "hello fs"

@pytest.mark.asyncio
async def test_list_dir(temp_workspace):
    test_file = temp_workspace / "test2.txt"
    test_file.write_text("dummy")
    
    list_tool = ListDirTool()
    list_args = ListDirArgs(dir_path=str(temp_workspace))
    list_result = await list_tool.execute(list_args)
    
    assert list_result.success is True
    items = list_result.data["items"]
    assert len(items) == 1
    assert items[0]["name"] == "test2.txt"

@pytest.mark.asyncio
async def test_sandbox_violation(temp_workspace):
    # Try to read something outside the workspace
    outside_file = Path(temp_workspace).parent / "outside.txt"
    outside_file.write_text("secret")
    
    read_tool = ReadFileTool()
    read_args = ReadFileArgs(file_path=str(outside_file))
    read_result = await read_tool.execute(read_args)
    
    assert read_result.success is False
    assert read_result.error_code == "SANDBOX_VIOLATION"
