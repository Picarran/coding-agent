"""Explicit agent state machine.

The project deliberately uses an explicit state machine instead of relying on
nested ``while True`` loops or on the model's natural-language claim of "done".
Every transition is logged, so runtime behavior is easy to trace and explain.
"""
from __future__ import annotations

import logging
from enum import Enum

logger = logging.getLogger(__name__)


class AgentState(str, Enum):
    """Lifecycle states of a single ReAct agent loop."""

    IDLE = "IDLE"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    MAX_STEPS = "MAX_STEPS"


class StateMachine:
    """Tracks the current state and logs every transition."""

    def __init__(self, initial: AgentState = AgentState.IDLE) -> None:
        self._state = initial
        logger.debug("state initialized as %s", initial.value)

    @property
    def current(self) -> AgentState:
        return self._state

    def transition(self, new_state: AgentState) -> None:
        logger.info("state transition: %s -> %s", self._state.value, new_state.value)
        self._state = new_state
