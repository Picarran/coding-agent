"""Permission control and policy engine (V1-1).

A deterministic, code-level guard that sits *in front of* tool execution (not a
soft prompt). For every tool call it decides whether the action is
``auto-allowed``, ``requires interactive approval``, or is ``denied``.

The decision pipeline is layered, in this order:

1. **Hard DENY list** — dangerous, irreversible commands (``rm -rf /``,
   ``mkfs``, ``shutdown``, ...) are always denied, in every mode.
2. **Per-mode tool whitelist** — e.g. ``PLAN`` forbids ``patch_file`` and
   ``write_file`` outright.
3. **Rule engine** — user rules with effect ``DENY > ASK > ALLOW``.
4. **Risk fallback** — when no rule matches, a risk score is computed and
   compared against the mode's ``ask_threshold``.

The risk score is a single, explainable formula::

    risk = tool_risk + command_risk + path_risk + side_effect_risk

clamped to 0..5. In one sentence: *the more the action involves writes, risky
commands, sensitive paths, or external side effects, the higher its score.*
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from src.core.models import ToolCall

logger = logging.getLogger(__name__)


class PermissionMode(str, Enum):
    """Global permission posture, from most to least restrictive."""

    PLAN = "plan"
    SAFE = "safe"
    DEFAULT = "default"
    AUTONOMOUS = "autonomous"


class PermissionEffect(str, Enum):
    """What a rule dictates for a matching action."""

    DENY = "deny"
    ASK = "ask"
    ALLOW = "allow"


class Decision(str, Enum):
    """Final outcome of a permission check."""

    AUTO_ALLOW = "auto_allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True)
class PermissionRule:
    """A declarative rule.

    ``tool`` is a tool name or ``"*"``. ``command_pattern`` / ``path_pattern``
    are optional regular expressions matched (with ``re.search``) against the
    tool call's ``command`` / ``path`` argument. A rule matches only when every
    provided constraint matches; leaving one unset makes the rule broader.
    """

    effect: PermissionEffect
    tool: str = "*"
    command_pattern: str | None = None
    path_pattern: str | None = None

    def describe(self) -> str:
        parts = [f"effect={self.effect.value}", f"tool={self.tool}"]
        if self.command_pattern:
            parts.append(f"command~/{self.command_pattern}/")
        if self.path_pattern:
            parts.append(f"path~/{self.path_pattern}/")
        return f"PermissionRule({', '.join(parts)})"


@dataclass
class PermissionDecision:
    """The outcome of a check plus enough detail to audit *why*."""

    decision: Decision
    reason: str
    description: str = ""
    matched_rule: str | None = None
    risk_score: int | None = None


# --------------------------------------------------------------------------- #
# Hard DENY list — irreversible / system-damaging commands, always denied.
# --------------------------------------------------------------------------- #
DANGEROUS_COMMAND_PATTERNS: list[tuple[str, str]] = [
    # `rm -rf /`, `rm -fr ~`, `rm -rf $HOME` (but NOT `rm -rf /tmp`).
    (
        r"\brm\s+-(?:[a-z]*r[a-z]*f[a-z]*|[a-z]*f[a-z]*r[a-z]*)\s+(?:/|~|\$HOME)(?:\s|$)",
        "recursively delete the filesystem root or home directory",
    ),
    (r"\bmkfs\b", "format a filesystem"),
    (r"\bdd\b[^\n]*\bof=/dev/", "write raw bytes to a block device"),
    (r"\bshutdown\b|\breboot\b|\bpoweroff\b|\bhalt\b", "shut down or reboot the machine"),
    (r"\bformat\s+[a-zA-Z]:", "format a drive"),
    (r"\bdel(?:\s+/[fsqap])*\s+[a-zA-Z]:\\", "delete from an entire drive (Windows)"),
    (r"\b(?:rmdir|rd)(?:\s+/[sq])+\s+[a-zA-Z]:\\", "recursively delete a drive (Windows)"),
    (r":\(\)\s*\{[^}]*:\|:&[^}]*\}\s*;\s*:", "fork bomb"),
]


# --------------------------------------------------------------------------- #
# Risk scoring components.
# --------------------------------------------------------------------------- #
_TOOL_RISK: dict[str, int] = {
    "list_files": 0,
    "read_file": 0,
    "search_text": 0,
    "submit_report": 0,
    "patch_file": 1,
    "write_file": 1,
    "execute_command": 1,
}

# weight 2: destructive/irreversible, but not on the hard-DENY list.
_HIGH_RISK_COMMANDS: list[tuple[str, str]] = [
    (r"\brm\b", "delete files"),
    (r"\bdel\b|\berase\b", "delete files (Windows)"),
    (r"\b(?:rmdir|rd)\s+/s\b", "recursively remove a directory (Windows)"),
    (r"\bgit\s+reset\s+--hard\b", "discard uncommitted changes"),
    (r"\bgit\s+push\b[^\n]*--force\b", "force push"),
    (r"\bpip3?\s+uninstall\b", "uninstall a package"),
    (r"\bmv\b[^\n]*\s+(?:/|~)(?:\s|$)", "move into the filesystem root"),
]

# weight 1: has real side effects but is usually recoverable.
_MEDIUM_RISK_COMMANDS: list[tuple[str, str]] = [
    (r"\bgit\s+push\b", "push to a remote"),
    (r"\b(pip3?)\s+install\b", "install a Python package"),
    (r"\bnpm\s+install\b", "install an npm package"),
    (r"\bcurl\b|\bwget\b", "download from the network"),
    (r"\bsudo\b", "elevated privileges"),
    (r"\bchmod\b|\bchown\b", "change permissions/ownership"),
    (r"\bdocker\b", "docker operation"),
    (r"\bapt(?:-get)?\s+install\b", "install a system package"),
    (r"\bscp\b|\brsync\b", "copy to/from a remote host"),
]

# external side effects: network access / global persistence (weight 1).
_SIDE_EFFECT_PATTERN = re.compile(
    r"\bgit\s+(?:push|pull|clone|fetch)\b"
    r"|\b(?:pip3?|npm|apt(?:-get)?|brew|yum)\s+install\b"
    r"|\bcurl\b|\bwget\b"
    r"|\bdocker\s+(?:pull|run|push)\b"
    r"|\bpython\s+-m\s+pip\s+install\b"
)

_SENSITIVE_PATH_PATTERN = re.compile(
    r"(\.env|secret|credential|password|token|id_rsa|\.pem|\.key|\.git/config)",
    re.IGNORECASE,
)


class RiskScorer:
    """Computes ``risk = tool + command + path + side_effect``, clamped to 0..5."""

    def score(self, call: ToolCall) -> int:
        command = self._command_arg(call)
        path = self._path_arg(call)
        total = (
            self._tool_risk(call.name)
            + self._command_risk(command)
            + self._path_risk(path)
            + self._side_effect_risk(command)
        )
        return max(0, min(5, total))

    @staticmethod
    def _command_arg(call: ToolCall) -> str:
        # Risk follows what the call *does*, so any tool carrying a ``command``
        # argument is scored by it (not only the built-in execute_command).
        command = call.arguments.get("command")
        return str(command).strip() if isinstance(command, str) else ""

    @staticmethod
    def _path_arg(call: ToolCall) -> str:
        path = call.arguments.get("path")
        return str(path) if isinstance(path, str) else ""

    @staticmethod
    def _tool_risk(name: str) -> int:
        return _TOOL_RISK.get(name, 2)

    @staticmethod
    def _command_risk(command: str) -> int:
        if not command:
            return 0
        for pattern, _desc in _HIGH_RISK_COMMANDS:
            if re.search(pattern, command):
                return 2
        for pattern, _desc in _MEDIUM_RISK_COMMANDS:
            if re.search(pattern, command):
                return 1
        return 0

    @staticmethod
    def _path_risk(path: str) -> int:
        if not path:
            return 0
        if ".." in path:
            return 2
        if _SENSITIVE_PATH_PATTERN.search(path):
            return 1
        return 0

    @staticmethod
    def _side_effect_risk(command: str) -> int:
        if command and _SIDE_EFFECT_PATTERN.search(command):
            return 1
        return 0


# --------------------------------------------------------------------------- #
# Mode policies.
# --------------------------------------------------------------------------- #
_READ_ONLY_TOOLS = frozenset(
    {"list_files", "read_file", "search_text", "execute_command", "submit_report"}
)


@dataclass(frozen=True)
class ModePolicy:
    mode: PermissionMode
    allowed_tools: frozenset[str] | None  # None = all registered tools allowed
    ask_threshold: int  # risk >= threshold => ASK (when no rule matches)
    description: str


MODE_POLICIES: dict[PermissionMode, ModePolicy] = {
    PermissionMode.PLAN: ModePolicy(
        PermissionMode.PLAN,
        _READ_ONLY_TOOLS,
        2,
        "read-only: patch_file/write_file are denied; risky commands require approval",
    ),
    PermissionMode.SAFE: ModePolicy(
        PermissionMode.SAFE,
        None,
        2,
        "all tools allowed; anything with a non-trivial risk requires approval",
    ),
    PermissionMode.DEFAULT: ModePolicy(
        PermissionMode.DEFAULT,
        None,
        3,
        "human-in-the-loop: every execute_command requires approval; file reads/writes auto-allowed",
    ),
    PermissionMode.AUTONOMOUS: ModePolicy(
        PermissionMode.AUTONOMOUS,
        None,
        6,
        "trust the agent: only the hard DENY list still applies (risk maxes at 5)",
    ),
}

# Well-known commands that should *always* ask, regardless of the risk fallback.
_DEFAULT_ASK_RULES: list[PermissionRule] = [
    PermissionRule(PermissionEffect.ASK, "execute_command", command_pattern=r"\bgit\s+push\b"),
    PermissionRule(PermissionEffect.ASK, "execute_command", command_pattern=r"\b(pip3?)\s+install\b"),
    PermissionRule(PermissionEffect.ASK, "execute_command", command_pattern=r"\bnpm\s+install\b"),
    PermissionRule(PermissionEffect.ASK, "execute_command", command_pattern=r"\brm\b"),
]

# DEFAULT mode asks before *every* command: ``execute_command`` is an arbitrary
# shell, so in the human-in-the-loop mode every command requires approval.
_ALWAYS_ASK_EXECUTE_COMMAND: list[PermissionRule] = [
    PermissionRule(PermissionEffect.ASK, "execute_command"),
]

_MODE_DEFAULT_RULES: dict[PermissionMode, list[PermissionRule]] = {
    PermissionMode.PLAN: _DEFAULT_ASK_RULES,
    PermissionMode.SAFE: _DEFAULT_ASK_RULES,
    PermissionMode.DEFAULT: _ALWAYS_ASK_EXECUTE_COMMAND,
    # AUTONOMOUS has no ASK rules: only the hard DENY list and the (unreachable)
    # risk threshold remain, so nothing prompts for approval.
    PermissionMode.AUTONOMOUS: [],
}

# Approval callback: given a human-readable description, return True to allow.
Approver = Callable[[str], bool]


def default_input_approver() -> Approver:
    """Interactive approver: prompt on stdin, fail-closed on EOF/^C."""

    def _approve(description: str) -> bool:
        try:
            answer = input(f"是否允许执行 {description}? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return answer in ("y", "yes")

    return _approve


# --------------------------------------------------------------------------- #
# The checker.
# --------------------------------------------------------------------------- #
class PermissionChecker:
    """Evaluates a tool call and returns an auditable ``PermissionDecision``."""

    def __init__(
        self,
        mode: PermissionMode | str,
        rules: list[PermissionRule] | None = None,
        scorer: RiskScorer | None = None,
        approver: Approver | None = None,
        dangerous_patterns: list[tuple[str, str]] | None = None,
    ) -> None:
        if isinstance(mode, str):
            mode = PermissionMode(mode)
        self.mode = mode
        self._policy = MODE_POLICIES[mode]
        self._rules = list(
            _MODE_DEFAULT_RULES[mode] if rules is None else rules
        )
        self._scorer = scorer or RiskScorer()
        self.approver = approver
        self._dangerous = dangerous_patterns or DANGEROUS_COMMAND_PATTERNS

    @classmethod
    def from_mode(
        cls,
        mode: PermissionMode | str,
        rules: list[PermissionRule] | None = None,
        approver: Approver | None = None,
    ) -> "PermissionChecker":
        return cls(mode=mode, rules=rules, approver=approver)

    @property
    def policy(self) -> ModePolicy:
        return self._policy

    def check(self, call: ToolCall) -> PermissionDecision:
        command = RiskScorer._command_arg(call)
        path = RiskScorer._path_arg(call)
        description = self._describe(call)

        # 1. Hard DENY: dangerous commands, in every mode.
        if command:
            for pattern, reason in self._dangerous:
                if re.search(pattern, command):
                    return self._log(
                        PermissionDecision(
                            Decision.DENY,
                            f"dangerous command: {reason}",
                            description=description,
                            risk_score=5,
                        )
                    )

        # 2. Per-mode tool whitelist.
        allowed = self._policy.allowed_tools
        if allowed is not None and call.name not in allowed:
            return self._log(
                PermissionDecision(
                    Decision.DENY,
                    f"tool '{call.name}' is not allowed in {self.mode.value} mode",
                    description=description,
                )
            )

        # 3. Rules: DENY > ASK > ALLOW (structural, not order-dependent).
        for effect in (PermissionEffect.DENY, PermissionEffect.ASK, PermissionEffect.ALLOW):
            for rule in self._rules:
                if rule.effect is not effect:
                    continue
                if not self._rule_matches(rule, call.name, command, path):
                    continue
                decision = {
                    PermissionEffect.DENY: Decision.DENY,
                    PermissionEffect.ASK: Decision.ASK,
                    PermissionEffect.ALLOW: Decision.AUTO_ALLOW,
                }[effect]
                return self._log(
                    PermissionDecision(
                        decision,
                        f"matched rule: {rule.describe()}",
                        description=description,
                        matched_rule=rule.describe(),
                    )
                )

        # 4. Risk fallback.
        score = self._scorer.score(call)
        threshold = self._policy.ask_threshold
        if score >= threshold:
            decision = Decision.ASK
            reason = f"risk score {score} >= threshold {threshold}"
        else:
            decision = Decision.AUTO_ALLOW
            reason = f"risk score {score} < threshold {threshold}"
        return self._log(
            PermissionDecision(
                decision, reason, description=description, risk_score=score
            )
        )

    @staticmethod
    def _rule_matches(rule: PermissionRule, tool: str, command: str, path: str) -> bool:
        if rule.tool != "*" and rule.tool != tool:
            return False
        if rule.command_pattern and (not command or not re.search(rule.command_pattern, command)):
            return False
        if rule.path_pattern and (not path or not re.search(rule.path_pattern, path)):
            return False
        return True

    @staticmethod
    def _describe(call: ToolCall) -> str:
        if call.name == "execute_command":
            command = str(call.arguments.get("command", "") or "").strip()
            return f"execute_command: {command}"
        if "path" in call.arguments:
            return f"{call.name}: {call.arguments['path']}"
        return f"{call.name}: {json.dumps(call.arguments, ensure_ascii=False)}"

    @staticmethod
    def _log(decision: PermissionDecision) -> PermissionDecision:
        # Always log the *why* (decision + matched rule / risk score) for audit.
        logger.info(
            "permission decision: %s | %s | rule=%s | risk=%s",
            decision.decision.value,
            decision.reason,
            decision.matched_rule,
            decision.risk_score,
        )
        return decision
