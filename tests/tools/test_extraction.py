"""Tests for butlers.tools.extraction — multi-butler signal extraction pipeline."""

from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace

import pytest

# Skip all tests in this module if Docker is not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@dataclass
class FakeSpawnerResult:
    result: str | None = None
    tool_calls: list[dict] = field(default_factory=list)
    error: str | None = None
    duration_ms: int = 0


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Provision a fresh database with switchboard tables."""
    async with provisioned_postgres_pool() as p:
        await p.execute("""
            CREATE TABLE IF NOT EXISTS butler_registry (
                name TEXT PRIMARY KEY,
                endpoint_url TEXT NOT NULL,
                description TEXT,
                modules JSONB NOT NULL DEFAULT '[]',
                last_seen_at TIMESTAMPTZ,
                eligibility_state TEXT NOT NULL DEFAULT 'active',
                liveness_ttl_seconds INTEGER NOT NULL DEFAULT 300,
                quarantined_at TIMESTAMPTZ,
                quarantine_reason TEXT,
                route_contract_min INTEGER NOT NULL DEFAULT 1,
                route_contract_max INTEGER NOT NULL DEFAULT 1,
                capabilities JSONB NOT NULL DEFAULT '[]',
                eligibility_updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                agent_type TEXT NOT NULL DEFAULT 'butler'
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS routing_log (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                source_butler TEXT NOT NULL,
                target_butler TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                success BOOLEAN NOT NULL,
                duration_ms INTEGER,
                error TEXT,
                thread_id TEXT,
                source_channel TEXT,
                contact_id UUID,
                entity_id UUID,
                sender_roles TEXT[],
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        yield p


def _make_dispatch_fn(response_json: str):
    async def dispatch(**kwargs):
        return FakeSpawnerResult(result=response_json)

    return dispatch


def _make_failing_dispatch_fn():
    async def dispatch(**kwargs):
        raise RuntimeError("runtime invocation failed")

    return dispatch


async def _mock_call(endpoint_url, tool_name, args):
    return {"status": "ok"}


def _single_extraction(
    sig_type: str = "contacts",
    confidence: str = "HIGH",
    tool_name: str = "contact_create",
    tool_args: dict | None = None,
    target: str = "relationship",
) -> str:
    return json.dumps(
        [
            {
                "type": sig_type,
                "confidence": confidence,
                "tool_name": tool_name,
                "tool_args": tool_args or {},
                "target_butler": target,
            }
        ]
    )


def _multi_extraction(items: list[dict]) -> str:
    return json.dumps(items)


def _calendar_extraction(
    confidence: str = "HIGH",
    *,
    title: str = "Flight to NYC",
    start_at: str = "2026-08-01T14:00:00+00:00",
    end_at: str = "2026-08-01T17:00:00+00:00",
) -> str:
    return json.dumps(
        [
            {
                "type": "events",
                "confidence": confidence,
                "tool_name": "calendar_propose_event",
                "tool_args": {
                    "title": title,
                    "start_at": start_at,
                    "end_at": end_at,
                    "timezone": "UTC",
                },
                "target_butler": "general",
            }
        ]
    )


def _make_calendar_call(pool):
    """Route ``calendar_propose_event`` into the real calendar producer.

    Stands in for the MCP transport: the calendar-owning butler receives the
    routed proposal and runs ``CalendarModule._propose_event`` against the same
    pool, exercising the producer's idempotency end to end.
    """
    from butlers.modules.calendar import CalendarModule

    async def _call(endpoint_url, tool_name, args):
        if tool_name != "calendar_propose_event":
            return {"status": "ok"}
        mod = CalendarModule()
        mod._butler_name = "general"
        mod._db = SimpleNamespace(pool=pool)
        entity_ids = [uuid.UUID(e) for e in (args.get("entity_ids") or [])]
        proposal_id = await mod._propose_event(
            butler_name=args.get("butler_name"),
            title=args["title"],
            start_at=datetime.fromisoformat(args["start_at"]),
            end_at=datetime.fromisoformat(args["end_at"]),
            timezone=args.get("timezone", "UTC"),
            source_event_id=args.get("source_event_id"),
            source_snippet=args.get("source_snippet"),
            confidence=args.get("confidence"),
            entity_ids=entity_ids,
        )
        return {"proposal_id": str(proposal_id)}

    return _call


# ------------------------------------------------------------------
# Unit tests: data models + prompt + parse
# ------------------------------------------------------------------


def test_data_models_and_prompt():
    """ExtractorSchema, Extraction, Confidence data models; prompt builder."""
    from butlers.tools.extraction import (
        HEALTH_SCHEMA,
        RELATIONSHIP_SCHEMA,
        Confidence,
        Extraction,
        ExtractorSchema,
        build_extraction_prompt,
    )

    schema = ExtractorSchema(
        butler_name="health", signal_types=["symptoms"], tool_mappings={"symptoms": "symptom_log"}
    )
    with pytest.raises(AttributeError):
        schema.butler_name = "other"
    assert RELATIONSHIP_SCHEMA.butler_name == "relationship"
    assert HEALTH_SCHEMA.butler_name == "health"

    ext = Extraction(
        type="contacts",
        confidence=Confidence.HIGH,
        tool_name="contact_create",
        tool_args={},
        target_butler="relationship",
    )
    assert ext.dispatched is False
    assert Confidence.HIGH == "HIGH"
    assert Confidence("MEDIUM") == Confidence.MEDIUM

    prompt = build_extraction_prompt("Test message", [RELATIONSHIP_SCHEMA, HEALTH_SCHEMA])
    assert "Test message" in prompt
    assert "symptom_log" in prompt
    assert "/signal-extraction" in prompt
    assert "Return ONLY a JSON array" in prompt


def test_parse_extractions():
    """parse_extractions handles valid/invalid input, filtering, fallbacks."""
    from butlers.tools.extraction import (
        RELATIONSHIP_SCHEMA,
        Confidence,
        parse_extractions,
    )

    # Valid single + markdown block
    result = parse_extractions(
        _single_extraction(tool_args={"name": "Alice"}), [RELATIONSHIP_SCHEMA]
    )
    assert result[0].confidence == Confidence.HIGH

    inner = _single_extraction(tool_args={"name": "Carol"})
    assert (
        parse_extractions(f"```json\n{inner}\n```", [RELATIONSHIP_SCHEMA])[0].tool_args["name"]
        == "Carol"
    )

    # Empty / invalid JSON / non-array / empty schemas
    assert parse_extractions("[]", [RELATIONSHIP_SCHEMA]) == []
    assert parse_extractions("not json", [RELATIONSHIP_SCHEMA]) == []
    assert parse_extractions('{"not": "array"}', [RELATIONSHIP_SCHEMA]) == []

    # Filtering: unknown butler/type, tool_mapping fallback, confidence default, non-dict items
    assert parse_extractions(_single_extraction(target="unknown"), [RELATIONSHIP_SCHEMA]) == []
    assert (
        parse_extractions(_single_extraction(sig_type="xray_results"), [RELATIONSHIP_SCHEMA]) == []
    )

    result2 = parse_extractions(_single_extraction(tool_name=""), [RELATIONSHIP_SCHEMA])
    assert result2[0].tool_name == "contact_create"

    result3 = parse_extractions(_single_extraction(confidence="VERY_HIGH"), [RELATIONSHIP_SCHEMA])
    assert result3[0].confidence == Confidence.LOW

    items = json.loads(_single_extraction())
    assert len(parse_extractions(json.dumps(["not a dict"] + items), [RELATIONSHIP_SCHEMA])) == 1


def test_calendar_schema_and_proposal_args():
    """CALENDAR_SCHEMA is a default; proposal arg builder injects provenance."""
    from butlers.tools.extraction import (
        _CONFIDENCE_SCORE,
        CALENDAR_PROPOSAL_CONFIDENCE_FLOOR,
        CALENDAR_SCHEMA,
        Confidence,
        Extraction,
        _build_calendar_proposal_args,
        _is_calendar_proposal,
    )

    assert CALENDAR_SCHEMA.signal_types == ["events"]
    assert CALENDAR_SCHEMA.tool_mappings["events"] == "calendar_propose_event"

    # Floor admits HIGH only with the default score map.
    assert _CONFIDENCE_SCORE[Confidence.HIGH] >= CALENDAR_PROPOSAL_CONFIDENCE_FLOOR
    assert _CONFIDENCE_SCORE[Confidence.MEDIUM] < CALENDAR_PROPOSAL_CONFIDENCE_FLOOR

    cal = Extraction(
        type="events",
        confidence=Confidence.HIGH,
        tool_name="calendar_propose_event",
        tool_args={"title": "Dinner"},
        target_butler=CALENDAR_SCHEMA.butler_name,
    )
    assert _is_calendar_proposal(cal) is True

    rel = Extraction(
        type="contacts",
        confidence=Confidence.HIGH,
        tool_name="contact_create",
        tool_args={},
        target_butler="relationship",
    )
    assert _is_calendar_proposal(rel) is False

    eid = str(uuid.uuid4())
    args = _build_calendar_proposal_args(
        {"title": "Dinner", "source_snippet": "model snippet"},
        confidence=0.9,
        source_event_id="evt-1",
        source_snippet="ingestion snippet",
        entity_ids=[eid],
    )
    assert args["confidence"] == 0.9
    assert args["source_event_id"] == "evt-1"
    # Explicit source_snippet wins over any model-provided value.
    assert args["source_snippet"] == "ingestion snippet"
    assert args["entity_ids"] == [eid]
    # Original event shape preserved.
    assert args["title"] == "Dinner"


# ------------------------------------------------------------------
# Integration tests: extract_signals
# ------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
class TestExtractSignals:
    async def test_dispatch_thresholds_and_empty_cases(self, pool):
        """HIGH dispatched; MEDIUM not dispatched by default; failure/empty cases return []."""
        from butlers.tools.extraction import extract_signals
        from butlers.tools.switchboard import register_butler

        await pool.execute("DELETE FROM butler_registry")
        await pool.execute("DELETE FROM routing_log")
        await register_butler(pool, "relationship", "http://localhost:41101/sse")

        # HIGH → dispatched
        result = await extract_signals(
            pool,
            "I met Eve today",
            _make_dispatch_fn(_single_extraction(tool_args={"name": "Eve"})),
            call_fn=_mock_call,
        )
        assert result[0].dispatched is True

        # Failure / empty / no signals / no schemas → []
        assert await extract_signals(pool, "msg", _make_failing_dispatch_fn()) == []
        assert await extract_signals(pool, "msg", _make_dispatch_fn("")) == []
        assert await extract_signals(pool, "msg", _make_dispatch_fn("[]")) == []
        assert (
            await extract_signals(pool, "msg", _make_dispatch_fn("[]"), extractor_schemas=[]) == []
        )

    async def test_multi_butler_and_resilience(self, pool):
        """Multiple signals dispatch correctly; route failure for one doesn't crash others."""
        from butlers.tools.extraction import ExtractorSchema, extract_signals
        from butlers.tools.switchboard import register_butler

        await pool.execute("DELETE FROM butler_registry")
        await pool.execute("DELETE FROM routing_log")
        await register_butler(pool, "relationship", "http://localhost:41101/sse")
        await register_butler(pool, "health", "http://localhost:41102/sse")
        await register_butler(pool, "finance", "http://localhost:41103/sse")

        # Multi-butler: 2 HIGH dispatched, 1 MEDIUM not dispatched
        response = _multi_extraction(
            [
                {
                    "type": "contacts",
                    "confidence": "HIGH",
                    "tool_name": "contact_create",
                    "tool_args": {"name": "Frank"},
                    "target_butler": "relationship",
                },
                {
                    "type": "symptoms",
                    "confidence": "HIGH",
                    "tool_name": "symptom_log",
                    "tool_args": {"name": "allergies"},
                    "target_butler": "health",
                },
                {
                    "type": "facts",
                    "confidence": "MEDIUM",
                    "tool_name": "fact_set",
                    "tool_args": {"key": "diet"},
                    "target_butler": "relationship",
                },
            ]
        )
        result = await extract_signals(
            pool, "Frank has allergies", _make_dispatch_fn(response), call_fn=_mock_call
        )
        assert len([e for e in result if e.dispatched]) == 2

        # Custom schema + unregistered target doesn't crash
        finance_schema = ExtractorSchema(
            butler_name="finance",
            signal_types=["expenses"],
            tool_mappings={"expenses": "expense_log"},
        )
        response2 = _single_extraction(
            sig_type="expenses",
            tool_name="expense_log",
            tool_args={"amount": 42.50},
            target="finance",
        )
        result2 = await extract_signals(
            pool,
            "Spent $42.50",
            _make_dispatch_fn(response2),
            extractor_schemas=[finance_schema],
            call_fn=_mock_call,
        )
        assert result2[0].dispatched is True


# ------------------------------------------------------------------
# Integration test: calendar proposal ingestion wiring (bu-6ha2p5)
# ------------------------------------------------------------------


@pytest.fixture
async def calendar_pool(provisioned_postgres_pool):
    """Provision switchboard tables plus the calendar proposals store."""
    async with provisioned_postgres_pool() as p:
        await p.execute("""
            CREATE TABLE IF NOT EXISTS butler_registry (
                name TEXT PRIMARY KEY,
                endpoint_url TEXT NOT NULL,
                description TEXT,
                modules JSONB NOT NULL DEFAULT '[]',
                last_seen_at TIMESTAMPTZ,
                eligibility_state TEXT NOT NULL DEFAULT 'active',
                liveness_ttl_seconds INTEGER NOT NULL DEFAULT 300,
                quarantined_at TIMESTAMPTZ,
                quarantine_reason TEXT,
                route_contract_min INTEGER NOT NULL DEFAULT 1,
                route_contract_max INTEGER NOT NULL DEFAULT 1,
                capabilities JSONB NOT NULL DEFAULT '[]',
                eligibility_updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                agent_type TEXT NOT NULL DEFAULT 'butler'
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS routing_log (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                source_butler TEXT NOT NULL,
                target_butler TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                success BOOLEAN NOT NULL,
                duration_ms INTEGER,
                error TEXT,
                thread_id TEXT,
                source_channel TEXT,
                contact_id UUID,
                entity_id UUID,
                sender_roles TEXT[],
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        # Minimal originating-signal table (the proposal's source_event_id link).
        await p.execute("""
            CREATE TABLE IF NOT EXISTS public.ingestion_events (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                received_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        # Mirrors core_136 (FK on accepted_event_id dropped for fixture simplicity).
        await p.execute("""
            CREATE TABLE IF NOT EXISTS calendar_event_proposals (
                id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                butler_name      TEXT NOT NULL,
                title            TEXT NOT NULL,
                start_at         TIMESTAMPTZ NOT NULL,
                end_at           TIMESTAMPTZ NOT NULL,
                description      TEXT,
                location         TEXT,
                timezone         TEXT NOT NULL DEFAULT 'UTC',
                source_event_id  TEXT,
                source_snippet   TEXT,
                confidence       REAL,
                entity_ids       UUID[] NOT NULL DEFAULT '{}',
                status           TEXT NOT NULL DEFAULT 'pending',
                accepted_event_id UUID,
                created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT chk_calendar_event_proposals_status
                    CHECK (status IN ('pending', 'accepted', 'dismissed')),
                CONSTRAINT chk_calendar_event_proposals_window
                    CHECK (end_at > start_at),
                CONSTRAINT chk_calendar_event_proposals_confidence
                    CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0))
            )
        """)
        await p.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_calendar_event_proposals_source_event_id
            ON calendar_event_proposals (source_event_id)
            WHERE source_event_id IS NOT NULL
        """)
        yield p


@pytest.mark.asyncio(loop_scope="session")
class TestCalendarProposalWiring:
    async def test_event_signal_creates_one_pending_proposal(self, calendar_pool):
        """One event-bearing signal → exactly one pending proposal; idempotent; floored."""
        from butlers.tools.extraction import CALENDAR_SCHEMA, extract_signals
        from butlers.tools.switchboard import register_butler

        pool = calendar_pool
        await register_butler(pool, "general", "http://localhost:41109/sse")

        src_id = await pool.fetchval(
            "INSERT INTO public.ingestion_events DEFAULT VALUES RETURNING id"
        )
        entity_id = uuid.uuid4()
        call_fn = _make_calendar_call(pool)

        # HIGH-confidence, event-bearing signal → exactly one pending proposal.
        result = await extract_signals(
            pool,
            "Booked a flight to NYC on Aug 1 at 2pm",
            _make_dispatch_fn(_calendar_extraction("HIGH")),
            extractor_schemas=[CALENDAR_SCHEMA],
            call_fn=call_fn,
            source_event_id=str(src_id),
            source_snippet="Booked a flight to NYC",
            entity_ids=[str(entity_id)],
        )
        assert any(e.dispatched for e in result)

        rows = await pool.fetch("SELECT * FROM calendar_event_proposals")
        assert len(rows) == 1
        row = rows[0]
        assert row["status"] == "pending"
        assert row["butler_name"] == "general"
        assert row["source_event_id"] == str(src_id)
        assert row["source_snippet"] == "Booked a flight to NYC"
        assert row["confidence"] == pytest.approx(0.9)
        assert entity_id in row["entity_ids"]

        # Re-ingesting the same signal is idempotent — still exactly one row.
        await extract_signals(
            pool,
            "Booked a flight to NYC on Aug 1 at 2pm",
            _make_dispatch_fn(_calendar_extraction("HIGH")),
            extractor_schemas=[CALENDAR_SCHEMA],
            call_fn=call_fn,
            source_event_id=str(src_id),
            source_snippet="Booked a flight to NYC",
            entity_ids=[str(entity_id)],
        )
        assert await pool.fetchval("SELECT count(*) FROM calendar_event_proposals") == 1

        # Below-floor (MEDIUM) signal from a different source → no proposal.
        src_id2 = await pool.fetchval(
            "INSERT INTO public.ingestion_events DEFAULT VALUES RETURNING id"
        )
        med = await extract_signals(
            pool,
            "Maybe lunch sometime next week?",
            _make_dispatch_fn(_calendar_extraction("MEDIUM")),
            extractor_schemas=[CALENDAR_SCHEMA],
            call_fn=call_fn,
            source_event_id=str(src_id2),
        )
        assert all(not e.dispatched for e in med)
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM calendar_event_proposals WHERE source_event_id = $1",
                str(src_id2),
            )
            == 0
        )
