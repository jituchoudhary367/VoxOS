import pytest
from voxos.tools.code.repl import PythonREPLTool

@pytest.mark.asyncio
async def test_python_repl_success():
    tool = PythonREPLTool()
    result = await tool.execute({"code": "print('Hello, World!')"})
    
    assert result.success is True
    assert result.status.value == "SUCCESS"
    assert "Hello, World!" in result.data["stdout"]
    assert result.data["return_code"] == 0

@pytest.mark.asyncio
async def test_python_repl_syntax_error():
    tool = PythonREPLTool()
    result = await tool.execute({"code": "print('missing quote)"})
    
    assert result.success is False
    assert result.status.value == "FAILED"
    assert result.error_code == "EXECUTION_FAILED"
    assert "SyntaxError" in result.error

@pytest.mark.asyncio
async def test_python_repl_timeout():
    tool = PythonREPLTool()
    # An infinite loop should timeout
    code = "import time\nwhile True:\n    time.sleep(1)"
    result = await tool.execute({"code": code, "timeout": 1})
    
    assert result.success is False
    assert result.status.value == "FAILED"
    assert result.error_code == "TIMEOUT"
