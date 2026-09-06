import os
import platform
import pytest
from unittest.mock import patch, MagicMock

from voxos.tools.desktop.apps import ListAppsTool, OpenAppTool, CloseAppTool
from voxos.tools.desktop.screen import DesktopScreenshotTool


@pytest.mark.asyncio
async def test_list_apps():
    mock_gw = MagicMock()
    mock_gw.getAllTitles.return_value = ["Window 1", "", "Window 2"]
    
    with patch.dict('sys.modules', {'pygetwindow': mock_gw}):
        tool = ListAppsTool()
        result = await tool.execute({})
        
        assert result.success is True
        assert result.data["windows"] == ["Window 1", "Window 2"]


@pytest.mark.asyncio
@patch("voxos.tools.desktop.apps.os.startfile", create=True)
@patch("voxos.tools.desktop.apps.subprocess.Popen")
async def test_open_app_windows(mock_popen, mock_startfile):
    # Only test if running on Windows, or mock platform.system
    with patch("voxos.tools.desktop.apps.platform.system", return_value="Windows"):
        tool = OpenAppTool()
        result = await tool.execute({"app_path_or_name": "notepad.exe"})
        
        assert result.success is True
        mock_startfile.assert_called_once_with("notepad.exe")


@pytest.mark.asyncio
@patch("voxos.tools.desktop.apps.subprocess.Popen")
async def test_open_app_mac(mock_popen):
    with patch("voxos.tools.desktop.apps.platform.system", return_value="Darwin"):
        tool = OpenAppTool()
        result = await tool.execute({"app_path_or_name": "Calculator"})
        
        assert result.success is True
        mock_popen.assert_called_once_with(["open", "Calculator"])


@pytest.mark.asyncio
async def test_close_app_success():
    mock_gw = MagicMock()
    mock_window = MagicMock()
    mock_gw.getWindowsWithTitle.return_value = [mock_window]
    
    with patch.dict('sys.modules', {'pygetwindow': mock_gw}):
        tool = CloseAppTool()
        result = await tool.execute({"window_title": "Notepad"})
        
        assert result.success is True
        mock_window.close.assert_called_once()
        assert "Closed 1 window(s)" in result.data["status"]


@pytest.mark.asyncio
async def test_close_app_not_found():
    mock_gw = MagicMock()
    mock_gw.getWindowsWithTitle.return_value = []
    
    with patch.dict('sys.modules', {'pygetwindow': mock_gw}):
        tool = CloseAppTool()
        result = await tool.execute({"window_title": "NonExistent"})
        
        assert result.success is False
        assert result.error_code == "NOT_FOUND"


@pytest.mark.asyncio
async def test_desktop_screenshot():
    mock_mss_module = MagicMock()
    mock_sct = MagicMock()
    mock_mss_module.mss.return_value.__enter__.return_value = mock_sct
    mock_sct.monitors = [{"left": 0, "top": 0, "width": 1920, "height": 1080}]
    
    mock_img = MagicMock()
    mock_img.rgb = b"fake_rgb_data"
    mock_img.size = (1920, 1080)
    mock_sct.grab.return_value = mock_img
    
    # Mock to_png to do nothing instead of trying to write real files
    mock_mss_module.tools.to_png = MagicMock()
    
    with patch.dict('sys.modules', {'mss': mock_mss_module}):
        tool = DesktopScreenshotTool()
        result = await tool.execute({"format": "png"})
        
        assert result.success is True
        assert "filepath" in result.data
        assert result.data["filepath"].endswith(".png")
