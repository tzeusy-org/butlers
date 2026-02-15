"""E2E scenario definitions and database assertion helpers.

Scenarios are declarative test cases that specify inputs, expected routing,
tool calls, and database state changes. The scenario runner executes them
against the live ecosystem and validates all assertions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DbAssertion:
    """A database assertion to validate after scenario execution.

    Attributes:
        butler: Butler name whose database to query (e.g., "health", "relationship")
        query: SQL query to execute
        expected: Expected result. Can be:
            - int: Expected row count (for COUNT queries)
            - dict: Expected single-row result (column->value mapping)
            - list[dict]: Expected multi-row result
            - None: Assertion passes if query returns no rows
        description: Human-readable assertion description for test output
    """

    butler: str
    query: str
    expected: int | dict[str, Any] | list[dict[str, Any]] | None
    description: str = ""


@dataclass
class E2EScenario:
    """Declarative end-to-end test scenario.

    Attributes:
        id: Unique scenario identifier (e.g., "health-weight-log")
        description: Human-readable scenario description
        input_prompt: The user input/message to send to the ecosystem
        expected_butler: Expected target butler for routing (None if multi-target)
        expected_tool_calls: List of expected tool names to be called
        db_assertions: Database state assertions to validate after execution
        timeout_seconds: Maximum time to wait for scenario completion
        tags: Categorization tags (e.g., ["classification", "health"])
    """

    id: str
    description: str
    input_prompt: str
    expected_butler: str | None = None
    expected_tool_calls: list[str] = field(default_factory=list)
    db_assertions: list[DbAssertion] = field(default_factory=list)
    timeout_seconds: int = 30
    tags: list[str] = field(default_factory=list)
