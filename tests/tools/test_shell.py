import pytest
from voxos.tools.os.shell import ShellCommandTool, ShellCommandArgs

@pytest.mark.asyncio
async def test_shell_command_success():
    tool = ShellCommandTool()
    # Assuming Windows environment
    args = ShellCommandArgs(command="echo 'hello world'")
    result = await tool.execute(args)
    
    assert result.success is True
    assert result.data is not None
    assert "hello world" in result.data["stdout"].lower()
    assert result.data["exit_code"] == 0

@pytest.mark.asyncio
async def test_shell_command_failure():
    tool = ShellCommandTool()
    # A command that definitely fails
    args = ShellCommandArgs(command="exit 1")
    result = await tool.execute(args)
    
    assert result.success is False
    assert result.error_code == "NON_ZERO_EXIT"
    assert result.data["exit_code"] == 1
