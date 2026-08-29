"""Orchestration modes (V2-5.2): user-selectable agent topology.

Three dials for the same workspace:

- ``FAST``     — one ReAct loop with the full toolset; no planner, no SubAgents,
                 no structured report. Cheapest; best for small tasks.
- ``AUTO``     — Main Agent + ``DelegationPolicy``: simple steps go DIRECT, complex
                 steps go to a role SubAgent, independent read-only steps run in
                 parallel. The default.
- ``THOROUGH`` — Main Agent, but every step goes through a full role SubAgent
                 (DIRECT is disabled). Highest care per step; read-only steps may
                 still run in parallel (parallelism does not cut quality).

``fast`` / ``thorough`` double as the lower / upper baselines for measuring the
value of ``auto`` in the eval dashboard.
"""
from __future__ import annotations

from enum import Enum


class OrchestrationMode(str, Enum):
    FAST = "fast"
    AUTO = "auto"
    THOROUGH = "thorough"
