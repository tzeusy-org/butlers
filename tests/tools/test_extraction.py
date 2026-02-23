"""Tests for butlers.tools.extraction — multi-butler signal extraction pipeline."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field

import pytest

# Skip all tests in this module if Docker is not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@dataclass
class FakeSpawnerResult:
    """Fake LLM CLI spawner result for testing."""

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
                registered_at TIMESTAMPTZ NOT NULL DEFAULT now()
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
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)

        yield p


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_dispatch_fn(response_json: str):
    """Create a mock dispatch_fn returning given JSON."""

    async def dispatch(**kwargs):
        return FakeSpawnerResult(result=response_json)

    return dispatch


def _make_failing_dispatch_fn():
    """Create a mock dispatch_fn that raises."""

    async def dispatch(**kwargs):
        raise RuntimeError("runtime invocation failed")

    return dispatch


async def _mock_call(endpoint_url, tool_name, args):
    """Mock call_fn for route dispatch that always succeeds."""
    return {"status": "ok"}


def _single_extraction(
    sig_type: str = "contacts",
    confidence: str = "HIGH",
    tool_name: str = "contact_create",
    tool_args: dict | None = None,
    target: str = "relationship",
) -> str:
    """Build a single-extraction JSON response string."""
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
    """Build a multi-extraction JSON response string."""
    return json.dumps(items)


# ------------------------------------------------------------------
# ExtractorSchema
# ------------------------------------------------------------------


class TestExtractorSchema:
    """Tests for the ExtractorSchema data class."""

    def test_schema_creation(self):
        from butlers.tools.extraction import ExtractorSchema

        schema = ExtractorSchema(
            butler_name="health",
            signal_types=["symptoms", "medications"],
            tool_mappings={
                "symptoms": "symptom_log",
                "medications": "medication_add",
            },
        )
        assert schema.butler_name == "health"
        assert "symptoms" in schema.signal_types
        assert schema.tool_mappings["symptoms"] == "symptom_log"

    def test_schema_is_frozen(self):
        from butlers.tools.extraction import ExtractorSchema

        schema = ExtractorSchema(
            butler_name="test",
            signal_types=["a"],
            tool_mappings={"a": "tool_a"},
        )
        with pytest.raises(AttributeError):
            schema.butler_name = "other"

    def test_default_schemas_exist(self):
        from butlers.tools.extraction import (
            HEALTH_SCHEMA,
            RELATIONSHIP_SCHEMA,
        )

        assert RELATIONSHIP_SCHEMA.butler_name == "relationship"
        assert "contacts" in RELATIONSHIP_SCHEMA.signal_types
        assert "interactions" in RELATIONSHIP_SCHEMA.signal_types

        assert HEALTH_SCHEMA.butler_name == "health"
        assert "symptoms" in HEALTH_SCHEMA.signal_types
        assert "medications" in HEALTH_SCHEMA.signal_types


# ------------------------------------------------------------------
# Extraction data class
# ------------------------------------------------------------------


class TestExtraction:
    """Tests for the Extraction data class."""

    def test_extraction_creation(self):
        from butlers.tools.extraction import Confidence, Extraction

        ext = Extraction(
            type="contacts",
            confidence=Confidence.HIGH,
            tool_name="contact_create",
            tool_args={"name": "Alice"},
            target_butler="relationship",
        )
        assert ext.type == "contacts"
        assert ext.confidence == Confidence.HIGH
        assert ext.dispatched is False

    def test_extraction_dispatched_default_false(self):
        from butlers.tools.extraction import Confidence, Extraction

        ext = Extraction(
            type="symptoms",
            confidence=Confidence.MEDIUM,
            tool_name="symptom_log",
            tool_args={},
            target_butler="health",
        )
        assert ext.dispatched is False


# ------------------------------------------------------------------
# Confidence enum
# ------------------------------------------------------------------


class TestConfidence:
    """Tests for the Confidence enum."""

    def test_confidence_values(self):
        from butlers.tools.extraction import Confidence

        assert Confidence.HIGH == "HIGH"
        assert Confidence.MEDIUM == "MEDIUM"
        assert Confidence.LOW == "LOW"

    def test_confidence_from_string(self):
        from butlers.tools.extraction import Confidence

        assert Confidence("HIGH") == Confidence.HIGH
        assert Confidence("MEDIUM") == Confidence.MEDIUM
        assert Confidence("LOW") == Confidence.LOW


# ------------------------------------------------------------------
# build_extraction_prompt
# ------------------------------------------------------------------


class TestBuildExtractionPrompt:
    """Tests for the prompt builder."""

    def test_prompt_includes_all_schemas(self):
        from butlers.tools.extraction import (
            HEALTH_SCHEMA,
            RELATIONSHIP_SCHEMA,
            build_extraction_prompt,
        )

        prompt = build_extraction_prompt("Hello", [RELATIONSHIP_SCHEMA, HEALTH_SCHEMA])
        assert "relationship" in prompt
        assert "health" in prompt
        assert "contacts" in prompt
        assert "symptoms" in prompt
        assert "Hello" in prompt

    def test_prompt_includes_message(self):
        from butlers.tools.extraction import (
            RELATIONSHIP_SCHEMA,
            build_extraction_prompt,
        )

        prompt = build_extraction_prompt("Test message 123", [RELATIONSHIP_SCHEMA])
        assert "Test message 123" in prompt

    def test_prompt_includes_tool_mappings(self):
        from butlers.tools.extraction import (
            HEALTH_SCHEMA,
            build_extraction_prompt,
        )

        prompt = build_extraction_prompt("test", [HEALTH_SCHEMA])
        assert "symptom_log" in prompt
        assert "medication_add" in prompt


# ------------------------------------------------------------------
# parse_extractions
# ------------------------------------------------------------------


class TestParseExtractions:
    """Tests for the response parser."""

    def test_parse_valid_json_array(self):
        from butlers.tools.extraction import (
            RELATIONSHIP_SCHEMA,
            Confidence,
            parse_extractions,
        )

        raw = _single_extraction(tool_args={"name": "Alice"})
        result = parse_extractions(raw, [RELATIONSHIP_SCHEMA])
        assert len(result) == 1
        assert result[0].type == "contacts"
        assert result[0].confidence == Confidence.HIGH
        assert result[0].target_butler == "relationship"
        assert result[0].tool_args == {"name": "Alice"}

    def test_parse_multiple_extractions(self):
        from butlers.tools.extraction import (
            HEALTH_SCHEMA,
            RELATIONSHIP_SCHEMA,
            parse_extractions,
        )

        raw = _multi_extraction(
            [
                {
                    "type": "contacts",
                    "confidence": "HIGH",
                    "tool_name": "contact_create",
                    "tool_args": {"name": "Bob"},
                    "target_butler": "relationship",
                },
                {
                    "type": "symptoms",
                    "confidence": "MEDIUM",
                    "tool_name": "symptom_log",
                    "tool_args": {"name": "headache", "severity": 5},
                    "target_butler": "health",
                },
            ]
        )
        result = parse_extractions(raw, [RELATIONSHIP_SCHEMA, HEALTH_SCHEMA])
        assert len(result) == 2
        butlers = {e.target_butler for e in result}
        assert butlers == {"relationship", "health"}

    def test_parse_markdown_code_block(self):
        from butlers.tools.extraction import (
            RELATIONSHIP_SCHEMA,
            parse_extractions,
        )

        inner = _single_extraction(tool_args={"name": "Carol"})
        raw = f"```json\n{inner}\n```"
        result = parse_extractions(raw, [RELATIONSHIP_SCHEMA])
        assert len(result) == 1
        assert result[0].tool_args["name"] == "Carol"

    def test_parse_empty_array(self):
        from butlers.tools.extraction import (
            RELATIONSHIP_SCHEMA,
            parse_extractions,
        )

        result = parse_extractions("[]", [RELATIONSHIP_SCHEMA])
        assert result == []

    def test_parse_invalid_json(self):
        from butlers.tools.extraction import (
            RELATIONSHIP_SCHEMA,
            parse_extractions,
        )

        result = parse_extractions("not json at all", [RELATIONSHIP_SCHEMA])
        assert result == []

    def test_parse_drops_unknown_butler(self):
        from butlers.tools.extraction import (
            RELATIONSHIP_SCHEMA,
            parse_extractions,
        )

        raw = _single_extraction(target="unknown_butler")
        result = parse_extractions(raw, [RELATIONSHIP_SCHEMA])
        assert result == []

    def test_parse_drops_unknown_signal_type(self):
        from butlers.tools.extraction import (
            RELATIONSHIP_SCHEMA,
            parse_extractions,
        )

        raw = _single_extraction(sig_type="xray_results")
        result = parse_extractions(raw, [RELATIONSHIP_SCHEMA])
        assert result == []

    def test_parse_uses_tool_mapping_when_tool_name_missing(self):
        from butlers.tools.extraction import (
            RELATIONSHIP_SCHEMA,
            parse_extractions,
        )

        raw = _single_extraction(tool_name="", tool_args={"name": "Dan"})
        result = parse_extractions(raw, [RELATIONSHIP_SCHEMA])
        assert len(result) == 1
        assert result[0].tool_name == "contact_create"

    def test_parse_invalid_confidence_defaults_to_low(self):
        from butlers.tools.extraction import (
            RELATIONSHIP_SCHEMA,
            Confidence,
            parse_extractions,
        )

        raw = _single_extraction(confidence="VERY_HIGH")
        result = parse_extractions(raw, [RELATIONSHIP_SCHEMA])
        assert len(result) == 1
        assert result[0].confidence == Confidence.LOW

    def test_parse_non_array_response(self):
        from butlers.tools.extraction import (
            RELATIONSHIP_SCHEMA,
            parse_extractions,
        )

        result = parse_extractions('{"not": "an array"}', [RELATIONSHIP_SCHEMA])
        assert result == []

    def test_parse_skips_non_dict_items(self):
        from butlers.tools.extraction import (
            RELATIONSHIP_SCHEMA,
            parse_extractions,
        )

        items = json.loads(_single_extraction())
        raw = json.dumps(["not a dict"] + items)
        result = parse_extractions(raw, [RELATIONSHIP_SCHEMA])
        assert len(result) == 1

    def test_parse_handles_non_dict_tool_args(self):
        from butlers.tools.extraction import (
            RELATIONSHIP_SCHEMA,
            parse_extractions,
        )

        raw = json.dumps(
            [
                {
                    "type": "contacts",
                    "confidence": "HIGH",
                    "tool_name": "contact_create",
                    "tool_args": "invalid",
                    "target_butler": "relationship",
                }
            ]
        )
        result = parse_extractions(raw, [RELATIONSHIP_SCHEMA])
        assert len(result) == 1
        assert result[0].tool_args == {}


# ------------------------------------------------------------------
# extract_signals (integration with mocked CC)
# ------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
class TestExtractSignals:
    """Tests for the main extract_signals pipeline."""

    async def test_high_confidence_dispatched(self, pool):
        """HIGH confidence extractions are dispatched."""
        from butlers.tools.extraction import Confidence, extract_signals
        from butlers.tools.switchboard import register_butler

        await pool.execute("DELETE FROM butler_registry")
        await pool.execute("DELETE FROM routing_log")
        await register_butler(pool, "relationship", "http://localhost:40101/sse")

        response = _single_extraction(tool_args={"name": "Eve"})
        result = await extract_signals(
            pool,
            "I met Eve today",
            _make_dispatch_fn(response),
            call_fn=_mock_call,
        )

        assert len(result) == 1
        assert result[0].type == "contacts"
        assert result[0].confidence == Confidence.HIGH
        assert result[0].dispatched is True

    async def test_medium_not_dispatched_by_default(self, pool):
        """MEDIUM confidence not dispatched when threshold is HIGH."""
        from butlers.tools.extraction import Confidence, extract_signals
        from butlers.tools.switchboard import register_butler

        await pool.execute("DELETE FROM butler_registry")
        await pool.execute("DELETE FROM routing_log")
        await register_butler(pool, "health", "http://localhost:40102/sse")

        response = _single_extraction(
            sig_type="symptoms",
            confidence="MEDIUM",
            tool_name="symptom_log",
            tool_args={"name": "headache", "severity": 3},
            target="health",
        )
        result = await extract_signals(
            pool,
            "I have a slight headache",
            _make_dispatch_fn(response),
            call_fn=_mock_call,
        )

        assert len(result) == 1
        assert result[0].confidence == Confidence.MEDIUM
        assert result[0].dispatched is False

    async def test_low_confidence_not_dispatched(self, pool):
        """LOW confidence extractions are never dispatched."""
        from butlers.tools.extraction import Confidence, extract_signals
        from butlers.tools.switchboard import register_butler

        await pool.execute("DELETE FROM butler_registry")
        await pool.execute("DELETE FROM routing_log")
        await register_butler(pool, "relationship", "http://localhost:40101/sse")

        response = _single_extraction(
            sig_type="sentiments",
            confidence="LOW",
            tool_name="note_create",
            tool_args={"content": "seemed happy"},
        )
        result = await extract_signals(
            pool,
            "She seemed happy",
            _make_dispatch_fn(response),
            call_fn=_mock_call,
        )

        assert len(result) == 1
        assert result[0].confidence == Confidence.LOW
        assert result[0].dispatched is False

    async def test_multi_butler_multi_signal(self, pool):
        """Multiple signals across multiple butlers."""
        from butlers.tools.extraction import extract_signals
        from butlers.tools.switchboard import register_butler

        await pool.execute("DELETE FROM butler_registry")
        await pool.execute("DELETE FROM routing_log")
        await register_butler(pool, "relationship", "http://localhost:40101/sse")
        await register_butler(pool, "health", "http://localhost:40102/sse")

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
                    "type": "interactions",
                    "confidence": "HIGH",
                    "tool_name": "interaction_log",
                    "tool_args": {"type": "lunch"},
                    "target_butler": "relationship",
                },
                {
                    "type": "symptoms",
                    "confidence": "HIGH",
                    "tool_name": "symptom_log",
                    "tool_args": {"name": "allergies", "severity": 4},
                    "target_butler": "health",
                },
                {
                    "type": "facts",
                    "confidence": "MEDIUM",
                    "tool_name": "fact_set",
                    "tool_args": {"key": "diet", "value": "vegetarian"},
                    "target_butler": "relationship",
                },
            ]
        )

        msg = "Had lunch with Frank. His allergies are acting up. He's vegetarian now."
        result = await extract_signals(
            pool,
            msg,
            _make_dispatch_fn(response),
            call_fn=_mock_call,
        )

        assert len(result) == 4
        dispatched = [e for e in result if e.dispatched]
        not_dispatched = [e for e in result if not e.dispatched]
        assert len(dispatched) == 3  # 3 HIGH
        assert len(not_dispatched) == 1  # 1 MEDIUM

        dispatched_butlers = {e.target_butler for e in dispatched}
        assert dispatched_butlers == {"relationship", "health"}

    async def test_cc_failure_returns_empty(self, pool):
        """If runtime invocation fails, return empty list."""
        from butlers.tools.extraction import extract_signals

        result = await extract_signals(
            pool,
            "any message",
            _make_failing_dispatch_fn(),
        )
        assert result == []

    async def test_empty_response_returns_empty(self, pool):
        """If CC returns empty string, return empty list."""
        from butlers.tools.extraction import extract_signals

        result = await extract_signals(
            pool,
            "any message",
            _make_dispatch_fn(""),
        )
        assert result == []

    async def test_no_signals_returns_empty(self, pool):
        """If CC finds no signals, return empty list."""
        from butlers.tools.extraction import extract_signals

        result = await extract_signals(
            pool,
            "What's the weather today?",
            _make_dispatch_fn("[]"),
        )
        assert result == []

    async def test_empty_schemas_returns_empty(self, pool):
        """If no extractor schemas provided, return empty list."""
        from butlers.tools.extraction import extract_signals

        result = await extract_signals(
            pool,
            "any message",
            _make_dispatch_fn("[]"),
            extractor_schemas=[],
        )
        assert result == []

    async def test_custom_confidence_threshold_medium(self, pool):
        """MEDIUM threshold dispatches both HIGH and MEDIUM."""
        from butlers.tools.extraction import Confidence, extract_signals
        from butlers.tools.switchboard import register_butler

        await pool.execute("DELETE FROM butler_registry")
        await pool.execute("DELETE FROM routing_log")
        await register_butler(pool, "relationship", "http://localhost:40101/sse")

        response = _multi_extraction(
            [
                {
                    "type": "contacts",
                    "confidence": "HIGH",
                    "tool_name": "contact_create",
                    "tool_args": {"name": "Grace"},
                    "target_butler": "relationship",
                },
                {
                    "type": "sentiments",
                    "confidence": "MEDIUM",
                    "tool_name": "note_create",
                    "tool_args": {"content": "excited"},
                    "target_butler": "relationship",
                },
            ]
        )

        result = await extract_signals(
            pool,
            "Grace was so excited about her promotion!",
            _make_dispatch_fn(response),
            confidence_threshold=Confidence.MEDIUM,
            call_fn=_mock_call,
        )

        assert len(result) == 2
        dispatched = [e for e in result if e.dispatched]
        assert len(dispatched) == 2


# ------------------------------------------------------------------
# Extraction logging in routing_log
# ------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
class TestExtractionLogging:
    """Tests for extraction audit logging."""

    async def test_dispatched_logged_as_success(self, pool):
        """Dispatched extractions create success routing_log entries."""
        from butlers.tools.extraction import extract_signals
        from butlers.tools.switchboard import register_butler

        await pool.execute("DELETE FROM butler_registry")
        await pool.execute("DELETE FROM routing_log")
        await register_butler(pool, "relationship", "http://localhost:40101/sse")

        response = _single_extraction(tool_args={"name": "Hank"})
        await extract_signals(
            pool,
            "Met Hank",
            _make_dispatch_fn(response),
            call_fn=_mock_call,
        )

        rows = await pool.fetch(
            "SELECT * FROM routing_log WHERE source_butler = 'switchboard:extractor'"
        )
        # route() logs one entry and _log_extractions logs another
        success_entries = [r for r in rows if r["tool_name"] == "contact_create" and r["success"]]
        assert len(success_entries) >= 1

    async def test_below_threshold_logged_with_error(self, pool):
        """Below-threshold extractions logged with error message."""
        from butlers.tools.extraction import extract_signals
        from butlers.tools.switchboard import register_butler

        await pool.execute("DELETE FROM butler_registry")
        await pool.execute("DELETE FROM routing_log")
        await register_butler(pool, "health", "http://localhost:40102/sse")

        response = _single_extraction(
            sig_type="symptoms",
            confidence="LOW",
            tool_name="symptom_log",
            tool_args={"name": "cough"},
            target="health",
        )
        await extract_signals(
            pool,
            "Slight cough",
            _make_dispatch_fn(response),
            call_fn=_mock_call,
        )

        rows = await pool.fetch(
            "SELECT * FROM routing_log "
            "WHERE source_butler = 'switchboard:extractor' "
            "AND success = false"
        )
        assert len(rows) >= 1
        assert "Below threshold" in rows[0]["error"]
        assert "LOW" in rows[0]["error"]


# ------------------------------------------------------------------
# Dispatch failure handling
# ------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
class TestDispatchFailureHandling:
    """Tests that dispatch failures are handled gracefully."""

    async def test_route_failure_no_crash(self, pool):
        """If route() fails for one, others still process."""
        from butlers.tools.extraction import extract_signals
        from butlers.tools.switchboard import register_butler

        await pool.execute("DELETE FROM butler_registry")
        await pool.execute("DELETE FROM routing_log")
        # Register relationship but NOT health
        await register_butler(pool, "relationship", "http://localhost:40101/sse")

        response = _multi_extraction(
            [
                {
                    "type": "contacts",
                    "confidence": "HIGH",
                    "tool_name": "contact_create",
                    "tool_args": {"name": "Jack"},
                    "target_butler": "relationship",
                },
                {
                    "type": "symptoms",
                    "confidence": "HIGH",
                    "tool_name": "symptom_log",
                    "tool_args": {"name": "fever"},
                    "target_butler": "health",
                },
            ]
        )

        result = await extract_signals(
            pool,
            "Jack has a fever",
            _make_dispatch_fn(response),
            call_fn=_mock_call,
        )

        assert len(result) == 2
        rel_ext = [e for e in result if e.target_butler == "relationship"]
        assert len(rel_ext) == 1
        assert rel_ext[0].dispatched is True

    async def test_call_fn_exception_handled(self, pool):
        """If call_fn raises, pipeline continues."""
        from butlers.tools.extraction import extract_signals
        from butlers.tools.switchboard import register_butler

        await pool.execute("DELETE FROM butler_registry")
        await pool.execute("DELETE FROM routing_log")
        await register_butler(pool, "relationship", "http://localhost:40101/sse")

        response = _single_extraction(tool_args={"name": "Kate"})

        async def failing_call(endpoint_url, tool_name, args):
            raise ConnectionError("Connection refused")

        result = await extract_signals(
            pool,
            "Met Kate",
            _make_dispatch_fn(response),
            call_fn=failing_call,
        )

        # Extraction found; route catches exception internally,
        # returns error dict but doesn't raise — so dispatched=True
        # because the route() function completed successfully.
        assert len(result) == 1
        assert result[0].dispatched is True


# ------------------------------------------------------------------
# Custom ExtractorSchema
# ------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
class TestCustomSchemas:
    """Tests with custom extractor schemas."""

    async def test_custom_schema_single_butler(self, pool):
        """Custom schema for a single butler works."""
        from butlers.tools.extraction import (
            ExtractorSchema,
            extract_signals,
        )
        from butlers.tools.switchboard import register_butler

        await pool.execute("DELETE FROM butler_registry")
        await pool.execute("DELETE FROM routing_log")
        await register_butler(pool, "finance", "http://localhost:40103/sse")

        finance_schema = ExtractorSchema(
            butler_name="finance",
            signal_types=["expenses", "income"],
            tool_mappings={
                "expenses": "expense_log",
                "income": "income_log",
            },
        )

        response = _single_extraction(
            sig_type="expenses",
            tool_name="expense_log",
            tool_args={"amount": 42.50, "description": "lunch"},
            target="finance",
        )

        result = await extract_signals(
            pool,
            "Spent $42.50 on lunch",
            _make_dispatch_fn(response),
            extractor_schemas=[finance_schema],
            call_fn=_mock_call,
        )

        assert len(result) == 1
        assert result[0].target_butler == "finance"
        assert result[0].tool_name == "expense_log"
        assert result[0].dispatched is True

    async def test_schema_rejects_unregistered_type(self, pool):
        """Extractions for types not in schema are dropped."""
        from butlers.tools.extraction import (
            ExtractorSchema,
            extract_signals,
        )
        from butlers.tools.switchboard import register_butler

        await pool.execute("DELETE FROM butler_registry")
        await pool.execute("DELETE FROM routing_log")
        await register_butler(pool, "finance", "http://localhost:40103/sse")

        finance_schema = ExtractorSchema(
            butler_name="finance",
            signal_types=["expenses"],
            tool_mappings={"expenses": "expense_log"},
        )

        # CC returns "income" which is NOT in the schema
        response = _single_extraction(
            sig_type="income",
            tool_name="income_log",
            tool_args={"amount": 1000},
            target="finance",
        )

        result = await extract_signals(
            pool,
            "Received $1000",
            _make_dispatch_fn(response),
            extractor_schemas=[finance_schema],
            call_fn=_mock_call,
        )

        assert result == []
