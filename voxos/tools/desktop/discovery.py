"""
Windows Application Discovery Engine.

Discovers installed applications via:
1. Windows App Paths registry (HKLM/HKCU)
2. Start Menu shortcut scanning (.lnk files)
3. PATH executable lookup (shutil.which)
4. Known aliases (normalized common names)

Results are cached at first use and refreshed on demand.
"""

from __future__ import annotations

import os
import re
import shutil
import time
import winreg
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from voxos.observability.logger import get_logger

log = get_logger(__name__)


@dataclass
class AppEntry:
    """A discovered application."""
    name: str                        # Normalized display name
    executable: str                  # Full path to .exe or launch token
    source: str                      # "registry" | "startmenu" | "path" | "alias"
    confidence: float = 1.0


# ---------------------------------------------------------------------------
# Hardcoded alias table — high confidence, covers the most common apps
# ---------------------------------------------------------------------------

ALIASES: dict[str, str] = {
    # Browsers
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "chromium": "chromium.exe",
    "firefox": "firefox.exe",
    "mozilla firefox": "firefox.exe",
    "edge": "msedge.exe",
    "microsoft edge": "msedge.exe",
    # Editors / IDEs
    "notepad": "notepad.exe",
    "notepad++": "notepad++.exe",
    "vs code": "code.exe",
    "vscode": "code.exe",
    "visual studio code": "code.exe",
    "code": "code.exe",
    "sublime": "sublime_text.exe",
    "sublime text": "sublime_text.exe",
    "atom": "atom.exe",
    "pycharm": "pycharm64.exe",
    "intellij": "idea64.exe",
    "webstorm": "webstorm64.exe",
    # Office
    "word": "winword.exe",
    "microsoft word": "winword.exe",
    "excel": "excel.exe",
    "microsoft excel": "excel.exe",
    "powerpoint": "powerpnt.exe",
    "microsoft powerpoint": "powerpnt.exe",
    "outlook": "outlook.exe",
    "onenote": "onenote.exe",
    "teams": "teams.exe",
    "microsoft teams": "teams.exe",
    # Windows built-ins
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "paint": "mspaint.exe",
    "mspaint": "mspaint.exe",
    "wordpad": "wordpad.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "task manager": "taskmgr.exe",
    "taskmgr": "taskmgr.exe",
    "control panel": "control.exe",
    "settings": "ms-settings:",
    "windows settings": "ms-settings:",
    "snipping tool": "snippingtool.exe",
    "clock": "ms-clock:",
    "calendar": "outlookcal:",
    "mail": "outlookmail:",
    "photos": "ms-photos:",
    "store": "ms-windows-store:",
    # Terminals
    "terminal": "wt.exe",
    "windows terminal": "wt.exe",
    "cmd": "cmd.exe",
    "command prompt": "cmd.exe",
    "powershell": "powershell.exe",
    "pwsh": "pwsh.exe",
    # Dev tools
    "git bash": "git-bash.exe",
    "docker": "docker.exe",
    "postman": "postman.exe",
    # Media
    "vlc": "vlc.exe",
    "spotify": "spotify.exe",
    "discord": "discord.exe",
    "zoom": "zoom.exe",
    "obs": "obs64.exe",
    "obs studio": "obs64.exe",
    # Utilities
    "7zip": "7zfm.exe",
    "7-zip": "7zfm.exe",
    "winrar": "winrar.exe",
    "notepad plus plus": "notepad++.exe",
}

# Registry keys to scan for App Paths
_APP_PATH_KEYS = [
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
    (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
]

# Start Menu root paths
_START_MENU_PATHS = [
    Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
    Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
]

_CACHE_TTL_SECONDS = 300   # 5-minute cache


class AppDiscovery:
    """Singleton that discovers and caches installed applications."""

    def __init__(self) -> None:
        self._cache: dict[str, AppEntry] = {}
        self._cache_built_at: float = 0.0

    def _ensure_cache(self) -> None:
        now = time.monotonic()
        if now - self._cache_built_at < _CACHE_TTL_SECONDS and self._cache:
            return
        log.info("app_discovery_building_cache")
        cache: dict[str, AppEntry] = {}

        # 1. Registry App Paths
        for hive, key_path in _APP_PATH_KEYS:
            try:
                with winreg.OpenKey(hive, key_path) as key:
                    i = 0
                    while True:
                        try:
                            subkey_name = winreg.EnumKey(key, i)
                            i += 1
                            try:
                                with winreg.OpenKey(key, subkey_name) as subkey:
                                    exe_path, _ = winreg.QueryValueEx(subkey, "")
                                    normalized = _normalize(subkey_name)
                                    if normalized not in cache:
                                        cache[normalized] = AppEntry(
                                            name=subkey_name,
                                            executable=exe_path.strip('"'),
                                            source="registry",
                                        )
                            except OSError:
                                pass
                        except OSError:
                            break
            except OSError:
                pass

        # 2. Start Menu shortcuts
        for sm_path in _START_MENU_PATHS:
            if sm_path.exists():
                for lnk in sm_path.rglob("*.lnk"):
                    name = lnk.stem
                    normalized = _normalize(name)
                    if normalized not in cache:
                        cache[normalized] = AppEntry(
                            name=name,
                            executable=str(lnk),  # Will be resolved via shell
                            source="startmenu",
                            confidence=0.9,
                        )

        # 3. Static aliases (highest confidence, override if present)
        for alias_name, exe_token in ALIASES.items():
            normalized = _normalize(alias_name)
            cache[normalized] = AppEntry(
                name=alias_name,
                executable=exe_token,
                source="alias",
                confidence=1.0,
            )

        self._cache = cache
        self._cache_built_at = time.monotonic()
        log.info("app_discovery_cache_built", count=len(cache))

    def find(self, user_phrase: str) -> Optional[AppEntry]:
        """
        Resolve a user phrase to an installed application.

        Returns None if nothing found with sufficient confidence.
        """
        self._ensure_cache()
        normalized = _normalize(user_phrase)

        # Exact match
        if normalized in self._cache:
            return self._cache[normalized]

        # Prefix match
        for key, entry in self._cache.items():
            if key.startswith(normalized) or normalized.startswith(key):
                return entry

        # Fuzzy: check if all words in query appear in the key
        query_words = set(normalized.split())
        for key, entry in self._cache.items():
            key_words = set(key.split())
            if query_words and query_words.issubset(key_words):
                return entry

        # PATH lookup as last resort
        on_path = shutil.which(user_phrase) or shutil.which(normalized)
        if on_path:
            return AppEntry(
                name=user_phrase,
                executable=on_path,
                source="path",
                confidence=0.8,
            )

        return None

    def resolve_launch_target(self, user_phrase: str) -> tuple[str | None, str | None]:
        """
        Returns (launch_target, error_message).
        launch_target is a string suitable for os.startfile() or subprocess.
        """
        entry = self.find(user_phrase)
        if entry is None:
            return None, f"Could not find '{user_phrase}' on this computer."
        return entry.executable, None

    def refresh_cache(self) -> None:
        """Force a cache refresh."""
        self._cache_built_at = 0.0
        self._ensure_cache()


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


# Module-level singleton
_discovery: AppDiscovery | None = None


def get_app_discovery() -> AppDiscovery:
    global _discovery
    if _discovery is None:
        _discovery = AppDiscovery()
    return _discovery
