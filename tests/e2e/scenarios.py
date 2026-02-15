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


# ---------------------------------------------------------------------------
# Health Butler Scenarios
# ---------------------------------------------------------------------------

HEALTH_SCENARIOS = [
    E2EScenario(
        id="health-weight-log",
        description="Log a weight measurement",
        input_prompt="I weigh 75.5 kg today",
        expected_butler="health",
        tags=["classification", "health", "smoke"],
        db_assertions=[
            DbAssertion(
                butler="health",
                query="SELECT COUNT(*) as count FROM measurements WHERE type = 'weight'",
                expected=1,
                description="Weight measurement should be logged",
            ),
        ],
    ),
    E2EScenario(
        id="health-medication-track",
        description="Track medication intake",
        input_prompt="I take aspirin 100mg daily for my heart",
        expected_butler="health",
        tags=["classification", "health"],
        db_assertions=[
            DbAssertion(
                butler="health",
                query="SELECT COUNT(*) as count FROM medications WHERE name ILIKE '%aspirin%'",
                expected=1,
                description="Medication should be tracked",
            ),
        ],
    ),
    E2EScenario(
        id="health-food-preference",
        description="Food preference classification",
        input_prompt="I like chicken rice",
        expected_butler="health",
        tags=["classification", "health"],
    ),
]


# ---------------------------------------------------------------------------
# Relationship Butler Scenarios
# ---------------------------------------------------------------------------

RELATIONSHIP_SCENARIOS = [
    E2EScenario(
        id="relationship-contact-create",
        description="Create a new contact",
        input_prompt="Add Sarah Chen as a new contact, her email is sarah@example.com",
        expected_butler="relationship",
        tags=["classification", "relationship", "smoke"],
        db_assertions=[
            DbAssertion(
                butler="relationship",
                query="SELECT COUNT(*) as count FROM contacts WHERE name ILIKE '%sarah%'",
                expected=1,
                description="Contact should be created",
            ),
        ],
    ),
    E2EScenario(
        id="relationship-reminder",
        description="Set a social reminder",
        input_prompt="Remind me to call Mom next week",
        expected_butler="relationship",
        tags=["classification", "relationship"],
    ),
]


# ---------------------------------------------------------------------------
# Switchboard Classification Scenarios
# ---------------------------------------------------------------------------

SWITCHBOARD_SCENARIOS = [
    E2EScenario(
        id="switchboard-classify-health",
        description="Route health query to health butler",
        input_prompt="What medications am I currently taking?",
        expected_butler="health",
        tags=["classification", "switchboard", "smoke"],
    ),
    E2EScenario(
        id="switchboard-classify-relationship",
        description="Route relationship query to relationship butler",
        input_prompt="Who did I meet last month?",
        expected_butler="relationship",
        tags=["classification", "switchboard"],
    ),
    E2EScenario(
        id="switchboard-classify-general",
        description="Route general query to general butler",
        input_prompt="What's the weather today?",
        expected_butler="general",
        tags=["classification", "switchboard", "smoke"],
    ),
    E2EScenario(
        id="switchboard-multi-domain",
        description="Multi-domain message decomposition",
        input_prompt=(
            "I saw Dr. Smith today and got prescribed metformin 500mg twice daily. "
            "Also, remind me to send her a thank-you card next week."
        ),
        expected_butler=None,  # Multi-target scenario
        tags=["classification", "switchboard", "decomposition"],
    ),
]


# ---------------------------------------------------------------------------
# Combined Scenario List
# ---------------------------------------------------------------------------

ALL_SCENARIOS = HEALTH_SCENARIOS + RELATIONSHIP_SCENARIOS + SWITCHBOARD_SCENARIOS
