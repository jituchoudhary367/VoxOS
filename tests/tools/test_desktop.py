import os
import platform
import pytest
from unittest.mock import patch, MagicMock

from voxos.tools.desktop.apps import CloseAppTool, ListAppsTool, OpenAppArgs, OpenAppTool
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


def test_open_app_accepts_legacy_planner_argument():
    """Fallback plans using app_path_or_name remain executable."""
    args = OpenAppArgs.model_validate({"app_path_or_name": "notepad"})

    assert args.name == "notepad"


@pytest.mark.asyncio
@patch("voxos.tools.desktop.apps.os.startfile", create=True)
@patch("voxos.tools.desktop.apps.subprocess.Popen")
@patch("voxos.tools.desktop.discovery.get_app_discovery")
async def test_open_app_windows(mock_get_discovery, mock_popen, mock_startfile):
    # Only test if running on Windows, or mock platform.system
    mock_get_discovery.return_value.resolve_launch_target.return_value = ("notepad.exe", None)
    with patch("voxos.tools.desktop.apps.platform.system", return_value="Windows"):
        tool = OpenAppTool()
        result = await tool.execute({"name": "notepad"})
        
        assert result.success is True
        mock_startfile.assert_called_once_with("notepad.exe")


@pytest.mark.asyncio
@patch("voxos.tools.desktop.apps.subprocess.Popen")
@patch("voxos.tools.desktop.discovery.get_app_discovery")
async def test_open_app_mac(mock_get_discovery, mock_popen):
    mock_get_discovery.return_value.resolve_launch_target.return_value = ("Calculator", None)
    with patch("voxos.tools.desktop.apps.platform.system", return_value="Darwin"):
        tool = OpenAppTool()
        result = await tool.execute({"name": "calculator"})
        
        assert result.success is True
        mock_popen.assert_called_once_with(["open", "-a", "Calculator"])


@pytest.mark.asyncio
async def test_close_app_success():
    mock_gw = MagicMock()
    mock_window = MagicMock()
    mock_window.title = "Untitled - Notepad"
    mock_gw.getAllWindows.return_value = [mock_window]
    
    with patch.dict('sys.modules', {'pygetwindow': mock_gw}):
        tool = CloseAppTool()
        result = await tool.execute({"window_title": "Notepad"})
        
        assert result.success is True
        mock_window.close.assert_called_once()
        assert "Closed 1 window(s)" in result.data["status"]


@pytest.mark.asyncio
async def test_close_app_not_found():
    mock_gw = MagicMock()
    mock_gw.getAllWindows.return_value = []
    
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
