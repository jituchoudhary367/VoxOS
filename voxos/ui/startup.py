"""
Windows Startup Registration.
"""
import os
import sys
import winreg
from pathlib import Path
from voxos.observability.logger import get_logger

log = get_logger(__name__)

def is_startup_enabled(app_name: str = "VoxOS") -> bool:
    """Checks if the application is registered to run on Windows startup."""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
        value, _ = winreg.QueryValueEx(key, app_name)
        winreg.CloseKey(key)
        return True
    except OSError:
        return False

def enable_startup(app_name: str = "VoxOS", executable_path: str = None) -> bool:
    """Enables running the application on Windows startup."""
    if not executable_path:
        # If frozen by PyInstaller, sys.executable points to the .exe
        if getattr(sys, 'frozen', False):
            executable_path = sys.executable
        else:
            # During development, point to the Python executable running main
            executable_path = f'"{sys.executable}" "{os.path.abspath(sys.argv[0])}"'
            
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, executable_path)
        winreg.CloseKey(key)
        log.info(f"Startup enabled for {app_name} at {executable_path}")
        return True
    except Exception as e:
        log.error("startup_enable_failed", error=str(e))
        return False

def disable_startup(app_name: str = "VoxOS") -> bool:
    """Disables the application from running on Windows startup."""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, app_name)
        winreg.CloseKey(key)
        log.info(f"Startup disabled for {app_name}")
        return True
    except OSError:
        # Key likely doesn't exist, which means it's already disabled
        return True
    except Exception as e:
        log.error("startup_disable_failed", error=str(e))
        return False
