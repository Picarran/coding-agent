"""Explicit agent state machines.

The project deliberately uses explicit state machines instead of relying on
nested ``while True`` loops or on the model's natural-language claim of "done".
Every transition is logged, so runtime behavior is easy to trace and explain.
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Generic, TypeVar

logger = logging.getLogger(__name__)

StateT = TypeVar("StateT", bound=Enum)


class AgentState(str, Enum):
    """Lifecycle states of a single ReAct agent loop."""

    IDLE = "IDLE"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    MAX_STEPS = "MAX_STEPS"
    BLOCKED = "BLOCKED"


class MainAgentState(str, Enum):
    """Lifecycle states of the Plan-and-Execute Main Agent."""

    IDLE = "IDLE"
    PLANNING = "PLANNING"
    DISPATCHING = "DISPATCHING"
    EXECUTING = "EXECUTING"
    OBSERVING = "OBSERVING"
    REPLANNING = "REPLANNING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"
    BLOCKED = "BLOCKED"


class StateMachine(Generic[StateT]):
    """Tracks the current state and logs every transition."""

    def __init__(self, initial: StateT) -> None:
        self._state = initial
        logger.debug("state initialized as %s", initial.value)

    @property
    def current(self) -> StateT:
        return self._state

    def transition(self, new_state: StateT) -> None:
        logger.info("state transition: %s -> %s", self._state.value, new_state.value)
        self._state = new_state
