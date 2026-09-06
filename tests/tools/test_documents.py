import pytest
from unittest.mock import patch, MagicMock

from voxos.tools.documents.tools import DocumentReadTool, DocumentReadArgs

@pytest.mark.asyncio
@patch("voxos.tools.documents.tools.os.path.isfile")
@patch("voxos.tools.documents.tools.resolve_and_verify_path")
@patch("voxos.tools.documents.tools.parse_document")
async def test_document_read(mock_parse, mock_verify, mock_isfile):
    mock_verify.return_value = "/safe/path/test.txt"
    
    mock_isfile.return_value = True
    mock_parse.return_value = "This is a test document."
    
    tool = DocumentReadTool()
    args = DocumentReadArgs(path="test.txt")
    result = await tool.execute(args)
    
    assert result.success is True
    assert result.data["content"] == "This is a test document."
    mock_parse.assert_called_once_with("/safe/path/test.txt")
