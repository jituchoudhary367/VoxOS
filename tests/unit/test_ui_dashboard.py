"""The main window builds offscreen, routes transcripts, and manages user data."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402


class _FakeKeyring:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def set_password(self, service, name, value):
        self.store[(service, name)] = value

    def get_password(self, service, name):
        return self.store.get((service, name))

    def delete_password(self, service, name):
        self.store.pop((service, name), None)


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path: Path, monkeypatch):
    import voxos.vault.store as vault_store
    from voxos.vault.store import CredentialVault

    monkeypatch.setattr(vault_store, "_vault", CredentialVault(tmp_path / "vault.json", backend=_FakeKeyring()))
    env = tmp_path / ".env"
    env.write_text("# keep me\nGROQ_API_KEY=gsk_one\nVOICE_WAKE_WORD_ENABLED=false\n", encoding="utf-8")

    from voxos.ui.dashboard import InteractionDashboard

    w = InteractionDashboard(env_path=str(env))
    yield w
    w.close()
    w.deleteLater()


def test_message_roles_follow_transcript_prefixes() -> None:
    from voxos.ui.dashboard import message_role

    assert message_role("You: open notepad") == ("user", "open notepad")
    assert message_role("User: hello") == ("user", "hello")
    assert message_role("Vox: Done.") == ("assistant", "Done.")
    assert message_role("Opened Notepad.") == ("assistant", "Opened Notepad.")
    assert message_role("VoxOS [tool] ran")[0] == "system"


def test_status_words_map_to_states() -> None:
    from voxos.ui.widgets import status_state

    assert status_state("VoxOS: Listening...") == "listening"
    assert status_state("Vox: Thinking...") == "thinking"
    assert status_state("VoxOS: Speaking...") == "speaking"
    assert status_state("Vox: Error") == "error"
    assert status_state("Vox: Idle") == "idle"


def test_window_has_six_tabs_and_switches(window) -> None:
    from voxos.ui.dashboard import PAGES

    assert PAGES == ("chat", "voice", "routines", "usage", "profile", "settings")
    window.show_page("settings")
    assert window.stack.currentWidget() is window.settings_page
    window.switch_mode(1)
    assert window.stack.currentWidget() is window.voice_page
    window.switch_mode(0)
    assert window.stack.currentWidget() is window.chat_page


def test_chat_send_emits_and_adds_a_bubble(window) -> None:
    from voxos.ui.widgets import ChatBubble

    sent: list[str] = []
    window.chat_message_sent.connect(sent.append)
    window.chat_page.input.setText("  open notepad ")
    window.send_chat_message()
    window.append_transcript("Opened Notepad.")
    bubbles = window.chat_page.findChildren(ChatBubble)
    assert sent == ["open notepad"]
    assert len(bubbles) == 2


def test_thinking_status_shows_typing_dots(window) -> None:
    window.show()
    window.update_status("Vox: Thinking...", "#fff")
    assert window.chat_page.typing.isVisible()
    window.update_status("Vox: Idle", "#fff")
    assert not window.chat_page.typing.isVisible()


def test_settings_save_keeps_comments_and_updates_keys(window, tmp_path: Path) -> None:
    page = window.settings_page
    assert [r.field.text() for r in page._key_rows] == ["gsk_one"]
    page._add_key_row("gsk_two")
    page._inputs["VOICE_WAKE_WORD_ENABLED"].setChecked(True)
    page._inputs["DEEPGRAM_API_KEY"].setText("dg_key")
    assert set(page.dirty_fields()) == {"GROQ_API_KEYS", "VOICE_WAKE_WORD_ENABLED", "DEEPGRAM_API_KEY"}
    page.save()

    text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert text.startswith("# keep me\n")
    assert "GROQ_API_KEY=gsk_one" in text
    assert "GROQ_API_KEYS=gsk_two" in text
    assert "VOICE_WAKE_WORD_ENABLED=true" in text
    assert "DEEPGRAM_API_KEY=dg_key" in text
    assert page.dirty_fields() == []


def test_profile_facts_name_and_logins_are_editable(window) -> None:
    from voxos.core.profile.store import get_profile
    from voxos.vault.store import get_vault

    page = window.profile_page
    page.fact_input.setText("Prefers Brave over Chrome")
    page._add_fact()
    page.name_input.setText("Abhijeet")
    page._save_name()
    profile = get_profile()
    assert profile.name == "Abhijeet"
    assert any(f["text"] == "Prefers Brave over Chrome" for f in profile.facts)

    page._forget_fact("Prefers Brave over Chrome")
    assert not any(f["text"] == "Prefers Brave over Chrome" for f in profile.facts)

    # Renaming replaces the old name fact instead of piling up.
    page.name_input.setText("Abhijit")
    page._save_name()
    names = [f["text"] for f in profile.facts if "name is" in f["text"]]
    assert names == ["User's name is Abhijit"]

    get_vault().add("amazon", username="me@example.com", password="pw123456", domains=["amazon.in"])
    page._render_logins()
    assert page.stat_logins.value.text() == "1"
