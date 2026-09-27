"""Saved logins Vox can use without ever seeing them.

The agent only knows a credential's name and the sites it belongs to. A
tool fills the field in the browser from Windows Credential Manager, so the
secret never enters a prompt, a tool result, a log or the database. Two
rules are enforced in code: the page must be on one of the credential's
domains, and the user's own request must ask to log in.
"""

from __future__ import annotations

from typing import Any

import pytest

from voxos.vault.store import CredentialVault


class MemoryKeyring:
    """Stands in for Windows Credential Manager."""

    def __init__(self) -> None:
        self.data: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, key: str, value: str) -> None:
        self.data[(service, key)] = value

    def get_password(self, service: str, key: str) -> str | None:
        return self.data.get((service, key))

    def delete_password(self, service: str, key: str) -> None:
        self.data.pop((service, key), None)


@pytest.fixture
def vault(tmp_path) -> CredentialVault:
    v = CredentialVault(tmp_path / "vault.json", backend=MemoryKeyring())
    v.add("amazon", username="abhi@example.com", password="S3cret-pw!", domains=["amazon.in"])
    return v


# ─── Store ──────────────────────────────────────────────────────────────────

def test_secrets_live_in_the_keyring_not_in_the_index(vault):
    index = vault.index_path.read_text(encoding="utf-8")

    assert "S3cret-pw!" not in index
    assert "abhi@example.com" not in index
    assert vault.secret("amazon", "password") == "S3cret-pw!"
    assert vault.secret("amazon", "username") == "abhi@example.com"


def test_listing_shows_names_and_sites_only(vault):
    listing = vault.list()

    assert listing == [{"name": "amazon", "domains": ["amazon.in"]}]
    assert "S3cret" not in str(listing) and "abhi" not in str(listing)


def test_remove_deletes_from_keyring_and_index(vault):
    vault.remove("amazon")

    assert vault.list() == []
    assert vault.secret("amazon", "password") is None


@pytest.mark.parametrize(("url", "allowed"), [
    ("https://www.amazon.in/ap/signin", True),
    ("https://amazon.in/", True),
    ("https://amazon.in.evil.com/login", False),
    ("https://evil.com/amazon.in", False),
    ("https://notamazon.in/", False),
    ("http://www.amazon.in/", False),   # never over plain http
    ("about:blank", False),
])
def test_domain_lock(vault, url, allowed):
    assert vault.allowed_on("amazon", url) is allowed


# ─── Tool ───────────────────────────────────────────────────────────────────

class FakeLocator:
    def __init__(self, page: "FakePage", selector: str) -> None:
        self.page, self.selector = page, selector

    @property
    def first(self) -> "FakeLocator":
        return self

    async def count(self) -> int:
        return 1 if self.selector in self.page.fields else 0

    async def is_visible(self) -> bool:
        return True

    async def fill(self, value: str) -> None:
        self.page.filled[self.selector] = value


class FakePage:
    def __init__(self, url: str) -> None:
        self.url = url
        self.fields = {"input[type=password]", "input[type=email]"}
        self.filled: dict[str, str] = {}

    def is_closed(self) -> bool:
        return False

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)


@pytest.fixture
def login_page(monkeypatch, vault):
    from voxos.tools.browser import tools as browser
    from voxos.vault import tool as vault_tool

    page = FakePage("https://www.amazon.in/ap/signin")

    async def session(navigate=False):
        return page

    monkeypatch.setattr(browser, "_get_browser_session", session)
    monkeypatch.setattr(vault_tool, "get_vault", lambda: vault)
    return page


async def _fill(field: str, request: str, credential: str = "amazon"):
    from voxos.core.request_context import user_request
    from voxos.vault.tool import FillCredentialArgs, FillCredentialTool

    with user_request(request):
        return await FillCredentialTool()._execute(FillCredentialArgs(credential=credential, field=field))


async def test_fills_the_password_without_returning_it(login_page):
    result = await _fill("password", "log in to amazon")

    assert result.success
    assert login_page.filled == {"input[type=password]": "S3cret-pw!"}
    assert "S3cret" not in str(result.data) and "S3cret" not in str(result.error)


async def test_fills_the_username(login_page):
    result = await _fill("username", "sign in to my amazon account")

    assert result.success
    assert login_page.filled == {"input[type=email]": "abhi@example.com"}


@pytest.mark.parametrize("request_text", [
    "search amazon for hp laptops",          # no login asked
    "what is on this page",
    "",
])
async def test_refuses_when_the_user_did_not_ask_to_log_in(login_page, request_text):
    result = await _fill("password", request_text)

    assert not result.success
    assert result.error_code == "LOGIN_NOT_REQUESTED"
    assert login_page.filled == {}


async def test_refuses_on_another_site(login_page):
    login_page.url = "https://amazon-login.example.net/signin"

    result = await _fill("password", "log in to amazon")

    assert result.error_code == "WRONG_SITE"
    assert login_page.filled == {}


async def test_unknown_credential(login_page):
    result = await _fill("password", "log in to netflix", credential="netflix")

    assert result.error_code == "NOT_FOUND"


# ─── What the agent sees ────────────────────────────────────────────────────

def test_worker_prompt_lists_credentials_but_never_values(monkeypatch, vault):
    from voxos.orchestration import worker as worker_module

    monkeypatch.setattr(worker_module, "get_vault", lambda: vault)
    text = worker_module.credentials_prompt()

    assert "amazon" in text and "amazon.in" in text
    assert "S3cret" not in text and "abhi@" not in text


def test_secrets_are_redacted_before_reaching_the_model(monkeypatch, vault):
    from voxos.orchestration import worker as worker_module
    from voxos.vault.store import redact

    monkeypatch.setattr("voxos.vault.store.get_vault", lambda: vault)
    rendered = worker_module.Worker._render([{
        "tool": "browser.extract", "arguments": {}, "status": "SUCCESS",
        "data": {"text": "Welcome abhi@example.com, your password S3cret-pw! was reset"},
    }])

    assert "S3cret-pw!" not in rendered
    assert "abhi@example.com" not in rendered
    assert redact("x S3cret-pw! y") == "x ••• y"


def test_fill_credential_is_a_permitted_tool():
    from voxos.core.permissions.manager import get_permission_manager
    from voxos.tools.registry import get_tool_registry

    assert get_tool_registry().get("browser.fill_credential") is not None
    assert get_permission_manager().allows_tool("browser.fill_credential")


# ─── CLI: secrets typed privately, never as arguments ───────────────────────

def test_cli_reads_the_password_with_getpass(monkeypatch, tmp_path):
    from voxos.vault import __main__ as cli

    store = CredentialVault(tmp_path / "vault.json", backend=MemoryKeyring())
    monkeypatch.setattr(cli, "get_vault", lambda: store)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "typed-privately")
    monkeypatch.setattr("builtins.input", lambda prompt="": "abhi@example.com")

    assert cli.main(["add", "amazon", "--domain", "amazon.in"]) == 0
    assert store.secret("amazon", "password") == "typed-privately"


def test_log_lines_mask_saved_secret_values(monkeypatch, vault):
    import voxos.vault.store as store
    from voxos.observability.logger import _redact_secrets

    monkeypatch.setattr(store, "_vault", vault)
    vault.load_for_redaction()

    event = _redact_secrets(None, "info", {"event": "page_text", "text": "hello abhi@example.com S3cret-pw!"})

    assert "S3cret-pw!" not in event["text"] and "abhi@example.com" not in event["text"]


def test_spoken_reply_never_contains_a_secret(monkeypatch, vault):
    from voxos.orchestration.synthesizer import speakable

    monkeypatch.setattr("voxos.vault.store.get_vault", lambda: vault)

    assert "S3cret-pw!" not in speakable("Your password is S3cret-pw! now.")


async def test_the_user_request_reaches_tools_during_a_run(monkeypatch):
    """The TaskManager publishes the user's own words for the login gate."""
    from types import SimpleNamespace

    from voxos.core.request_context import current_user_request
    from voxos.core.task_manager.manager import TaskManager
    from voxos.core.task_manager.models import Task

    seen = {}
    manager = TaskManager()

    async def fake_pipeline(task, *args):
        seen["request"] = current_user_request()
        return task

    async def no_sync(task):
        return None

    monkeypatch.setattr(manager, "_execute_pipeline", fake_pipeline)
    monkeypatch.setattr(manager, "_sync_task_to_db", no_sync)
    await manager.run_pipeline(Task(session_id="s", user_request="log in to amazon"), None, None, None, None)

    assert seen["request"] == "log in to amazon"
    assert current_user_request() == ""  # cleared after the run
