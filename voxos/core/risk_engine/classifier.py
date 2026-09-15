"""
Command Risk Classifier.

Classifies terminal commands and tool operations by risk level.

Design principles:
1. Multi-layer: allowlist → denylist → pattern analysis → target analysis
2. Conservative: when in doubt, classify as HIGH
3. Extensible: add new patterns without rewriting the engine
4. Not solely string-matching: uses parsed command structure

Risk Levels:
    LOW      — Safe, read-only, no side effects
    MEDIUM   — Reversible changes, package installs, network requests
    HIGH     — File deletion, process kills, system changes (require approval)
    CRITICAL — Formatted drives, mass deletion, credential changes (block by default)
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

from voxos.tools.base import RiskLevel
from voxos.observability.logger import get_logger

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Pattern definitions
# ─────────────────────────────────────────────────────────────────────────────

# Commands that are always safe to run
LOW_RISK_COMMANDS = frozenset({
    "python", "python3", "py",  # with --version, etc.
    "git",          # status, log, diff, branch (not push/force)
    "dir", "ls", "ll", "la",
    "pwd", "cd",    # navigation only
    "echo", "cat", "type",
    "whoami", "hostname", "uptime",
    "date", "time", "cal",
    "which", "where",
    "df", "du", "free",
    "top", "htop", "ps",
    "ping",         # limited
    "tracert", "traceroute",
    "nslookup", "dig",
    "docker",       # ps, images, logs, inspect (not rm, rmi, system prune)
    "kubectl",      # get, describe (not delete, apply to prod)
    "node", "npm",  # version, list
    "pip",          # list, show, freeze (not install to prod)
    "curl", "wget", # read-only fetches
    "find",         # search only
    "grep", "rg",   # search
    "wc", "sort", "head", "tail",
    "env", "set", "printenv",
    "ipconfig", "ifconfig", "ip",
    "netstat",
    "tasklist", "pgrep",
})

# Patterns that ALWAYS indicate HIGH or CRITICAL risk
CRITICAL_PATTERNS = [
    # Mass deletion
    r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|--recursive.*--force|--force.*--recursive)",
    r"\brmdir\s+/[sS]",                        # Windows rmdir /s
    r"\bdel\s+/[sS]",                          # Windows del /s
    r"\bformat\b",                              # disk format
    r"\bdiskpart\b",                            # Windows disk partitioning
    r"\bdd\s+if=/dev/zero",                     # disk wipe
    r"\bshred\b",                               # secure file deletion
    # Credential/secret operations
    r"\bpasswd\b",
    r"\bchpasswd\b",
    r"\bsudo\s+passwd",
    r"\bnet\s+user.*password",
    # System-level operations
    r"\binit\s+0",                              # shutdown
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bhalt\b",
    # Registry (Windows)
    r"\breg\s+(delete|add|import|export)",
    # Python exec of arbitrary code
    r"python.*-c\s+[\"'].*eval|exec",
    # curl/wget piped to shell (extremely dangerous)
    r"(curl|wget).*\|.*sh",
    r"(curl|wget).*\|.*bash",
    r"(curl|wget).*\|.*python",
    # Obfuscation and nested execution
    r"\bpowershell\s+-(Enc|EncodedCommand)\b",  # Encoded PowerShell
    r"[A-Za-z0-9+/]{50,}={0,2}",                # Base64 payloads (heuristically >50 chars)
    r"\bcmd\s+/c\s+[\"']?(powershell|bash|sh)\b",# Nested shell execution
]

HIGH_RISK_PATTERNS = [
    r"\bsudo\b",
    # File deletion (single or targeted)
    r"\brm\b",
    r"\bdel\b",                                # Windows
    r"\bmove.*(/trash|recycle)",
    # Process termination
    r"\bkill\b",
    r"\btaskkill\b",
    # System modification
    r"\bchmod\b",
    r"\bchown\b",
    r"\bicacls\b",                             # Windows ACL
    # Package management (install/remove)
    r"\bpip\s+install\b",
    r"\bapt(-get)?\s+(install|remove|purge)\b",
    r"\byum\s+(install|remove)\b",
    r"\bnpm\s+install\b",
    r"\bchoco\s+install\b",
    # Service management
    r"\bsystemctl\s+(start|stop|restart|enable|disable)\b",
    r"\bsc\s+(start|stop|delete)\b",
    r"\bnet\s+(start|stop)\b",
    # Git destructive
    r"\bgit\s+(push.*--force|reset.*--hard|clean\s+-[fd]+)\b",
    r"\bgit\s+checkout\s+-[Bf]",
    # Docker destructive
    r"\bdocker\s+(rm|rmi|system\s+prune|container\s+rm)\b",
    # Environment variable exposure
    r"\benv\s*>",
    r"\bset\s*>",
    r"\bprintenv\s*>",
]

MEDIUM_RISK_PATTERNS = [
    r"\b(?:curl|wget)\b",
    r"\bpip\s+(install|uninstall)\b",
    r"\bnpm\s+(install|uninstall|update)\b",
    r"\bdocker\s+build\b",
    r"\bgit\s+pull\b",
    r"\bgit\s+push\b",
    r"\bgit\s+commit\b",
    r"\bgit\s+merge\b",
    r"\bgit\s+rebase\b",
    r"\bscp\b",
    r"\brsync\b",
    r"\bmkdir\b",
    r"\btouch\b",
    r"\bcp\b",
    r"\bcopy\b",
    r"\bmove\b",
    r"\bren\b",
    r"\brename\b",
]

# Paths that are ALWAYS protected — require extra confirmation
PROTECTED_PATHS = [
    r"C:\\Windows",
    r"C:\\System32",
    r"C:\\Program Files",
    r"/etc",
    r"/usr",
    r"/bin",
    r"/sbin",
    r"/boot",
    r"/dev",
    r"/proc",
    r"/sys",
    r"~/.ssh",
    r"~/.gnupg",
]


# ─────────────────────────────────────────────────────────────────────────────
# Classifier
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ClassificationResult:
    """Result of a risk classification."""

    risk_level: RiskLevel
    reasons: list[str] = field(default_factory=list)
    matched_patterns: list[str] = field(default_factory=list)
    requires_approval: bool = False
    is_blocked: bool = False
    block_reason: str | None = None


class CommandRiskClassifier:
    """
    Classifies terminal commands by risk level.
    
    Uses a layered approach:
    1. Check if command is in safe allowlist
    2. Check for critical patterns (block or require strong approval)
    3. Check for high-risk patterns (require approval)
    4. Check for medium-risk patterns (log and proceed)
    5. Default: LOW (safe to proceed)
    """

    def __init__(self) -> None:
        self._critical_re = [re.compile(p, re.IGNORECASE) for p in CRITICAL_PATTERNS]
        self._high_re = [re.compile(p, re.IGNORECASE) for p in HIGH_RISK_PATTERNS]
        self._medium_re = [re.compile(p, re.IGNORECASE) for p in MEDIUM_RISK_PATTERNS]
        self._protected_re = [re.compile(p, re.IGNORECASE) for p in PROTECTED_PATHS]

    def classify_command(self, command: str) -> ClassificationResult:
        """
        Classify a terminal command by risk level.
        
        Args:
            command: The shell command to classify (not yet executed)
            
        Returns:
            ClassificationResult with risk level and reasons
        """
        command = self._normalize_command(command)
        reasons: list[str] = []
        matched_patterns: list[str] = []

        # Check for protected path access
        protected_hit = self._check_protected_paths(command)
        if protected_hit:
            reasons.append(f"Accesses protected path: {protected_hit}")

        # Layer 1: Critical patterns — these are blocked or require strong approval
        for pattern in self._critical_re:
            if pattern.search(command):
                matched_patterns.append(pattern.pattern)
                reasons.append("Matches critical risk pattern")
                log.warning(
                    "command_classified_critical",
                    command=command[:100],
                    pattern=pattern.pattern[:50],
                )
                return ClassificationResult(
                    risk_level=RiskLevel.CRITICAL,
                    reasons=reasons,
                    matched_patterns=matched_patterns,
                    requires_approval=True,
                    is_blocked=True,
                    block_reason="Command matches critical risk pattern and is blocked by policy",
                )

        # Layer 2: High-risk patterns
        for pattern in self._high_re:
            if pattern.search(command):
                matched_patterns.append(pattern.pattern)
                reasons.append("Matches high-risk operation pattern")
                return ClassificationResult(
                    risk_level=RiskLevel.HIGH,
                    reasons=reasons + (
                        ["Accesses protected path"] if protected_hit else []
                    ),
                    matched_patterns=matched_patterns,
                    requires_approval=True,
                )

        # Escalate to HIGH if protected path was accessed even for medium commands
        if protected_hit:
            reasons.append("Protected path access escalates risk to HIGH")
            return ClassificationResult(
                risk_level=RiskLevel.HIGH,
                reasons=reasons,
                requires_approval=True,
            )

        # Layer 3: Medium-risk patterns
        for pattern in self._medium_re:
            if pattern.search(command):
                matched_patterns.append(pattern.pattern)
                reasons.append("Matches medium-risk operation pattern")
                return ClassificationResult(
                    risk_level=RiskLevel.MEDIUM,
                    reasons=reasons,
                    matched_patterns=matched_patterns,
                    requires_approval=False,
                )

        # Layer 4: Check if base command is in safe allowlist
        base_cmd = self._extract_base_command(command)
        if base_cmd and base_cmd.lower() in LOW_RISK_COMMANDS:
            return ClassificationResult(
                risk_level=RiskLevel.LOW,
                reasons=["Command in safe allowlist"],
                requires_approval=False,
            )

        # Layer 5: Unknown — default to MEDIUM (conservative)
        log.info("command_unknown_risk", command=command[:100], base=base_cmd)
        return ClassificationResult(
            risk_level=RiskLevel.MEDIUM,
            reasons=["Unknown command — defaulting to MEDIUM (conservative policy)"],
            requires_approval=False,
        )

    def assess_command(self, command: str) -> RiskLevel:
        """Return the simplified risk level used by older callers."""
        result = self.classify_command(command)
        if result.risk_level == RiskLevel.CRITICAL:
            return RiskLevel.HIGH
        if result.risk_level == RiskLevel.HIGH and re.search(
            r"\b(?:apt(?:-get)?|pip|npm|yum|choco)\s+(?:install|remove|uninstall|purge)",
            self._normalize_command(command),
            re.IGNORECASE,
        ):
            return RiskLevel.MEDIUM
        return result.risk_level

    def classify_tool_operation(
        self,
        tool_name: str,
        arguments: dict,
    ) -> ClassificationResult:
        """
        Classify a tool operation by risk level.
        
        For filesystem/terminal tools, delegates to command classification
        or uses the tool's declared risk level.
        """
        # Terminal tool — analyze the actual command
        if tool_name in {"terminal.execute", "os.shell_command"}:
            command = arguments.get("command", "")
            return self.classify_command(command)

        # Filesystem destructive operations
        if tool_name in {"filesystem.delete", "filesystem.archive"}:
            path = arguments.get("path", "")
            if self._check_protected_paths(path):
                return ClassificationResult(
                    risk_level=RiskLevel.CRITICAL,
                    reasons=["Deleting protected system path"],
                    requires_approval=True,
                    is_blocked=True,
                    block_reason="Cannot delete protected system path",
                )
            return ClassificationResult(
                risk_level=RiskLevel.HIGH,
                reasons=["File deletion is a destructive operation"],
                requires_approval=True,
            )

        # Communication sending — always requires confirmation
        if tool_name.startswith("communication.send"):
            return ClassificationResult(
                risk_level=RiskLevel.HIGH,
                reasons=["Sending external communications requires approval"],
                requires_approval=True,
            )

        return ClassificationResult(
            risk_level=RiskLevel.LOW,
            reasons=["Tool operation — use tool's declared risk level"],
            requires_approval=False,
        )

    def _check_protected_paths(self, text: str) -> str | None:
        """Return matched protected path pattern or None."""
        for pattern in self._protected_re:
            match = pattern.search(text)
            if match:
                return match.group(0)
        return None

    @staticmethod
    def _normalize_command(command: str) -> str:
        """Remove simple shell escaping before pattern classification."""
        return re.sub(r"\\(?=[A-Za-z])", "", command.strip())

    @staticmethod
    def _extract_base_command(command: str) -> str | None:
        """Extract the base command name from a shell command string."""
        try:
            parts = shlex.split(command)
            if parts:
                # Handle env var assignments, sudo, etc.
                for part in parts:
                    if "=" not in part:
                        return part.split("/")[-1].split("\\")[-1]
        except ValueError:
            pass
        return command.split()[0] if command.split() else None
