"""
Credential vault: usernames and passwords in Windows Credential Manager.

Only the credential names and their allowed domains are kept in a local
index file; the secrets themselves are in the OS keyring (encrypted with
the user's Windows login). Nothing here ever returns a secret to an agent:
secret() is for the fill tool alone, and redact() masks any secret that
reaches text headed for a model, a log or the user.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from voxos.core.profile.store import data_dir
from voxos.observability.logger import get_logger

log = get_logger(__name__)

SERVICE = "VoxOS-vault"
Field = Literal["username", "password"]
_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,40}$")
_MASK = "•••"


class CredentialVault:
    def __init__(self, index_path: Path, backend: Any | None = None) -> None:
        if backend is None:
            import keyring as backend  # Windows Credential Manager on Windows
        self.index_path = Path(index_path)
        self._backend = backend
        self._index: dict[str, dict[str, Any]] = {}
        if self.index_path.exists():
            try:
                self._index = json.loads(self.index_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                log.warning("vault_index_unreadable", error=str(e))
        self._seen: set[str] = set()  # known secret values, for redact()
        self._loaded = False

    # ── management (CLI only) ────────────────────────────────────────────────

    def add(self, name: str, *, username: str, password: str, domains: list[str]) -> None:
        name = name.strip().lower()
        if not _NAME.match(name):
            raise ValueError("Name: lowercase letters, digits, '-', '_' or '.', up to 40 characters.")
        clean = sorted({d.strip().lower().removeprefix("www.").strip(".") for d in domains if d.strip()})
        if not clean:
            raise ValueError("At least one domain (e.g. amazon.in) is required.")
        if not password:
            raise ValueError("Password is empty.")
        self._backend.set_password(SERVICE, f"{name}:username", username)
        self._backend.set_password(SERVICE, f"{name}:password", password)
        self._seen.update(v for v in (username, password) if v)
        self._index[name] = {"domains": clean, "added": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        self._save()
        log.info("vault_credential_added", name=name, domains=clean)

    def remove(self, name: str) -> bool:
        name = name.strip().lower()
        for field in ("username", "password"):
            try:
                self._backend.delete_password(SERVICE, f"{name}:{field}")
            except Exception:  # noqa: BLE001 — already gone
                pass
        existed = self._index.pop(name, None) is not None
        self._save()
        return existed

    def _save(self) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._index, indent=2), encoding="utf-8")
        os.replace(tmp, self.index_path)

    # ── what agents may know ─────────────────────────────────────────────────

    def list(self) -> list[dict[str, Any]]:
        return [{"name": n, "domains": list(meta["domains"])} for n, meta in sorted(self._index.items())]

    def allowed_on(self, name: str, url: str) -> bool:
        """True when `url` is https on one of the credential's domains (or a subdomain)."""
        meta = self._index.get(name.strip().lower())
        parsed = urlparse(url or "")
        host = (parsed.hostname or "").lower()
        if meta is None or parsed.scheme != "https" or not host:
            return False
        return any(host == d or host.endswith("." + d) for d in meta["domains"])

    # ── for the fill tool only ───────────────────────────────────────────────

    def secret(self, name: str, field: Field) -> str | None:
        name = name.strip().lower()
        if name not in self._index:
            return None
        value = self._backend.get_password(SERVICE, f"{name}:{field}")
        if value:
            self._seen.add(value)
        return value

    def redact(self, text: str) -> str:
        if not self._loaded:
            self.load_for_redaction()
        for value in sorted(self._seen, key=len, reverse=True):
            if len(value) >= 4 and value in text:
                text = text.replace(value, _MASK)
        return text

    def load_for_redaction(self) -> None:
        """Know every stored secret, so redact() works before first use."""
        self._loaded = True
        for name in self._index:
            for field in ("username", "password"):
                self.secret(name, field)  # type: ignore[arg-type]


_vault: CredentialVault | None = None


def get_vault() -> CredentialVault:
    global _vault
    if _vault is None:
        _vault = CredentialVault(data_dir() / "vault.json")
        try:
            _vault.load_for_redaction()
        except Exception as e:  # noqa: BLE001 — keyring unavailable: nothing to redact
            log.warning("vault_keyring_unavailable", error=str(e))
    return _vault


def redact(text: str) -> str:
    """Mask stored secrets in any text (model prompts, replies, logs)."""
    try:
        return get_vault().redact(text)
    except Exception:  # noqa: BLE001
        return text
