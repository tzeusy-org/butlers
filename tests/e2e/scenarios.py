"""E2E scenario definitions and database assertion helpers.

Scenarios are declarative test cases that specify an ingest.v1 envelope,
expected routing, expected tool calls, and post-execution database assertions.
The scenario runner injects envelopes at the ``ingest_v1()`` boundary and
validates all assertions after session completion.

Unified design (per specs/ingress-injection/spec.md):
- Single ``Scenario`` dataclass covers all evaluation dimensions:
  routing accuracy, tool-call accuracy, and effect verification.
- Scenarios are authored via ``email_envelope()`` / ``telegram_envelope()``
  factory functions from ``tests.e2e.envelopes``.
- Tag-based filtering is available via ``--scenarios`` pytest CLI option.

Tag conventions:
- Channel:   email | telegram
- Butler:    health | calendar | relationship | general | interactive
- Scope:     smoke | classification | tool-call | db-effect | edge-case
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tests.e2e.envelopes import email_envelope, telegram_envelope

# ---------------------------------------------------------------------------
# Core data types
# ---------------------------------------------------------------------------


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
class Scenario:
    """Declarative end-to-end test scenario using ingest.v1 envelopes.

    This dataclass is the unified definition for all evaluation modes:
    - Routing accuracy: does ``IngestAcceptedResponse.triage_target`` match
      ``expected_routing``?
    - Tool-call accuracy: do captured tool calls contain ``expected_tool_calls``
      (subset match)?
    - Effect verification: do ``db_assertions`` pass after session completion?

    Attributes:
        id: Unique scenario identifier (e.g., "email-meeting-invite")
        description: Human-readable scenario description
        envelope: ingest.v1 payload dict (built via factory functions)
        expected_routing: Expected target butler name for routing. None for
            multi-target scenarios or when routing is not being tested.
        expected_tool_calls: Expected tool names to appear in session tool calls.
            Uses subset matching — scenario passes if all listed tools are called,
            even if additional internal tools are also called.
        db_assertions: Database state assertions to validate after session
            completion. Each assertion queries a specific butler's schema.
        tags: Categorization tags for filtering. Use channel (email/telegram),
            butler category (health/calendar/etc.), and scope (smoke/edge-case).
        timeout_seconds: Maximum time to wait for scenario completion.
            Defaults to 60 seconds.
    """

    id: str
    description: str
    envelope: dict[str, Any]
    expected_routing: str | None = None
    expected_tool_calls: list[str] = field(default_factory=list)
    db_assertions: list[DbAssertion] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    timeout_seconds: int = 60


# ---------------------------------------------------------------------------
# Email → Calendar scenarios
# ---------------------------------------------------------------------------

EMAIL_CALENDAR_SCENARIOS: list[Scenario] = [
    Scenario(
        id="email-meeting-invite",
        description="Email about a team lunch → calendar event creation",
        envelope=email_envelope(
            sender="alice@example.com",
            subject="Team lunch Thursday",
            body="Hey, let's do team lunch this Thursday at noon at the usual place on Main St.",
        ),
        expected_routing="calendar",
        expected_tool_calls=["calendar_create"],
        tags=["email", "calendar", "smoke", "tool-call"],
    ),
    Scenario(
        id="email-meeting-reschedule",
        description="Email rescheduling an existing meeting → calendar update",
        envelope=email_envelope(
            sender="bob@corp.example",
            subject="Re: Project sync — rescheduling to 3pm",
            body=(
                "Hi, I need to push our Friday project sync from 2pm to 3pm. "
                "Can you update the calendar invite?"
            ),
            thread_id="thread-project-sync-001",
        ),
        expected_routing="calendar",
        expected_tool_calls=["calendar_update"],
        tags=["email", "calendar", "tool-call"],
    ),
    Scenario(
        id="email-meeting-invite-thread-affinity",
        description="Email reply in existing thread uses thread-affinity routing",
        envelope=email_envelope(
            sender="carol@example.com",
            subject="Re: Doctor appointment confirmation",
            body="Yes, 10am Tuesday works perfectly. See you then!",
            thread_id="thread-doctor-appt-xyz",
        ),
        expected_routing="calendar",  # Thread was previously routed to calendar
        tags=["email", "calendar", "thread-affinity", "edge-case"],
    ),
    Scenario(
        id="email-dinner-reservation",
        description="Email about dinner plans → calendar event",
        envelope=email_envelope(
            sender="friend@personal.example",
            subject="Dinner Saturday night",
            body=(
                "Are you free for dinner Saturday at 7pm? "
                "I was thinking the Italian place on Oak Street."
            ),
        ),
        expected_routing="calendar",
        expected_tool_calls=["calendar_create"],
        tags=["email", "calendar", "tool-call"],
    ),
    Scenario(
        id="email-conference-registration",
        description="Conference registration confirmation email",
        envelope=email_envelope(
            sender="noreply@techconf.example",
            subject="Your PyCon 2026 registration is confirmed",
            body=(
                "Thank you for registering for PyCon 2026! "
                "Event dates: May 14-16, 2026, Chicago Convention Center."
            ),
        ),
        expected_routing="calendar",
        expected_tool_calls=["calendar_create"],
        tags=["email", "calendar", "smoke", "tool-call"],
    ),
]


# ---------------------------------------------------------------------------
# Telegram → Health scenarios
# ---------------------------------------------------------------------------

TELEGRAM_HEALTH_SCENARIOS: list[Scenario] = [
    Scenario(
        id="telegram-weight-log",
        description="Telegram message logging weight measurement",
        envelope=telegram_envelope(
            chat_id=12345,
            text="I weigh 75.5 kg today",
            from_user="test-user",
        ),
        expected_routing="health",
        expected_tool_calls=["log_measurement"],
        tags=["telegram", "health", "smoke", "tool-call"],
    ),
    Scenario(
        id="telegram-meal-log",
        description="Telegram message logging a meal",
        envelope=telegram_envelope(
            chat_id=12345,
            text="Just had grilled chicken with salad for lunch, about 450 calories",
            from_user="test-user",
        ),
        expected_routing="health",
        expected_tool_calls=["log_meal"],
        tags=["telegram", "health", "tool-call"],
    ),
    Scenario(
        id="telegram-exercise-log",
        description="Telegram message logging exercise activity",
        envelope=telegram_envelope(
            chat_id=12345,
            text="I ran 5km this morning in 28 minutes",
            from_user="test-user",
        ),
        expected_routing="health",
        expected_tool_calls=["log_exercise"],
        tags=["telegram", "health", "smoke", "tool-call"],
    ),
    Scenario(
        id="telegram-blood-pressure-log",
        description="Telegram message logging blood pressure reading",
        envelope=telegram_envelope(
            chat_id=12345,
            text="BP reading: 120/80 this morning",
            from_user="test-user",
        ),
        expected_routing="health",
        expected_tool_calls=["log_measurement"],
        tags=["telegram", "health", "tool-call"],
    ),
    Scenario(
        id="telegram-sleep-log",
        description="Telegram message logging sleep quality",
        envelope=telegram_envelope(
            chat_id=12345,
            text="Slept 7 hours last night, woke up feeling rested",
            from_user="test-user",
        ),
        expected_routing="health",
        expected_tool_calls=["log_measurement"],
        tags=["telegram", "health", "tool-call"],
    ),
    Scenario(
        id="telegram-medication-reminder",
        description="Telegram message about taking medication",
        envelope=telegram_envelope(
            chat_id=12345,
            text="Took my vitamin D and omega-3 supplements this morning",
            from_user="test-user",
        ),
        expected_routing="health",
        tags=["telegram", "health", "classification"],
    ),
]


# ---------------------------------------------------------------------------
# Telegram → Interactive / conversational scenarios
# ---------------------------------------------------------------------------

TELEGRAM_INTERACTIVE_SCENARIOS: list[Scenario] = [
    Scenario(
        id="telegram-health-query",
        description="Telegram question about health data → notify reply",
        envelope=telegram_envelope(
            chat_id=12345,
            text="How much did I weigh last week?",
            from_user="test-user",
        ),
        expected_routing="health",
        expected_tool_calls=["notify"],
        tags=["telegram", "health", "interactive", "smoke"],
    ),
    Scenario(
        id="telegram-general-question",
        description="Telegram general question → general butler with notify reply",
        envelope=telegram_envelope(
            chat_id=12345,
            text="What's the capital of France?",
            from_user="test-user",
        ),
        expected_routing="general",
        expected_tool_calls=["notify"],
        tags=["telegram", "general", "interactive"],
    ),
    Scenario(
        id="telegram-calendar-query",
        description="Telegram question about upcoming schedule → notify reply",
        envelope=telegram_envelope(
            chat_id=12345,
            text="What do I have scheduled for next week?",
            from_user="test-user",
        ),
        expected_routing="calendar",
        expected_tool_calls=["notify"],
        tags=["telegram", "calendar", "interactive"],
    ),
    Scenario(
        id="telegram-relationship-query",
        description="Telegram question about contacts → notify reply",
        envelope=telegram_envelope(
            chat_id=12345,
            text="When did I last talk to Sarah?",
            from_user="test-user",
        ),
        expected_routing="relationship",
        expected_tool_calls=["notify"],
        tags=["telegram", "relationship", "interactive"],
    ),
]


# ---------------------------------------------------------------------------
# Multi-butler classification edge cases
# ---------------------------------------------------------------------------

CLASSIFICATION_EDGE_CASE_SCENARIOS: list[Scenario] = [
    Scenario(
        id="email-health-and-calendar-combined",
        description="Email mentioning doctor visit with appointment details",
        envelope=email_envelope(
            sender="alice@example.com",
            subject="Doctor appointment next Tuesday",
            body=(
                "I have an appointment with Dr. Chen next Tuesday at 2pm at the clinic. "
                "I need to fast 8 hours beforehand for the blood work."
            ),
        ),
        expected_routing="calendar",  # Primary: schedule the appointment
        tags=["email", "calendar", "health", "classification", "edge-case"],
    ),
    Scenario(
        id="telegram-ambiguous-health-relationship",
        description="Message mentioning both health context and a person",
        envelope=telegram_envelope(
            chat_id=12345,
            text="Mom called today — she reminded me to take my blood pressure meds",
            from_user="test-user",
        ),
        # Expect classification to either health or relationship depending on emphasis
        expected_routing=None,  # Multi-target: health (meds) or relationship (Mom)
        tags=["telegram", "health", "relationship", "classification", "edge-case"],
    ),
    Scenario(
        id="email-newsletter-low-signal",
        description="Marketing newsletter — low routing signal",
        envelope=email_envelope(
            sender="noreply@newsletter.example",
            subject="This week's top articles",
            body=(
                "Check out this week's top reads: AI trends, productivity hacks, "
                "and the best coffee shops in your city."
            ),
        ),
        expected_routing="general",
        tags=["email", "general", "classification", "edge-case"],
    ),
    Scenario(
        id="telegram-finance-query",
        description="Telegram message about expense tracking",
        envelope=telegram_envelope(
            chat_id=12345,
            text="I spent $45 on groceries today at Whole Foods",
            from_user="test-user",
        ),
        expected_routing="finance",
        tags=["telegram", "finance", "classification"],
    ),
    Scenario(
        id="telegram-travel-query",
        description="Telegram message about travel planning",
        envelope=telegram_envelope(
            chat_id=12345,
            text="Looking at Tokyo flights in March, what should I know about visa requirements?",
            from_user="test-user",
        ),
        expected_routing="travel",
        tags=["telegram", "travel", "classification"],
    ),
    Scenario(
        id="email-travel-itinerary",
        description="Email with travel itinerary → calendar + travel butler",
        envelope=email_envelope(
            sender="bookings@airline.example",
            subject="Your flight booking: SFO → NRT on March 15",
            body=(
                "Flight confirmation: AA1234\n"
                "Departs SFO: March 15, 2026 at 11:45 PM\n"
                "Arrives NRT: March 17, 2026 at 6:30 AM (local time)\n"
                "Seat: 24A (Economy)\n"
                "Booking reference: XYZABC"
            ),
        ),
        expected_routing="calendar",  # Primary: add to calendar
        tags=["email", "calendar", "travel", "classification"],
    ),
    Scenario(
        id="telegram-education-query",
        description="Telegram message about learning resource",
        envelope=telegram_envelope(
            chat_id=12345,
            text="Can you recommend a good Python course for beginners?",
            from_user="test-user",
        ),
        expected_routing="education",
        tags=["telegram", "education", "classification", "interactive"],
    ),
    Scenario(
        id="telegram-home-query",
        description="Telegram message about home maintenance",
        envelope=telegram_envelope(
            chat_id=12345,
            text="The kitchen faucet has been dripping for two days, need to fix it",
            from_user="test-user",
        ),
        expected_routing="home",
        tags=["telegram", "home", "classification"],
    ),
    Scenario(
        id="telegram-multi-domain-health-calendar",
        description="Telegram multi-domain: health log + scheduling combined",
        envelope=telegram_envelope(
            chat_id=12345,
            text=(
                "Ran 10km this morning feeling great. "
                "Also need to schedule my annual checkup for next month."
            ),
            from_user="test-user",
        ),
        expected_routing=None,  # Multi-target: health (run log) + calendar (checkup)
        tags=["telegram", "health", "calendar", "classification", "edge-case"],
    ),
    Scenario(
        id="email-relationship-contact-info",
        description="Email introducing a new contact with details",
        envelope=email_envelope(
            sender="hr@company.example",
            subject="New team member: David Park",
            body=(
                "Please welcome David Park to the engineering team! "
                "David joins as a senior backend engineer. "
                "You can reach him at david.park@company.example or on Slack @davidp."
            ),
        ),
        expected_routing="relationship",
        tags=["email", "relationship", "classification"],
    ),
]


# ---------------------------------------------------------------------------
# Combined scenario list
# ---------------------------------------------------------------------------

ALL_SCENARIOS: list[Scenario] = (
    EMAIL_CALENDAR_SCENARIOS
    + TELEGRAM_HEALTH_SCENARIOS
    + TELEGRAM_INTERACTIVE_SCENARIOS
    + CLASSIFICATION_EDGE_CASE_SCENARIOS
)


def get_scenarios_by_tags(tags: list[str]) -> list[Scenario]:
    """Return scenarios that match ALL of the given tags (AND filter).

    Parameters
    ----------
    tags:
        List of tag strings to filter by. A scenario is included only if
        it has ALL of the listed tags.

    Returns
    -------
    list[Scenario]
        Filtered list of scenarios.

    Examples
    --------
    >>> smoke_scenarios = get_scenarios_by_tags(["smoke"])
    >>> all(["smoke" in s.tags for s in smoke_scenarios])
    True
    """
    tag_set = set(tags)
    return [s for s in ALL_SCENARIOS if tag_set.issubset(s.tags)]
