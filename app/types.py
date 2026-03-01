"""Shared types and constants for the application."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

# Maximum characters for tool result payloads sent to the LLM context.
# Used by both AgenticPlanner and PortfolioDiscovery decision loops.
MAX_TOOL_RESULT_CHARS = 8000


@dataclass
class PlannerResult:
    """Result from the agentic planner."""

    message: str
    tokens: List[Dict[str, str]] = field(default_factory=list)
    raw_data: Dict[str, Any] = field(default_factory=dict)
