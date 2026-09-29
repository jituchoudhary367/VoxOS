"""
The .env file behind the Settings page.

Edits happen in place: comments, blank lines and the order of keys the user
never touched are kept, a changed key is rewritten on its own line, and a new
key is appended. The old settings window rewrote the whole file from a dict,
which dropped every comment and any key it did not know about.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
_NUMBERED_GROQ = re.compile(r"^GROQ_API_KEY_\d+$")
_NEEDS_QUOTES = re.compile(r"[\s#'\"]")


def _unquote(raw: str) -> str:
    value = raw.strip()
    if value[:1] == "'":
        close = value.find("'", 1)
        if close > 0:
            return value[1:close]
    if value[:1] == '"':
        # Closing quote is the first one not escaped; anything after it is a comment.
        match = re.match(r'"((?:[^"\\]|\\.)*)"', value)
        if match:
            return match.group(1).replace('\\"', '"').replace("\\\\", "\\")
    # An unquoted value ends at an inline comment.
    return re.split(r"\s+#", value, maxsplit=1)[0].strip()


def _quote(value: str) -> str:
    if value and _NEEDS_QUOTES.search(value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


@dataclass
class EnvFile:
    path: Path
    lines: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path = ".env") -> "EnvFile":
        path = Path(path)
        lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
        return cls(path=path, lines=lines)

    # ── reading ──────────────────────────────────────────────────────────────

    def values(self) -> dict[str, str]:
        """Every key in the file; a key set twice keeps its last value, as dotenv does."""
        out: dict[str, str] = {}
        for line in self.lines:
            if line.lstrip().startswith("#"):
                continue
            if match := _LINE.match(line):
                out[match.group(1)] = _unquote(match.group(2))
        return out

    def get(self, key: str, default: str = "") -> str:
        return self.values().get(key, default)

    def groq_keys(self) -> list[str]:
        """GROQ_API_KEY, then GROQ_API_KEYS, then GROQ_API_KEY_<n>, deduplicated."""
        values = self.values()
        numbered = sorted(
            (int(k.rsplit("_", 1)[1]), v) for k, v in values.items() if _NUMBERED_GROQ.match(k)
        )
        candidates = [
            values.get("GROQ_API_KEY", ""),
            *values.get("GROQ_API_KEYS", "").split(","),
            *(v for _, v in numbered),
        ]
        keys: list[str] = []
        for key in (c.strip() for c in candidates):
            if key and key not in keys:
                keys.append(key)
        return keys

    # ── writing ──────────────────────────────────────────────────────────────

    def set(self, key: str, value: str) -> None:
        """Set `key`, in place when it exists (every copy, so the last one agrees)."""
        rendered = f"{key}={_quote(value)}"
        found = False
        for i, line in enumerate(self.lines):
            match = _LINE.match(line)
            if match and match.group(1) == key and not line.lstrip().startswith("#"):
                self.lines[i] = rendered
                found = True
        if not found:
            self.lines.append(rendered)

    def remove(self, key: str) -> None:
        self.lines = [
            line for line in self.lines
            if not ((m := _LINE.match(line)) and m.group(1) == key and not line.lstrip().startswith("#"))
        ]

    def set_groq_keys(self, keys: list[str]) -> None:
        """First key → GROQ_API_KEY, the rest → GROQ_API_KEYS; numbered copies are dropped.

        Numbered keys have to go: config reads them too, so a key the user
        deleted here would otherwise keep being used.
        """
        clean: list[str] = []
        for key in (k.strip() for k in keys):
            if key and key not in clean:
                clean.append(key)
        for name in [k for k in self.values() if _NUMBERED_GROQ.match(k)]:
            self.remove(name)
        self.set("GROQ_API_KEY", clean[0] if clean else "")
        self.set("GROQ_API_KEYS", ",".join(clean[1:]))

    def save(self) -> None:
        """Write atomically so a crash never leaves half a file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text("\n".join(self.lines) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)


def mask(secret: str) -> str:
    """`gsk_…a1b2` — enough to tell keys apart, never enough to use one."""
    secret = secret.strip()
    if not secret:
        return ""
    if len(secret) <= 8:
        return "•" * len(secret)
    return f"{secret[:4]}…{secret[-4:]}"


async def test_groq_key(key: str) -> tuple[str, str]:
    """One tiny request with `key`. Returns (state, detail); state is ok|limited|invalid|error."""
    from openai import AuthenticationError, RateLimitError

    from voxos.config import get_settings
    from voxos.llm.base import LLMConfig, LLMMessage
    from voxos.llm.openai_compat import OpenAICompatibleProvider

    settings = get_settings().llm
    provider = OpenAICompatibleProvider(
        settings.base_url, key.strip(), settings.provider, retry_rate_limits=False
    )
    config = LLMConfig(model=settings.fast_model, max_tokens=8, temperature=0)
    try:
        await provider.complete([LLMMessage(role="user", content="ping")], config)
    except AuthenticationError:
        return "invalid", "Key rejected by Groq"
    except RateLimitError:
        return "limited", "Valid, but rate-limited right now"
    except Exception as e:  # noqa: BLE001
        return "error", f"{type(e).__name__}: {e}"[:140]
    state = provider.rate_limit_state
    left = state.get("remaining_requests")
    return "ok", f"Working · {left} requests left today" if left else "Working"
