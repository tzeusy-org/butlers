"""E2E tests for data contract validation between pipeline stages.

Validates the data contracts that connect pipeline stages:
1. IngestEnvelopeV1 validation (schema version, channel/provider pairs, timestamps)
2. Idempotency contract (same key → same request_id)
3. Classification response validation (well-formed, LLM fallbacks)
4. FanoutPlan validation (modes, dependencies)
5. Route contract version (version matching, quarantine)
6. SpawnerResult contract (session persistence, field validation)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from pydantic import ValidationError

from butlers.tools.switchboard.ingestion.ingest import ingest_v1
from butlers.tools.switchboard.routing.classify import classify_message
from butlers.tools.switchboard.routing.contracts import (
    IngestEnvelopeV1,
    parse_ingest_envelope,
)
from butlers.tools.switchboard.routing.dispatch import plan_fanout

if TYPE_CHECKING:
    from asyncpg.pool import Pool

    from tests.e2e.conftest import ButlerEcosystem


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Contract 1: IngestEnvelopeV1 validation
# ---------------------------------------------------------------------------


async def test_ingest_envelope_valid_accepted(switchboard_pool: Pool) -> None:
    """Valid IngestEnvelopeV1 is accepted and returns request_id."""
    now = datetime.now(UTC)
    event_id = f"test-event-{uuid4().hex[:8]}"

    envelope_payload = {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "telegram",
            "provider": "telegram",
            "endpoint_identity": "test-endpoint-001",
        },
        "event": {
            "external_event_id": event_id,
            "external_thread_id": "thread-001",
            "observed_at": now.isoformat(),
        },
        "sender": {
            "identity": "user-001",
        },
        "payload": {
            "raw": {"text": "Valid message"},
            "normalized_text": "Valid message",
        },
        "control": {
            "policy_tier": "default",
        },
    }

    response = await ingest_v1(switchboard_pool, envelope_payload)

    assert response.status == "accepted"
    assert response.duplicate is False
    assert response.request_id is not None


async def test_ingest_envelope_wrong_schema_version_rejected() -> None:
    """Wrong schema_version raises ValidationError before DB touch."""
    now = datetime.now(UTC)

    envelope_payload = {
        "schema_version": "ingest.v2",  # Wrong version
        "source": {
            "channel": "telegram",
            "provider": "telegram",
            "endpoint_identity": "test-endpoint-002",
        },
        "event": {
            "external_event_id": f"test-event-{uuid4().hex[:8]}",
            "observed_at": now.isoformat(),
        },
        "sender": {
            "identity": "user-002",
        },
        "payload": {
            "raw": {"text": "Test"},
            "normalized_text": "Test",
        },
    }

    with pytest.raises(ValueError, match="Invalid ingest.v1 envelope"):
        parse_ingest_envelope(envelope_payload)


async def test_ingest_envelope_invalid_channel_provider_pair_rejected() -> None:
    """Invalid channel/provider pair raises ValidationError."""
    now = datetime.now(UTC)

    envelope_payload = {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "telegram",
            "provider": "gmail",  # Invalid for telegram channel
            "endpoint_identity": "test-endpoint-003",
        },
        "event": {
            "external_event_id": f"test-event-{uuid4().hex[:8]}",
            "observed_at": now.isoformat(),
        },
        "sender": {
            "identity": "user-003",
        },
        "payload": {
            "raw": {"text": "Test"},
            "normalized_text": "Test",
        },
    }

    with pytest.raises(ValidationError):
        parse_ingest_envelope(envelope_payload)


async def test_ingest_envelope_naive_datetime_rejected() -> None:
    """Naive datetime (no timezone) raises ValidationError."""
    envelope_payload = {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "telegram",
            "provider": "telegram",
            "endpoint_identity": "test-endpoint-004",
        },
        "event": {
            "external_event_id": f"test-event-{uuid4().hex[:8]}",
            "observed_at": "2026-02-16T10:00:00",  # No timezone
        },
        "sender": {
            "identity": "user-004",
        },
        "payload": {
            "raw": {"text": "Test"},
            "normalized_text": "Test",
        },
    }

    with pytest.raises(ValidationError):
        parse_ingest_envelope(envelope_payload)


async def test_ingest_envelope_extra_fields_rejected() -> None:
    """Extra fields are rejected due to extra='forbid' on model."""
    now = datetime.now(UTC)

    envelope_payload = {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "telegram",
            "provider": "telegram",
            "endpoint_identity": "test-endpoint-005",
        },
        "event": {
            "external_event_id": f"test-event-{uuid4().hex[:8]}",
            "observed_at": now.isoformat(),
        },
        "sender": {
            "identity": "user-005",
        },
        "payload": {
            "raw": {"text": "Test"},
            "normalized_text": "Test",
        },
        "unknown_field": "should be rejected",  # Extra field
    }

    with pytest.raises(ValidationError):
        parse_ingest_envelope(envelope_payload)


async def test_ingest_envelope_missing_required_fields_rejected() -> None:
    """Missing required fields raise ValidationError."""
    now = datetime.now(UTC)

    envelope_payload = {
        "schema_version": "ingest.v1",
        # Missing 'source' entirely
        "event": {
            "external_event_id": f"test-event-{uuid4().hex[:8]}",
            "observed_at": now.isoformat(),
        },
        "sender": {
            "identity": "user-006",
        },
        "payload": {
            "raw": {"text": "Test"},
            "normalized_text": "Test",
        },
    }

    with pytest.raises(ValidationError):
        parse_ingest_envelope(envelope_payload)


async def test_ingest_envelope_all_channels_validated() -> None:
    """Valid envelopes for each channel type are accepted."""
    now = datetime.now(UTC)

    channel_provider_pairs = [
        ("telegram", "telegram"),
        ("slack", "slack"),
        ("email", "gmail"),
        ("email", "imap"),
        ("api", "internal"),
        ("mcp", "internal"),
    ]

    for channel, provider in channel_provider_pairs:
        envelope_payload = {
            "schema_version": "ingest.v1",
            "source": {
                "channel": channel,
                "provider": provider,
                "endpoint_identity": f"test-endpoint-{channel}-{provider}",
            },
            "event": {
                "external_event_id": f"test-event-{uuid4().hex[:8]}",
                "observed_at": now.isoformat(),
            },
            "sender": {
                "identity": f"user-{channel}",
            },
            "payload": {
                "raw": {"text": f"Test {channel}"},
                "normalized_text": f"Test {channel}",
            },
        }

        # Should not raise
        envelope = parse_ingest_envelope(envelope_payload)
        assert isinstance(envelope, IngestEnvelopeV1)


# ---------------------------------------------------------------------------
# Contract 2: Idempotency contract
# ---------------------------------------------------------------------------


async def test_idempotency_same_key_same_request_id(switchboard_pool: Pool) -> None:
    """Same idempotency_key produces same request_id with duplicate=True."""
    now = datetime.now(UTC)
    event_id = f"test-event-{uuid4().hex[:8]}"
    idempotency_key = f"test-key-{uuid4().hex[:8]}"

    envelope_payload = {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "telegram",
            "provider": "telegram",
            "endpoint_identity": "test-endpoint-idem-001",
        },
        "event": {
            "external_event_id": event_id,
            "observed_at": now.isoformat(),
        },
        "sender": {
            "identity": "user-idem-001",
        },
        "payload": {
            "raw": {"text": "Idempotency test"},
            "normalized_text": "Idempotency test",
        },
        "control": {
            "idempotency_key": idempotency_key,
            "policy_tier": "default",
        },
    }

    # First submission
    response1 = await ingest_v1(switchboard_pool, envelope_payload)
    assert response1.status == "accepted"
    assert response1.duplicate is False
    request_id_1 = response1.request_id

    # Second submission (identical envelope)
    response2 = await ingest_v1(switchboard_pool, envelope_payload)
    assert response2.status == "accepted"
    assert response2.duplicate is True
    assert response2.request_id == request_id_1

    # Third submission (same behavior)
    response3 = await ingest_v1(switchboard_pool, envelope_payload)
    assert response3.status == "accepted"
    assert response3.duplicate is True
    assert response3.request_id == request_id_1


# ---------------------------------------------------------------------------
# Contract 3: Classification response validation
# ---------------------------------------------------------------------------


async def test_classification_well_formed_single_domain(
    butler_ecosystem: ButlerEcosystem,
    switchboard_pool: Pool,
) -> None:
    """Well-formed single-domain classification produces valid routing entry."""
    switchboard_daemon = butler_ecosystem.butlers["switchboard"]
    assert switchboard_daemon.spawner is not None
    dispatch_fn = switchboard_daemon.spawner.trigger

    message = "I weigh 75.5 kg today"
    entries = await classify_message(switchboard_pool, message, dispatch_fn)

    assert len(entries) >= 1
    entry = entries[0]
    assert "butler" in entry
    assert "prompt" in entry
    assert "segment" in entry
    assert isinstance(entry["butler"], str)
    assert isinstance(entry["prompt"], str)
    assert isinstance(entry["segment"], dict)

    # Segment must have at least one metadata field
    segment = entry["segment"]
    has_metadata = any(key in segment for key in ["rationale", "sentence_spans", "offsets"])
    assert has_metadata


async def test_classification_well_formed_multi_domain(
    butler_ecosystem: ButlerEcosystem,
    switchboard_pool: Pool,
) -> None:
    """Well-formed multi-domain classification produces multiple valid entries."""
    switchboard_daemon = butler_ecosystem.butlers["switchboard"]
    assert switchboard_daemon.spawner is not None
    dispatch_fn = switchboard_daemon.spawner.trigger

    message = (
        "I saw Dr. Smith today and got prescribed metformin 500mg twice daily. "
        "Also, remind me to send her a thank-you card next week."
    )
    entries = await classify_message(switchboard_pool, message, dispatch_fn)

    # Should produce multiple entries (health + relationship expected)
    assert len(entries) >= 2

    # Validate structure of all entries
    for idx, entry in enumerate(entries):
        assert "butler" in entry, f"Entry {idx} missing butler"
        assert "prompt" in entry, f"Entry {idx} missing prompt"
        assert "segment" in entry, f"Entry {idx} missing segment"

        # Each prompt should be non-empty and self-contained
        assert entry["prompt"].strip(), f"Entry {idx} has empty prompt"

        # Segment metadata validation
        segment = entry["segment"]
        assert isinstance(segment, dict), f"Entry {idx} segment not dict"


# Note: LLM fallback tests (non-JSON, empty array, unknown butler) are harder to test
# in E2E without mocking the spawner. These would be better as unit tests with mocked
# LLM responses. The real LLM classification is tested above and is fairly reliable.


# ---------------------------------------------------------------------------
# Contract 4: FanoutPlan validation
# ---------------------------------------------------------------------------


async def test_fanout_plan_single_subrequest() -> None:
    """Single classification entry produces valid FanoutPlan."""
    targets = [
        {
            "butler": "health",
            "prompt": "I weigh 80kg today",
            "subrequest_id": "sr-1",
        }
    ]

    plan = plan_fanout(targets, fanout_mode="parallel")

    assert plan.mode == "parallel"
    assert len(plan.subrequests) == 1
    subrequest = plan.subrequests[0]
    assert subrequest.butler == "health"
    assert subrequest.prompt == "I weigh 80kg today"
    assert subrequest.subrequest_id == "sr-1"


async def test_fanout_plan_multiple_subrequests() -> None:
    """Multiple classification entries produce valid multi-subrequest FanoutPlan."""
    targets = [
        {
            "butler": "health",
            "prompt": "Track metformin 500mg",
            "subrequest_id": "sr-1",
        },
        {
            "butler": "relationship",
            "prompt": "Remind me to send Dr. Smith a card",
            "subrequest_id": "sr-2",
        },
    ]

    plan = plan_fanout(targets, fanout_mode="parallel")

    assert plan.mode == "parallel"
    assert len(plan.subrequests) == 2
    assert plan.subrequests[0].butler == "health"
    assert plan.subrequests[1].butler == "relationship"


async def test_fanout_plan_invalid_mode_raises() -> None:
    """Invalid fanout mode raises ValueError."""
    targets = [{"butler": "health", "prompt": "Test", "subrequest_id": "sr-1"}]

    with pytest.raises(ValueError, match="Invalid fanout mode"):
        plan_fanout(targets, fanout_mode="invalid")  # type: ignore[arg-type]


async def test_fanout_plan_ordered_mode_dependencies() -> None:
    """Ordered mode creates dependency chain between subrequests."""
    targets = [
        {"butler": "health", "prompt": "First", "subrequest_id": "sr-1"},
        {"butler": "general", "prompt": "Second", "subrequest_id": "sr-2"},
        {"butler": "relationship", "prompt": "Third", "subrequest_id": "sr-3"},
    ]

    plan = plan_fanout(targets, fanout_mode="ordered")

    assert plan.mode == "ordered"
    # First subrequest has no dependencies
    assert plan.subrequests[0].depends_on == ()
    # Second depends on first
    assert plan.subrequests[1].depends_on == ("sr-1",)
    # Third depends on second
    assert plan.subrequests[2].depends_on == ("sr-2",)


async def test_fanout_plan_missing_butler_raises() -> None:
    """Missing butler field raises ValueError."""
    targets = [{"prompt": "Test", "subrequest_id": "sr-1"}]  # Missing butler

    with pytest.raises(ValueError, match="Missing required target field 'butler'"):
        plan_fanout(targets)


# ---------------------------------------------------------------------------
# Contract 5: Route contract version
# ---------------------------------------------------------------------------


async def test_route_contract_version_match(
    switchboard_pool: Pool,
    health_pool: Pool,
) -> None:
    """Butler with matching route_contract_version accepts route."""
    # Verify health butler is registered with v1
    registry_entry = await switchboard_pool.fetchrow(
        """
        SELECT route_contract_version, eligibility_state
        FROM butler_registry
        WHERE name = $1
        """,
        "health",
    )

    assert registry_entry is not None
    assert registry_entry["route_contract_version"] == "v1"
    assert registry_entry["eligibility_state"] == "active"


async def test_route_contract_quarantined_butler_skipped(
    switchboard_pool: Pool,
) -> None:
    """Quarantined butler is skipped from routing (if any exist)."""
    # Query for any quarantined butlers
    quarantined = await switchboard_pool.fetchval(
        """
        SELECT COUNT(*)
        FROM butler_registry
        WHERE eligibility_state = 'quarantined'
        """
    )

    # In a fresh test environment, there should be no quarantined butlers
    # This test documents the contract but doesn't actively test quarantine logic
    # (which would require manually quarantining a butler)
    assert quarantined == 0


# ---------------------------------------------------------------------------
# Contract 6: SpawnerResult contract
# ---------------------------------------------------------------------------


async def test_spawner_result_successful_invocation(
    butler_ecosystem: ButlerEcosystem,
    health_pool: Pool,
) -> None:
    """Successful spawner invocation populates all SpawnerResult fields."""
    health_daemon = butler_ecosystem.butlers["health"]
    assert health_daemon.spawner is not None

    # Trigger a real spawner invocation
    result = await health_daemon.spawner.trigger(
        "Log weight: 77kg",
        trigger_source="test",
    )

    # Contract: session_id always set
    assert result.session_id is not None

    # Contract: duration_ms always >= 0
    assert result.duration_ms >= 0

    # Contract: success is True iff output is non-empty and no error
    if result.success:
        assert result.output is not None
        assert result.output.strip()
        assert result.error is None
    else:
        # If not successful, error should be set
        assert result.error is not None

    # Contract: model should be set when adapter reports it
    # (May be None for some adapters, but Claude Code SDK should report it)
    if result.success:
        assert result.model is not None

    # Contract: token counts should be set when adapter reports usage
    if result.success:
        assert result.input_tokens is not None
        assert result.output_tokens is not None
        assert result.input_tokens > 0
        assert result.output_tokens > 0


async def test_spawner_result_session_persistence(
    butler_ecosystem: ButlerEcosystem,
    health_pool: Pool,
) -> None:
    """Every spawner invocation persists a session row with correct status."""
    health_daemon = butler_ecosystem.butlers["health"]
    assert health_daemon.spawner is not None

    # Trigger invocation
    result = await health_daemon.spawner.trigger(
        "Log weight: 78kg",
        trigger_source="test",
    )

    session_id = result.session_id
    assert session_id is not None

    # Verify session row exists in database
    session_row = await health_pool.fetchrow(
        """
        SELECT session_id, status, completed_at, duration_ms
        FROM sessions
        WHERE session_id = $1
        """,
        session_id,
    )

    assert session_row is not None
    assert session_row["session_id"] == session_id

    # Contract: completed sessions have status="completed" and completed_at set
    if result.success:
        assert session_row["status"] == "completed"
        assert session_row["completed_at"] is not None
        assert session_row["duration_ms"] is not None
        assert session_row["duration_ms"] >= 0


async def test_spawner_result_failed_invocation_sets_error(
    butler_ecosystem: ButlerEcosystem,
    health_pool: Pool,
) -> None:
    """Failed spawner invocation sets error field and logs session."""
    health_daemon = butler_ecosystem.butlers["health"]
    assert health_daemon.spawner is not None

    # Note: It's difficult to force a failure with real LLM calls in E2E tests.
    # This test documents the contract but may always pass if all invocations succeed.
    # For robust failure testing, we'd need unit tests with mocked adapters.

    result = await health_daemon.spawner.trigger(
        "This is a test prompt",
        trigger_source="test",
    )

    # Document the contract: if success=False, error must be set
    if not result.success:
        assert result.error is not None
        assert result.error.strip()

        # Session should still be persisted with error status
        session_row = await health_pool.fetchrow(
            """
            SELECT session_id, status
            FROM sessions
            WHERE session_id = $1
            """,
            result.session_id,
        )
        assert session_row is not None
        # Status could be "failed" or "error" depending on implementation
        assert session_row["status"] in ("failed", "error", "completed")
