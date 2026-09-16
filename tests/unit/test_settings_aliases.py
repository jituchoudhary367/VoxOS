"""Tests for the VOX_* settings aliases.

The project renamed its agent to Vox, but existing .env files still use the
older JARVIS_* names. Both must work, with VOX_* winning when both are set.
"""

from __future__ import annotations

import pytest

from voxos.config import SecuritySettings, Settings, WorkspaceSettings


def test_vox_secret_key_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOX_SECRET_KEY", "from-vox")

    assert SecuritySettings().secret_key == "from-vox"


def test_legacy_jarvis_secret_key_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOX_SECRET_KEY", raising=False)
    monkeypatch.setenv("JARVIS_SECRET_KEY", "from-jarvis")

    assert SecuritySettings().secret_key == "from-jarvis"


def test_vox_wins_when_both_are_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_SECRET_KEY", "from-jarvis")
    monkeypatch.setenv("VOX_SECRET_KEY", "from-vox")

    assert SecuritySettings().secret_key == "from-vox"


def test_vox_port_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOX_PORT", "9123")

    assert Settings().port == 9123


def test_legacy_jarvis_port_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOX_PORT", raising=False)
    monkeypatch.setenv("JARVIS_PORT", "9124")

    assert Settings().port == 9124


def test_vox_workspace_root_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOX_WORKSPACE_ROOT", "~/VoxWork")

    assert WorkspaceSettings().workspace_root == "~/VoxWork"


def test_legacy_jarvis_workspace_root_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOX_WORKSPACE_ROOT", raising=False)
    monkeypatch.setenv("JARVIS_WORKSPACE_ROOT", "~/LegacyWork")

    assert WorkspaceSettings().workspace_root == "~/LegacyWork"


def test_default_workspace_is_not_named_after_the_old_agent() -> None:
    assert "jarvis" not in WorkspaceSettings().workspace_root.lower()
