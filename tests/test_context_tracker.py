"""Tests for the live context-usage meter (ContextTracker)."""
from __future__ import annotations

import unittest

from src.core.events import EventType, TraceEvent
from src.main import ContextTracker


def _llm_call(prompt: int) -> TraceEvent:
    return TraceEvent(EventType.LLM_CALL, payload={"prompt_tokens": prompt})


class ContextTrackerTest(unittest.TestCase):
    def test_tracks_latest_prompt_tokens(self):
        tracker = ContextTracker()
        tracker.on_event(_llm_call(12000))
        self.assertEqual(tracker.prompt_tokens, 12000)
        tracker.on_event(_llm_call(9000))
        self.assertEqual(tracker.prompt_tokens, 9000)

    def test_compact_lowers_estimate_by_removed_chars(self):
        tracker = ContextTracker()
        tracker.on_event(_llm_call(12000))
        tracker.on_event(
            TraceEvent(EventType.CONTEXT_COMPACT, payload={"removed_chars": 8000})
        )
        self.assertEqual(tracker.prompt_tokens, 12000 - 8000 // 4)  # 10000

    def test_compact_never_goes_negative(self):
        tracker = ContextTracker()
        tracker.on_event(_llm_call(100))
        tracker.on_event(
            TraceEvent(EventType.CONTEXT_COMPACT, payload={"removed_chars": 10000})
        )
        self.assertEqual(tracker.prompt_tokens, 0)

    def test_compact_without_removed_chars_is_ignored(self):
        tracker = ContextTracker()
        tracker.on_event(_llm_call(12000))
        tracker.on_event(
            TraceEvent(EventType.CONTEXT_COMPACT, payload={"removed": 3})
        )
        self.assertEqual(tracker.prompt_tokens, 12000)


if __name__ == "__main__":
    unittest.main()
