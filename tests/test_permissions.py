"""Tests for the permission control and policy engine (V1-1)."""
from __future__ import annotations

import unittest

from src.core.models import AgentStatus, ToolCall
from src.llm.base import LLMClient, LLMResponse
from src.loops.react_loop import ReactLoop
from src.safety.permissions import (
    Decision,
    PermissionChecker,
    PermissionEffect,
    PermissionMode,
    PermissionRule,
    RiskScorer,
)
from src.tools.definitions import ToolDefinition
from src.tools.executor import ToolExecutor
from src.tools.registry import ToolRegistry


def _call(name: str, **args) -> ToolCall:
    return ToolCall(id="t1", name=name, arguments=args)


class RiskScorerTest(unittest.TestCase):
    def test_read_only_tool_scores_zero(self):
        scorer = RiskScorer()
        self.assertEqual(scorer.score(_call("read_file", path="a.py")), 0)

    def test_write_tool_scores_one(self):
        scorer = RiskScorer()
        self.assertEqual(scorer.score(_call("patch_file", path="a.py")), 1)

    def test_safe_command_scores_shell_base_risk(self):
        scorer = RiskScorer()
        # The arbitrary shell itself carries base risk 3; a benign command adds 0.
        self.assertEqual(
            scorer.score(_call("execute_command", command="python -m unittest")), 3
        )

    def test_rm_command_scores_high(self):
        scorer = RiskScorer()
        # shell base(3) + high-risk command(2) = 5
        self.assertEqual(
            scorer.score(_call("execute_command", command="rm -rf build/")), 5
        )

    def test_windows_del_command_scores_high(self):
        scorer = RiskScorer()
        # shell base(3) + high-risk `del`(2) = 5
        self.assertEqual(
            scorer.score(_call("execute_command", command="del calculator.py")), 5
        )

    def test_side_effect_adds_score(self):
        scorer = RiskScorer()
        # shell base(3) + medium git push(1) + side effect(1) = 5
        self.assertEqual(
            scorer.score(_call("execute_command", command="git push origin main")), 5
        )

    def test_sensitive_path_adds_score(self):
        scorer = RiskScorer()
        # write(1) + sensitive path(1) = 2
        self.assertEqual(scorer.score(_call("write_file", path=".env", content="x")), 2)

    def test_risk_is_clamped_to_five(self):
        scorer = RiskScorer()
        # unknown tool(2) + path traversal(2) + command(2) + side effect(1) -> clamp 5
        self.assertEqual(
            scorer.score(_call("unknown_tool", path="../x", command="rm x")), 5
        )


class PermissionCheckerTest(unittest.TestCase):
    def test_plan_mode_denies_write_tools(self):
        checker = PermissionChecker.from_mode(PermissionMode.PLAN)
        self.assertEqual(checker.check(_call("patch_file", path="a.py")).decision, Decision.DENY)
        self.assertEqual(checker.check(_call("write_file", path="a.py")).decision, Decision.DENY)

    def test_plan_mode_allows_read_tools(self):
        checker = PermissionChecker.from_mode(PermissionMode.PLAN)
        self.assertEqual(
            checker.check(_call("read_file", path="a.py")).decision, Decision.AUTO_ALLOW
        )

    def test_dangerous_command_denied_in_every_mode(self):
        for mode in PermissionMode:
            checker = PermissionChecker.from_mode(mode)
            decision = checker.check(_call("execute_command", command="rm -rf /"))
            self.assertEqual(decision.decision, Decision.DENY, mode)

    def test_dangerous_command_denied_even_autonomous(self):
        checker = PermissionChecker.from_mode(PermissionMode.AUTONOMOUS)
        decision = checker.check(_call("execute_command", command="shutdown now"))
        self.assertEqual(decision.decision, Decision.DENY)

    def test_rm_build_is_ask_in_default_not_deny(self):
        checker = PermissionChecker.from_mode(PermissionMode.DEFAULT)
        decision = checker.check(_call("execute_command", command="rm -rf build/"))
        self.assertEqual(decision.decision, Decision.ASK)

    def test_windows_del_file_prompts_in_default(self):
        # Regression: `del` (Windows delete) must prompt just like `rm`.
        checker = PermissionChecker.from_mode(PermissionMode.DEFAULT)
        decision = checker.check(_call("execute_command", command="del calculator.py"))
        self.assertEqual(decision.decision, Decision.ASK)

    def test_windows_erase_file_prompts(self):
        checker = PermissionChecker.from_mode(PermissionMode.DEFAULT)
        decision = checker.check(_call("execute_command", command="erase x.txt"))
        self.assertEqual(decision.decision, Decision.ASK)

    def test_windows_recursive_rmdir_prompts(self):
        checker = PermissionChecker.from_mode(PermissionMode.DEFAULT)
        decision = checker.check(_call("execute_command", command="rmdir /s /q build"))
        self.assertEqual(decision.decision, Decision.ASK)

    def test_windows_drive_root_delete_is_denied(self):
        checker = PermissionChecker.from_mode(PermissionMode.DEFAULT)
        self.assertEqual(
            checker.check(_call("execute_command", command="rd /s /q C:\\")).decision,
            Decision.DENY,
        )
        self.assertEqual(
            checker.check(_call("execute_command", command="del /f /s /q C:\\")).decision,
            Decision.DENY,
        )

    def test_deny_rule_wins_over_allow_rule(self):
        checker = PermissionChecker.from_mode(
            PermissionMode.AUTONOMOUS,
            rules=[
                PermissionRule(PermissionEffect.ALLOW, "write_file"),
                PermissionRule(PermissionEffect.DENY, "write_file", path_pattern=r"\.env$"),
            ],
        )
        decision = checker.check(_call("write_file", path=".env", content="x"))
        self.assertEqual(decision.decision, Decision.DENY)

    def test_ask_rule_wins_over_allow_rule(self):
        checker = PermissionChecker.from_mode(
            PermissionMode.AUTONOMOUS,
            rules=[
                PermissionRule(PermissionEffect.ALLOW, "execute_command"),
                PermissionRule(
                    PermissionEffect.ASK, "execute_command", command_pattern=r"\bgit\s+push\b"
                ),
            ],
        )
        decision = checker.check(_call("execute_command", command="git push origin main"))
        self.assertEqual(decision.decision, Decision.ASK)

    def test_allow_rule_overrides_risk(self):
        # In DEFAULT, `rm` normally scores 3 (>= threshold 3 => ASK); an ALLOW
        # rule matching it must downgrade to auto-allow.
        checker = PermissionChecker.from_mode(
            PermissionMode.DEFAULT,
            rules=[PermissionRule(PermissionEffect.ALLOW, "execute_command", command_pattern=r"\brm\b")],
        )
        decision = checker.check(_call("execute_command", command="rm build/"))
        self.assertEqual(decision.decision, Decision.AUTO_ALLOW)

    def test_risk_threshold_boundary(self):
        # SAFE threshold is 2: risk 1 < 2 => auto-allow, risk 2 >= 2 => ask.
        checker = PermissionChecker.from_mode(PermissionMode.SAFE)
        low = checker.check(_call("patch_file", path="a.py"))  # risk 1
        self.assertEqual(low.decision, Decision.AUTO_ALLOW)
        high = checker.check(_call("write_file", path=".env", content="x"))  # risk 2
        self.assertEqual(high.decision, Decision.ASK)

    def test_autonomous_never_asks(self):
        checker = PermissionChecker.from_mode(PermissionMode.AUTONOMOUS)
        decision = checker.check(_call("execute_command", command="git push origin main"))
        self.assertEqual(decision.decision, Decision.AUTO_ALLOW)

    def test_every_mode_except_autonomous_asks_on_commands(self):
        # Consistent ordering: PLAN < SAFE < DEFAULT all ask on *any* command;
        # AUTONOMOUS does not (its threshold 6 sits above the risk cap of 5).
        for mode in (PermissionMode.PLAN, PermissionMode.SAFE, PermissionMode.DEFAULT):
            checker = PermissionChecker.from_mode(mode)
            decision = checker.check(_call("execute_command", command="echo hello"))
            self.assertEqual(decision.decision, Decision.ASK, mode)
        auto = PermissionChecker.from_mode(PermissionMode.AUTONOMOUS)
        self.assertEqual(
            auto.check(_call("execute_command", command="echo hello")).decision,
            Decision.AUTO_ALLOW,
        )

    def test_default_mode_still_allows_file_writes(self):
        checker = PermissionChecker.from_mode(PermissionMode.DEFAULT)
        decision = checker.check(_call("patch_file", path="a.py"))
        self.assertEqual(decision.decision, Decision.AUTO_ALLOW)

    def test_decision_records_audit_reason(self):
        # `curl` reaches the risk fallback (there are no default rules now).
        checker = PermissionChecker.from_mode(PermissionMode.SAFE)
        decision = checker.check(_call("execute_command", command="curl https://example.com"))
        self.assertIsNotNone(decision.risk_score)
        self.assertEqual(decision.decision, Decision.ASK)
        self.assertTrue(decision.reason)
        self.assertTrue(decision.description)

    def test_rule_decision_records_matched_rule(self):
        # Custom rules (DENY/ASK/ALLOW) still take precedence over risk.
        checker = PermissionChecker.from_mode(
            PermissionMode.AUTONOMOUS,
            rules=[
                PermissionRule(
                    PermissionEffect.ASK, "execute_command", command_pattern=r"\bgit\s+push\b"
                )
            ],
        )
        decision = checker.check(_call("execute_command", command="git push origin main"))
        self.assertIsNotNone(decision.matched_rule)
        self.assertIn("git", decision.matched_rule)


class ExecutorIntegrationTest(unittest.TestCase):
    @staticmethod
    def _registry():
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="probe",
                description="probe",
                parameters={"type": "object", "properties": {"command": {"type": "string"}}},
                func=lambda command=None: "ran",
            )
        )
        return registry

    def test_denied_call_becomes_error_result(self):
        checker = PermissionChecker.from_mode(PermissionMode.PLAN)
        # PLAN whitelist excludes 'probe', so it is denied.
        executor = ToolExecutor(self._registry(), permission_checker=checker)
        result = executor.execute(_call("probe", command="x"))
        self.assertIsNotNone(result.error)
        self.assertIn("Permission denied", result.error)
        self.assertTrue(result.permission_denied)

    def test_auto_allowed_result_is_not_denied(self):
        checker = PermissionChecker.from_mode(PermissionMode.AUTONOMOUS)
        executor = ToolExecutor(self._registry(), permission_checker=checker)
        result = executor.execute(_call("probe", command="git push origin main"))
        self.assertFalse(result.permission_denied)

    def test_ask_fails_closed_without_approver(self):
        checker = PermissionChecker.from_mode(PermissionMode.DEFAULT)
        executor = ToolExecutor(self._registry(), permission_checker=checker)
        result = executor.execute(_call("probe", command="git push origin main"))
        self.assertIn("fail-closed", result.error)

    def test_approver_granted_then_runs(self):
        granted = []
        checker = PermissionChecker.from_mode(
            PermissionMode.DEFAULT, approver=lambda d: (granted.append(d) or True)
        )
        executor = ToolExecutor(self._registry(), permission_checker=checker)
        result = executor.execute(_call("probe", command="git push origin main"))
        self.assertIsNone(result.error)
        self.assertEqual(result.content, "ran")
        self.assertTrue(granted)

    def test_approver_denied_blocks_execution(self):
        checker = PermissionChecker.from_mode(
            PermissionMode.DEFAULT, approver=lambda d: False
        )
        executor = ToolExecutor(self._registry(), permission_checker=checker)
        result = executor.execute(_call("probe", command="git push origin main"))
        self.assertIn("denied by user", result.error)

    def test_auto_allowed_runs_without_approver(self):
        checker = PermissionChecker.from_mode(PermissionMode.AUTONOMOUS)
        executor = ToolExecutor(self._registry(), permission_checker=checker)
        result = executor.execute(_call("probe", command="git push origin main"))
        self.assertEqual(result.content, "ran")


class ReactLoopInterruptTest(unittest.TestCase):
    """A permission denial must interrupt the loop, not become an observation."""

    class _AlwaysProbe(LLMClient):
        def chat(self, messages, tools=None):
            return LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="x", name="probe", arguments={}, arguments_json="{}")],
                finish_reason="tool_calls",
            )

    @staticmethod
    def _probe_executor(checker):
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="probe",
                description="probe",
                parameters={"type": "object", "properties": {}},
                func=lambda: "ran",
            )
        )
        return ToolExecutor(registry, permission_checker=checker)

    def test_loop_stops_with_blocked_on_denial(self):
        # PLAN whitelist denies 'probe' => deterministic DENY.
        checker = PermissionChecker.from_mode(PermissionMode.PLAN)
        loop = ReactLoop(
            self._AlwaysProbe(),
            self._probe_executor(checker),
            system_prompt="sys",
            max_steps=10,
        )
        result = loop.run("task")
        self.assertEqual(result.status, AgentStatus.BLOCKED)
        self.assertEqual(result.artifacts["final_state"], "BLOCKED")
        self.assertEqual(result.artifacts["stop_reason"], "permission_denied")
        self.assertIn("blocked_reason", result.artifacts)

    def test_loop_does_not_continue_after_denial(self):
        checker = PermissionChecker.from_mode(PermissionMode.PLAN)
        loop = ReactLoop(
            self._AlwaysProbe(),
            self._probe_executor(checker),
            system_prompt="sys",
            max_steps=10,
        )
        result = loop.run("task")
        self.assertEqual(result.status, AgentStatus.BLOCKED)
        # Only one step ran: the denial interrupted the loop before the model
        # could propose a workaround.
        self.assertEqual(result.artifacts["steps"], 1)


if __name__ == "__main__":
    unittest.main()
