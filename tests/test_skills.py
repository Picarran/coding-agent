"""Tests for skills (V2-8): registry parsing, matching, template execution."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from src.agents.main_agent import MainAgent
from src.agents.main_agent_session import MainAgentSession
from src.core.events import EventBus, EventType
from src.core.models import AgentResult, AgentStatus
from src.skills.registry import (
    Skill,
    SkillMatcher,
    SkillRegistry,
    SkillStep,
    discover_skill_dirs,
)

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def _write_skill(root: Path, name: str, description: str, steps=None) -> None:
    (root / name / "SKILL.md").parent.mkdir(parents=True, exist_ok=True)
    front = yaml.safe_dump(
        {
            "name": name,
            "description": description,
            "keywords": [name],
            "steps": steps or [{"agent": "coding", "description": "do it"}],
        },
        allow_unicode=True,
    )
    (root / name / "SKILL.md").write_text(f"---\n{front}---\nbody\n", encoding="utf-8")


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


class LayeredDirsTest(unittest.TestCase):
    def test_load_dirs_overrides_by_name(self):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            _write_skill(Path(d1), "demo", "v1")
            _write_skill(Path(d2), "demo", "v2")
            registry = SkillRegistry.load_dirs([Path(d1), Path(d2)])
        self.assertEqual(registry.get("demo").description, "v2")  # later dir wins

    def test_discover_skill_dirs_orders_layers(self):
        dirs = discover_skill_dirs(Path("/tmp/ws"))
        self.assertEqual(len(dirs), 3)
        self.assertIn("skills", str(dirs[0]).replace("\\", "/"))      # built-in
        self.assertIn(".coding-agent", str(dirs[1]))                   # project
        self.assertIn(str(Path.home()), str(dirs[2]))                  # personal


class ForcedSkillTest(unittest.TestCase):
    def test_forced_skill_overrides_matching(self):
        registry = SkillRegistry(
            [
                Skill(name="s1", description="s1", keywords=["demo"], steps=[SkillStep("explorer", "read")]),
                Skill(name="s2", description="s2", keywords=["demo"], steps=[SkillStep("coding", "write")]),
            ]
        )

        class Planner:
            def plan(self, task):
                raise AssertionError("planner must not run when a skill is forced")

        class Replanner:
            def replan(self, plan, reason):
                return plan

        class Worker:
            def run(self, task):
                return AgentResult(agent_name="w", status=AgentStatus.SUCCESS, summary="ok")

        agent = MainAgent(
            Planner(), Replanner(), {"explorer": Worker(), "coding": Worker()}, skill_registry=registry
        )
        result = agent.run("a demo task", forced_skill="s2")
        self.assertEqual(result.artifacts["plan"][0]["id"], "s2-1")

    def test_use_command_forces_next_task_then_resets(self):
        registry = SkillRegistry(
            [Skill(name="s", description="d", keywords=[], steps=[SkillStep("coding", "x")])]
        )
        calls: list = []

        class Agent:
            def run(self, task, forced_skill=None):
                calls.append(forced_skill)
                return AgentResult(agent_name="a", status=AgentStatus.SUCCESS, summary="ok")

        session = MainAgentSession(Agent(), skill_registry=registry)
        self.assertIn("s", session.handle_command("/use s"))
        session.send("do it")
        session.send("do another")
        self.assertEqual(calls, ["s", None])  # forced once, then reset


class StepsOptionalTest(unittest.TestCase):
    def test_skill_without_steps_is_valid(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "guide" / "SKILL.md"
            p.parent.mkdir(parents=True)
            p.write_text(
                "---\nname: guide\ndescription: guidance only\nkeywords: [guide]\n---\n"
                "Do X then Y.\n",
                encoding="utf-8",
            )
            registry = SkillRegistry.load(Path(d))
        skill = registry.get("guide")
        self.assertIsNotNone(skill)
        self.assertEqual(skill.steps, [])
        self.assertIn("Do X then Y", skill.guidance())


class _StubLLM:
    def chat(self, messages, tools=None):
        raise AssertionError("LLM must not be called during these tests")


class SingleAgentSkillTest(unittest.TestCase):
    def test_single_agent_injects_skill_guidance(self):
        from src.agents.base_agent import BaseAgent
        from src.agents.registries import build_coding_registry

        registry = SkillRegistry(
            [Skill(name="s", description="d", keywords=["demo"], steps=[], body="Follow these rules.")]
        )
        with tempfile.TemporaryDirectory() as d:
            agent = BaseAgent(
                "single", _StubLLM(), build_coding_registry(Path(d)), "sys", {},
                skill_registry=registry,
            )
            task = agent._inject_skill_guidance("a demo task")
        self.assertIn("Follow these rules.", task)
        self.assertIn("Skill (s) guidance", task)

    def test_guidance_only_skill_runs_planner_and_injects(self):
        registry = SkillRegistry(
            [Skill(name="g", description="g", keywords=["demo"], steps=[], body="Follow rule A.")]
        )
        planner_calls: list[str] = []

        class Planner:
            def plan(self, task):
                planner_calls.append(task)
                from src.planning.task_plan import PlanStep, TaskPlan
                return TaskPlan(goal=task, steps=[PlanStep(id="s1", description="read x", assigned_agent="explorer")])

        class Replanner:
            def replan(self, plan, reason):
                return plan

        class Worker:
            def __init__(self):
                self.tasks: list[str] = []

            def run(self, task):
                self.tasks.append(task)
                return AgentResult(agent_name="w", status=AgentStatus.SUCCESS, summary="ok")

        worker = Worker()
        agent = MainAgent(Planner(), Replanner(), {"explorer": worker}, skill_registry=registry)
        result = agent.run("a demo task")
        self.assertEqual(result.status, AgentStatus.SUCCESS)
        self.assertEqual(len(planner_calls), 1)  # planner ran (no deterministic steps)
        self.assertIn("Follow rule A.", worker.tasks[0])  # guidance injected into subtask

    def test_single_agent_forced_skill_overrides(self):
        from src.agents.base_agent import BaseAgent
        from src.agents.registries import build_coding_registry

        registry = SkillRegistry(
            [
                Skill(name="s1", description="d", keywords=["demo"], steps=[], body="rule one"),
                Skill(name="s2", description="d", keywords=["demo"], steps=[], body="rule two"),
            ]
        )
        with tempfile.TemporaryDirectory() as d:
            agent = BaseAgent(
                "single", _StubLLM(), build_coding_registry(Path(d)), "sys", {},
                skill_registry=registry,
            )
            task = agent._inject_skill_guidance("a demo task", forced_skill="s2")
        self.assertIn("rule two", task)
        self.assertNotIn("rule one", task)


if __name__ == "__main__":
    unittest.main()
