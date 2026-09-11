"""
Risk Policies — configurable policy rules for risk classification.

Policies define which tool categories, operations, and patterns
are LOW / MEDIUM / HIGH / CRITICAL by default.
Administrators can override policies per-deployment.
"""

from __future__ import annotations

from typing import Any

from voxos.tools.base import RiskLevel


# ─────────────────────────────────────────────────────────────────────────────
# Tool category baseline risk levels
# ─────────────────────────────────────────────────────────────────────────────

TOOL_BASELINE_RISK: dict[str, RiskLevel] = {
    # Safe read-only
    "system.get_info": RiskLevel.LOW,
    "system.get_time": RiskLevel.LOW,
    "system.get_resources": RiskLevel.LOW,
    "os.list_dir": RiskLevel.LOW,
    "os.read_file": RiskLevel.LOW,
    "desktop.screenshot": RiskLevel.LOW,
    "desktop.list_apps": RiskLevel.LOW,
    "web.search": RiskLevel.LOW,
    "document.read": RiskLevel.LOW,
    "document.summarize": RiskLevel.LOW,
    "memory.search": RiskLevel.LOW,

    # Moderate — write side-effects
    "os.write_file": RiskLevel.MEDIUM,
    "browser.open": RiskLevel.MEDIUM,
    "browser.navigate": RiskLevel.MEDIUM,
    "browser.click": RiskLevel.MEDIUM,
    "browser.type": RiskLevel.MEDIUM,
    "browser.screenshot": RiskLevel.LOW,
    "desktop.open_app": RiskLevel.MEDIUM,
    "desktop.close_app": RiskLevel.MEDIUM,

    # High — destructive or privileged
    "filesystem.delete": RiskLevel.HIGH,
    "os.shell_command": RiskLevel.HIGH,
    "terminal.kill_process": RiskLevel.HIGH,
    "application.close": RiskLevel.MEDIUM,
    "system.shutdown": RiskLevel.CRITICAL,
    "system.restart": RiskLevel.CRITICAL,
}


# ─────────────────────────────────────────────────────────────────────────────
# Destructive command patterns (shell / powershell)
# ─────────────────────────────────────────────────────────────────────────────

CRITICAL_SHELL_PATTERNS: list[str] = [
    "rm -rf /",
    "format c:",
    "del /f /s /q c:\\",
    "rd /s /q c:\\",
    ":(){:|:&};:",  # Fork bomb
    "dd if=",
    "mkfs.",
    "shutdown",
    "poweroff",
    "halt",
]

HIGH_SHELL_PATTERNS: list[str] = [
    "rm -rf",
    "del /f",
    "rd /s",
    "rmdir /s",
    "sudo",
    "chmod 777",
    "chown",
    "net user",
    "net localgroup administrators",
    "reg delete",
    "reg add",
    "sc delete",
    "sc stop",
    "taskkill /f",
    "wmic",
    "curl | sh",
    "wget -O- | sh",
    "Invoke-Expression",
    "iex(",
    "powershell -enc",
    "base64",
    "certutil -decode",
]


def get_tool_risk(tool_name: str) -> RiskLevel:
    """Return the policy-defined baseline risk for a tool."""
    return TOOL_BASELINE_RISK.get(tool_name, RiskLevel.MEDIUM)


def classify_shell_command(command: str) -> RiskLevel:
    """
    Classify a shell command string by its risk level.
    
    This is a fast static check — the CommandRiskClassifier does deeper analysis.
    """
    cmd_lower = command.lower().strip()

    for pattern in CRITICAL_SHELL_PATTERNS:
        if pattern in cmd_lower:
            return RiskLevel.CRITICAL

    for pattern in HIGH_SHELL_PATTERNS:
        if pattern in cmd_lower:
            return RiskLevel.HIGH

    # Piping suggests chaining commands — elevate to MEDIUM
    if "|" in command or "&&" in command or ";" in command:
        return RiskLevel.MEDIUM

    return RiskLevel.LOW


class RiskPolicy:
    """
    Encapsulates configurable risk policy rules.

    Can be extended to load custom policies from a config file or DB.
    """

    def __init__(self) -> None:
        self._tool_overrides: dict[str, RiskLevel] = {}

    def override_tool_risk(self, tool_name: str, risk: RiskLevel) -> None:
        """Administrators can override baseline risk for specific tools."""
        self._tool_overrides[tool_name] = risk

    def get_effective_risk(self, tool_name: str, args: dict[str, Any]) -> RiskLevel:
        """
        Return the effective risk level for a tool call.

        Checks overrides first, then policy baseline, then arg-based escalation.
        """
        if tool_name in self._tool_overrides:
            return self._tool_overrides[tool_name]

        base = get_tool_risk(tool_name)

        # Escalate based on arguments
        if tool_name in {"terminal.execute", "os.shell_command"}:
            cmd = args.get("command", "")
            cmd_risk = classify_shell_command(cmd)
            # Take the higher
            order = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]
            if order.index(cmd_risk) > order.index(base):
                return cmd_risk

        return base


_risk_policy: RiskPolicy | None = None


def get_risk_policy() -> RiskPolicy:
    global _risk_policy
    if _risk_policy is None:
        _risk_policy = RiskPolicy()
    return _risk_policy
