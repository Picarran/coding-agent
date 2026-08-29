"""Model routing (V2-6): pick a strong or fast model per task type.

The upper layers call ``ModelRouter.route(task_type)`` to get the right client,
so swapping in a second (cheap) model later needs no changes above this point.

Task-type → model mapping:

- strong: ``planning``, ``coding`` (the work that must not regress in quality).
- fast:   ``exploration``, ``testing``, ``summarization``, ``synthesis`` (cheap,
          high-volume, or easy-to-verify work).

With a single model configured, ``route`` returns that same client for every
task type — the interface is live, the split just has no effect yet.
"""
from __future__ import annotations

import os
from enum import Enum

from src.llm.base import LLMClient


class TaskType(str, Enum):
    PLANNING = "planning"
    CODING = "coding"
    EXPLORATION = "exploration"
    TESTING = "testing"
    SUMMARIZATION = "summarization"
    SYNTHESIS = "synthesis"


STRONG_TASK_TYPES = frozenset({TaskType.PLANNING, TaskType.CODING})


class ModelRouter:
    """Routes a task type to a strong or fast LLM client."""

    def __init__(self, strong: LLMClient, fast: LLMClient | None = None) -> None:
        self._strong = strong
        self._fast = fast if fast is not None else strong

    def route(self, task_type: TaskType | str) -> LLMClient:
        t = TaskType(task_type)
        return self._strong if t in STRONG_TASK_TYPES else self._fast

    @property
    def split(self) -> bool:
        """True when strong and fast are actually two different clients."""
        return self._fast is not self._strong


def build_model_router(strong: LLMClient, fast_model: str | None = None) -> ModelRouter:
    """Build a router from an existing strong client.

    ``fast_model`` (or the ``DEEPSEEK_FAST_MODEL`` env var) optionally points at a
    cheaper model; when absent, strong and fast share the one client.
    """
    fast_model = fast_model or os.environ.get("DEEPSEEK_FAST_MODEL")
    if not fast_model:
        return ModelRouter(strong)
    from src.llm.deepseek_client import DeepSeekClient

    return ModelRouter(strong, DeepSeekClient(model=fast_model))
