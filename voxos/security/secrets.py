"""
Secret redaction for VoxOS.

Prevents API keys, passwords, tokens, and other credentials
from appearing in logs, LLM prompts, or API responses.

CRITICAL: These functions must be applied at every boundary
where untrusted or sensitive data could leak.
"""

from __future__ import annotations

import re
from typing import Any

# Patterns that likely indicate sensitive data
SENSITIVE_KEY_PATTERNS = [
    re.compile(r"(api[_-]?key|apikey)", re.IGNORECASE),
    re.compile(r"(secret|password|passwd|pwd)", re.IGNORECASE),
    re.compile(r"(token|auth|authorization|bearer)", re.IGNORECASE),
    re.compile(r"(private[_-]?key|ssh[_-]?key|rsa)", re.IGNORECASE),
    re.compile(r"(credential|credential)", re.IGNORECASE),
    re.compile(r"(cookie|session[_-]?id)", re.IGNORECASE),
    re.compile(r"(access[_-]?token|refresh[_-]?token)", re.IGNORECASE),
]

# Value patterns for known credential formats
CREDENTIAL_VALUE_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9]{32,}", re.IGNORECASE),          # OpenAI API keys
    re.compile(r"gsk_[a-zA-Z0-9]{32,}", re.IGNORECASE),         # Groq API keys
    re.compile(r"ghp_[a-zA-Z0-9]{36}", re.IGNORECASE),          # GitHub personal tokens
    re.compile(r"gho_[a-zA-Z0-9]{36}", re.IGNORECASE),          # GitHub OAuth tokens
    re.compile(r"[A-Za-z0-9+/]{40,}={0,2}"),                    # Base64-encoded secrets (40+ chars)
    re.compile(r"-----BEGIN [A-Z ]+-----"),                       # PEM headers
    re.compile(r"eyJ[A-Za-z0-9._-]{20,}"),                      # JWT tokens
]

REDACTED = "***REDACTED***"


def redact_dict(data: dict[str, Any], depth: int = 0) -> dict[str, Any]:
    """
    Recursively redact sensitive keys from a dictionary.
    
    Args:
        data: Dictionary to redact
        depth: Current recursion depth (capped at 5 to avoid infinite loops)
    
    Returns:
        New dictionary with sensitive values replaced by REDACTED
    """
    if depth > 5:
        return data

    result = {}
    for key, value in data.items():
        if _is_sensitive_key(str(key)):
            result[key] = REDACTED
        elif isinstance(value, dict):
            result[key] = redact_dict(value, depth + 1)
        elif isinstance(value, list):
            result[key] = [
                redact_dict(item, depth + 1) if isinstance(item, dict) else item
                for item in value
            ]
        elif isinstance(value, str):
            result[key] = redact_value(value)
        else:
            result[key] = value
    return result


def redact_value(value: str) -> str:
    """Redact known credential patterns from a string value."""
    for pattern in CREDENTIAL_VALUE_PATTERNS:
        value = pattern.sub(REDACTED, value)
    return value


def redact_env_output(text: str) -> str:
    """
    Redact sensitive values from env/set command output.
    
    Example:
        OPENAI_API_KEY=sk-abc123 → OPENAI_API_KEY=***REDACTED***
    """
    lines = text.split("\n")
    result = []
    for line in lines:
        if "=" in line:
            key, _, value = line.partition("=")
            if _is_sensitive_key(key.strip()):
                result.append(f"{key}={REDACTED}")
            else:
                result.append(redact_value(line))
        else:
            result.append(line)
    return "\n".join(result)


def _is_sensitive_key(key: str) -> bool:
    return any(p.search(key) for p in SENSITIVE_KEY_PATTERNS)
