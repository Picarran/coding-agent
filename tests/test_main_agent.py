"""Tests for the Main Agent's Plan-and-Execute loop (fake planner/replanner/agents)."""
from __future__ import annotations

import time
import unittest

from src.agents.main_agent import MainAgent, _answer_language
from src.core.events import EventBus, EventType
from src.core.models import AgentResult, AgentStatus
from src.llm.base import LLMResponse
from src.planning.delegation import DelegationPolicy
from src.planning.task_plan import PlanStep, TaskPlan


class FakePlanner:
    def __init__(self, plan):
        self._plan = plan

    def plan(self, task):
        return self._plan


class FakeReplanner:
    def __init__(self, plan):
        self._plan = plan

    def replan(self, plan, reason):
        return self._plan


class FakeWorker:
    def __init__(self, results):
        self._results = list(results)
        self.tasks = []

    def run(self, task):
        self.tasks.append(task)
        return self._results.pop(0)


def _success(summary="ok"):
    return AgentResult(agent_name="w", status=AgentStatus.SUCCESS, summary=summary)


def _failure(summary="boom"):
    return AgentResult(agent_name="w", status=AgentStatus.FAILED, summary=summary)


def _blocked(summary="denied"):
    return AgentResult(agent_name="w", status=AgentStatus.BLOCKED, summary=summary)


class _TextLLM:
    def __init__(self, content):
        self._content = content

    def chat(self, messages, tools=None):
        return LLMResponse(content=self._content, tool_calls=None, finish_reason="stop")


class _RecordingLLM:
    def __init__(self, content):
        self._content = content
        self.messages = []

    def chat(self, messages, tools=None):
        self.messages = list(messages)
        return LLMResponse(content=self._content, tool_calls=None, finish_reason="stop")


class MainAgentTest(unittest.TestCase):
    def test_executes_all_steps_in_order(self):
        plan = TaskPlan(
            goal="g",
            steps=[
                PlanStep(id="s1", description="step 1"),
                PlanStep(id="s2", description="step 2", dependencies=["s1"]),
            ],
        )
        worker = FakeWorker([_success("did s1"), _success("did s2")])
        agent = MainAgent(FakePlanner(plan), FakeReplanner(plan), {"coding": worker})
        result = agent.run("g")

        self.assertEqual(result.status, AgentStatus.SUCCESS)
        self.assertEqual(result.artifacts["final_state"], "COMPLETED")
        self.assertEqual(len(worker.tasks), 2)

    def test_dispatches_to_assigned_agent(self):
        plan = TaskPlan(
            goal="g",
            steps=[PlanStep(id="s1", description="investigate", assigned_agent="explorer")],
        )
        explorer = FakeWorker([_success("found it")])
        coding = FakeWorker([_success("unused")])
        agent = MainAgent(
            FakePlanner(plan),
            FakeReplanner(plan),
            {"explorer": explorer, "coding": coding},
        )
        result = agent.run("g")

        self.assertEqual(result.status, AgentStatus.SUCCESS)
        self.assertEqual(len(explorer.tasks), 1)
        self.assertEqual(len(coding.tasks), 0)

    def test_replans_on_failure(self):
        plan = TaskPlan(goal="g", steps=[PlanStep(id="s1", description="step 1")])
        retry_plan = TaskPlan(goal="g", steps=[PlanStep(id="s1-retry", description="retry")])
        worker = FakeWorker([_failure("boom"), _success("fixed")])
        agent = MainAgent(
            FakePlanner(plan), FakeReplanner(retry_plan), {"coding": worker}, max_replans=1
        )
        result = agent.run("g")

        self.assertEqual(result.status, AgentStatus.SUCCESS)
        self.assertEqual(result.artifacts["replans"], 1)
        self.assertEqual(len(worker.tasks), 2)

    def test_stops_on_blocked_worker_without_replan(self):
        plan = TaskPlan(goal="g", steps=[PlanStep(id="s1", description="step 1")])
        worker = FakeWorker([_blocked("user rejected")])
        agent = MainAgent(
            FakePlanner(plan), FakeReplanner(plan), {"coding": worker}, max_replans=5
        )
        result = agent.run("g")

        self.assertEqual(result.status, AgentStatus.BLOCKED)
        self.assertEqual(result.artifacts["final_state"], "BLOCKED")
        self.assertEqual(len(worker.tasks), 1)  # no replan, no further dispatch
        self.assertEqual(result.artifacts["replans"], 0)

    def test_fails_when_replans_exhausted(self):
        plan = TaskPlan(goal="g", steps=[PlanStep(id="s1", description="step 1")])
        retry_plan = TaskPlan(goal="g", steps=[PlanStep(id="r", description="retry")])
        worker = FakeWorker([_failure("boom1"), _failure("boom2")])
        agent = MainAgent(
            FakePlanner(plan), FakeReplanner(retry_plan), {"coding": worker}, max_replans=1
        )
        result = agent.run("g")

        self.assertEqual(result.status, AgentStatus.FAILED)
        self.assertEqual(result.artifacts["replans"], 1)

    def test_synthesizes_final_answer(self):
        plan = TaskPlan(
            goal="introduce files",
            steps=[PlanStep(id="s1", description="explore", assigned_agent="explorer")],
        )
        worker = FakeWorker(
            [
                AgentResult(
                    agent_name="explorer_agent",
                    status=AgentStatus.SUCCESS,
                    summary="found",
                    artifacts={"report": {"findings": "two files"}},
                )
            ]
        )
        agent = MainAgent(
            FakePlanner(plan),
            FakeReplanner(plan),
            {"explorer": worker},
            llm=_TextLLM("这里是最终回答。"),
        )
        result = agent.run("introduce files")
        self.assertEqual(result.summary, "这里是最终回答。")

    def test_injects_workspace_context(self):
        plan = TaskPlan(
            goal="g",
            steps=[
                PlanStep(id="s1", description="explore", assigned_agent="explorer"),
                PlanStep(id="s2", description="code", assigned_agent="coding"),
            ],
        )
        worker = FakeWorker(
            [
                AgentResult(
                    agent_name="explorer_agent",
                    status=AgentStatus.SUCCESS,
                    summary="found",
                    artifacts={"report": {"findings": "divide bug", "relevant_files": ["calculator.py"]}},
                ),
                AgentResult(
                    agent_name="coding_agent",
                    status=AgentStatus.SUCCESS,
                    summary="fixed",
                    artifacts={"report": {"modified_files": ["calculator.py"]}},
                ),
            ]
        )
        agent = MainAgent(
            FakePlanner(plan), FakeReplanner(plan), {"explorer": worker, "coding": worker}
        )
        agent.run("g")
        self.assertIn("Workspace context", worker.tasks[1])
        self.assertIn("calculator.py", worker.tasks[1])


class RecordingConsumer:
    def __init__(self):
        self.events = []

    def on_event(self, event):
        self.events.append(event)


class MainAgentEventTest(unittest.TestCase):
    def test_emits_lifecycle_events(self):
        plan = TaskPlan(
            goal="g",
            steps=[PlanStep(id="s1", description="step 1", assigned_agent="explorer")],
        )
        worker = FakeWorker([_success("did s1")])
        bus = EventBus()
        consumer = RecordingConsumer()
        bus.subscribe(consumer)
        agent = MainAgent(
            FakePlanner(plan), FakeReplanner(plan), {"explorer": worker}, event_bus=bus
        )
        result = agent.run("g")

        self.assertEqual(result.status, AgentStatus.SUCCESS)
        kinds = [e.event_type for e in consumer.events]
        self.assertIn(EventType.AGENT_START, kinds)
        self.assertIn(EventType.PLAN_CREATED, kinds)
        self.assertIn(EventType.STEP_START, kinds)
        self.assertIn(EventType.SUBAGENT_START, kinds)
        self.assertIn(EventType.SUBAGENT_FINISH, kinds)
        self.assertEqual(kinds[-1], EventType.AGENT_FINISH)
        # PLAN_CREATED carries structured steps; SUBAGENT events carry agent_id.
        plan_created = consumer.events[1]
        self.assertEqual(plan_created.payload["steps"][0]["assigned_agent"], "explorer")
        subagent_start = [e for e in consumer.events if e.event_type == EventType.SUBAGENT_START][0]
        self.assertEqual(subagent_start.agent_id, "explorer")


class TimingWorker:
    """Records wall-clock start/end of each run so tests can assert overlap."""

    def __init__(self, sleep: float = 0.2):
        self.sleep = sleep
        self.runs: list[tuple[float, float]] = []
        self.tasks: list[str] = []

    def run(self, task):
        start = time.monotonic()
        self.tasks.append(task)
        time.sleep(self.sleep)
        self.runs.append((start, time.monotonic()))
        return _success("ok")


class MainAgentDelegationTest(unittest.TestCase):
    def test_simple_step_routes_to_direct_worker(self):
        plan = TaskPlan(
            goal="g",
            steps=[PlanStep(id="s1", description="list files", assigned_agent="explorer")],
        )
        coding = FakeWorker([_success("unused")])
        direct = FakeWorker([_success("did directly")])
        agent = MainAgent(
            FakePlanner(plan),
            FakeReplanner(plan),
            {"coding": coding},
            direct_worker=direct,
        )
        result = agent.run("g")

        self.assertEqual(result.status, AgentStatus.SUCCESS)
        self.assertEqual(len(direct.tasks), 1)
        self.assertEqual(len(coding.tasks), 0)
        self.assertEqual(result.artifacts["direct_steps"], 1)
        # The direct subtask must not reference a submit_report tool it lacks.
        self.assertNotIn("submit_report", direct.tasks[0])
        self.assertIn("plain text", direct.tasks[0])

    def test_complex_step_delegates_and_emits_delegation_event(self):
        plan = TaskPlan(
            goal="g",
            steps=[PlanStep(id="s1", description="implement a feature", assigned_agent="coding")],
        )
        worker = FakeWorker([_success("ok")])
        bus = EventBus()
        consumer = RecordingConsumer()
        bus.subscribe(consumer)
        agent = MainAgent(
            FakePlanner(plan), FakeReplanner(plan), {"coding": worker}, event_bus=bus
        )
        agent.run("g")

        delegations = [e for e in consumer.events if e.event_type == EventType.DELEGATION]
        self.assertEqual(len(delegations), 1)
        self.assertEqual(delegations[0].payload["strategy"], "delegate")
        self.assertEqual(delegations[0].payload["step_ids"], ["s1"])

    def test_parallel_read_only_steps_run_concurrently(self):
        plan = TaskPlan(
            goal="g",
            steps=[
                PlanStep(id="a", description="read mod_a", assigned_agent="explorer"),
                PlanStep(id="b", description="read mod_b", assigned_agent="explorer"),
            ],
        )
        worker = TimingWorker()
        agent = MainAgent(FakePlanner(plan), FakeReplanner(plan), {"explorer": worker})
        result = agent.run("g")

        self.assertEqual(result.status, AgentStatus.SUCCESS)
        self.assertEqual(len(worker.runs), 2)
        self.assertEqual(result.artifacts["parallel_batches"], 1)
        starts = [s for s, _ in worker.runs]
        ends = [e for _, e in worker.runs]
        # Overlap (max start < min end) proves the two workers ran concurrently,
        # not serially.
        self.assertLess(max(starts), min(ends))


class MainAgentCascadeTest(unittest.TestCase):
    """DIRECT-first cascade: a failed (or judged-incomplete) direct attempt escalates."""

    def test_direct_failure_escalates_to_delegate(self):
        plan = TaskPlan(
            goal="g",
            steps=[PlanStep(id="s1", description="list files", assigned_agent="coding")],
        )
        direct = FakeWorker([_failure("boom")])
        coding = FakeWorker([_success("delegated ok")])
        agent = MainAgent(
            FakePlanner(plan),
            FakeReplanner(plan),
            {"coding": coding},
            direct_worker=direct,
        )
        result = agent.run("g")

        self.assertEqual(result.status, AgentStatus.SUCCESS)
        self.assertEqual(len(direct.tasks), 1)
        self.assertEqual(len(coding.tasks), 1)  # escalated
        self.assertEqual(result.artifacts["escalations"], 1)

    def test_verify_judge_escalates_when_incomplete(self):
        plan = TaskPlan(
            goal="g",
            steps=[PlanStep(id="s1", description="list files", assigned_agent="coding")],
        )
        direct = FakeWorker([_success("I could not finish writing the file")])
        coding = FakeWorker([_success("delegated ok")])
        agent = MainAgent(
            FakePlanner(plan),
            FakeReplanner(plan),
            {"coding": coding},
            direct_worker=direct,
            llm=_TextLLM("NO"),
            delegation_policy=DelegationPolicy(verify_low=0),  # always verify
        )
        result = agent.run("g")

        self.assertEqual(result.status, AgentStatus.SUCCESS)
        self.assertEqual(len(coding.tasks), 1)
        self.assertEqual(result.artifacts["escalations"], 1)

    def test_verify_judge_passes_does_not_escalate(self):
        plan = TaskPlan(
            goal="g",
            steps=[PlanStep(id="s1", description="list files", assigned_agent="coding")],
        )
        direct = FakeWorker([_success("done")])
        coding = FakeWorker([_success("unused")])
        agent = MainAgent(
            FakePlanner(plan),
            FakeReplanner(plan),
            {"coding": coding},
            direct_worker=direct,
            llm=_TextLLM("YES"),
            delegation_policy=DelegationPolicy(verify_low=0),  # always verify
        )
        result = agent.run("g")

        self.assertEqual(result.status, AgentStatus.SUCCESS)
        self.assertEqual(len(coding.tasks), 0)  # no escalation
        self.assertEqual(result.artifacts["escalations"], 0)


class LanguageTest(unittest.TestCase):
    def test_answer_language_chinese(self):
        self.assertEqual(_answer_language("介绍一下这个目录"), "Answer in Chinese (中文).")

    def test_answer_language_english(self):
        self.assertEqual(_answer_language("fix the failing test"), "Answer in English.")

    def test_synthesis_instructs_language(self):
        plan = TaskPlan(
            goal="g",
            steps=[PlanStep(id="s1", description="explore", assigned_agent="explorer")],
        )
        worker = FakeWorker(
            [
                AgentResult(
                    agent_name="explorer_agent",
                    status=AgentStatus.SUCCESS,
                    summary="found",
                    artifacts={"report": {"findings": "two files"}},
                )
            ]
        )
        llm = _RecordingLLM("回答")
        agent = MainAgent(
            FakePlanner(plan), FakeReplanner(plan), {"explorer": worker}, llm=llm
        )
        agent.run("介绍一下")
        user_msg = [m for m in llm.messages if m.role == "user"][0]
        self.assertIn("Answer in Chinese", user_msg.content)


if __name__ == "__main__":
    unittest.main()
