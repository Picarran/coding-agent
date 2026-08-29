"""Tests for skills (V2-8): registry parsing, matching, template execution."""
from __future__ import annotations

import unittest
from pathlib import Path

from src.agents.main_agent import MainAgent
from src.core.events import EventBus, EventType
from src.core.models import AgentResult, AgentStatus
from src.skills.registry import Skill, SkillMatcher, SkillRegistry, SkillStep

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


class RegistryTest(unittest.TestCase):
    def test_loads_skills_from_directory(self):
        reg = SkillRegistry.load(SKILLS_DIR)
        self.assertGreaterEqual(len(reg.all()), 4)
        for skill in reg.all():
            self.assertTrue(skill.name)
            self.assertTrue(skill.description)
            self.assertTrue(skill.steps)
        self.assertIsNotNone(reg.get("fix-tests"))

    def test_catalog_is_progressive_disclosure(self):
        reg = SkillRegistry.load(SKILLS_DIR)
        catalog = reg.catalog()
        self.assertIn("fix-tests", catalog)
        self.assertIn("code-review", catalog)
        # The catalog carries name+description only, not the body or steps.
        self.assertNotIn("reproduce", catalog)
        self.assertNotIn("Allowed tools", catalog)


class MatcherTest(unittest.TestCase):
    def setUp(self):
        self.matcher = SkillMatcher(SkillRegistry.load(SKILLS_DIR))

    def test_matches_fix_tests(self):
        self.assertEqual(self.matcher.match("fix the failing test").name, "fix-tests")

    def test_matches_code_review(self):
        self.assertEqual(self.matcher.match("review this change").name, "code-review")

    def test_matches_implement_feature(self):
        self.assertEqual(self.matcher.match("implement a new feature").name, "implement-feature")

    def test_matches_refactor(self):
        self.assertEqual(self.matcher.match("refactor this module").name, "refactor")

    def test_no_match(self):
        self.assertIsNone(self.matcher.match("list the files in this repo"))


class _RecordingConsumer:
    def __init__(self):
        self.events = []

    def on_event(self, event):
        self.events.append(event)


class MainAgentSkillTest(unittest.TestCase):
    def test_skill_match_uses_template_instead_of_planner(self):
        registry = SkillRegistry(
            [
                Skill(
                    name="demo",
                    description="demo skill",
                    keywords=["demo task"],
                    steps=[SkillStep(agent="explorer", description="read the code")],
                )
            ]
        )
        planner_calls: list[str] = []

        class Planner:
            def plan(self, task):
                planner_calls.append(task)
                raise AssertionError("planner must not run when a skill matches")

        class Replanner:
            def replan(self, plan, reason):
                return plan

        class Worker:
            def run(self, task):
                return AgentResult(agent_name="w", status=AgentStatus.SUCCESS, summary="ok")

        bus = EventBus()
        consumer = _RecordingConsumer()
        bus.subscribe(consumer)

        agent = MainAgent(
            Planner(), Replanner(), {"explorer": Worker()}, event_bus=bus, skill_registry=registry
        )
        result = agent.run("this is a demo task")

        self.assertEqual(result.status, AgentStatus.SUCCESS)
        self.assertEqual(planner_calls, [])  # deterministic template, no LLM planning
        self.assertEqual(result.artifacts["plan"][0]["id"], "demo-1")
        kinds = [e.event_type for e in consumer.events]
        self.assertIn(EventType.SKILL_MATCHED, kinds)

    def test_no_skill_match_falls_back_to_planner(self):
        registry = SkillRegistry([Skill(name="demo", description="d", keywords=["zzz"], steps=[SkillStep("explorer", "x")])])
        calls = []

        class Planner:
            def plan(self, task):
                calls.append(task)
                from src.planning.task_plan import PlanStep, TaskPlan
                return TaskPlan(goal=task, steps=[PlanStep(id="s1", description="explore", assigned_agent="explorer")])

        class Replanner:
            def replan(self, plan, reason):
                return plan

        class Worker:
            def run(self, task):
                return AgentResult(agent_name="w", status=AgentStatus.SUCCESS, summary="ok")

        agent = MainAgent(Planner(), Replanner(), {"explorer": Worker()}, skill_registry=registry)
        result = agent.run("do something unrelated")
        self.assertEqual(result.status, AgentStatus.SUCCESS)
        self.assertEqual(len(calls), 1)  # planner ran


if __name__ == "__main__":
    unittest.main()
