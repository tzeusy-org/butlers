"""Unit tests for envelope factory functions and the scenario corpus.

These tests run without the E2E ecosystem — no Docker, no LLM calls.
They verify:
- email_envelope() and telegram_envelope() produce valid ingest.v1 dicts
- Thread-ID propagation in email envelopes
- Deterministic idempotency key generation
- Scenario dataclass structure and tag filtering
- ALL_SCENARIOS count is in the expected range (20-30 scenarios)

Acceptance criteria verified:
1. email_envelope() produces valid ingest.v1 with source.channel='email'
2. telegram_envelope() produces valid ingest.v1 with source.channel='telegram'
3. email_envelope with thread_id sets event.external_thread_id
4. Unified Scenario dataclass has all required fields
5. 20-30 scenarios authored
6. --scenarios=smoke filtering works via get_scenarios_by_tags
7. Against specs/ingress-injection/spec.md envelope factory sections
"""

from __future__ import annotations

from tests.e2e.envelopes import email_envelope, telegram_envelope
from tests.e2e.scenarios import (
    ALL_SCENARIOS,
    DbAssertion,
    Scenario,
    get_scenarios_by_tags,
)

# ---------------------------------------------------------------------------
# email_envelope() tests
# ---------------------------------------------------------------------------


class TestEmailEnvelope:
    """Tests for the email_envelope() factory function."""

    def test_schema_version(self) -> None:
        env = email_envelope(
            sender="alice@example.com",
            subject="Hello",
            body="Test body",
        )
        assert env["schema_version"] == "ingest.v1"

    def test_source_channel_is_email(self) -> None:
        env = email_envelope(
            sender="alice@example.com",
            subject="Hello",
            body="Test body",
        )
        assert env["source"]["channel"] == "email"

    def test_source_provider_is_gmail(self) -> None:
        env = email_envelope(
            sender="alice@example.com",
            subject="Hello",
            body="Test body",
        )
        assert env["source"]["provider"] == "gmail"

    def test_source_endpoint_identity_present(self) -> None:
        env = email_envelope(
            sender="alice@example.com",
            subject="Hello",
            body="Test body",
        )
        assert env["source"]["endpoint_identity"]  # non-empty

    def test_sender_identity_is_email_address(self) -> None:
        env = email_envelope(
            sender="alice@example.com",
            subject="Meeting",
            body="Please attend",
        )
        assert env["sender"]["identity"] == "alice@example.com"

    def test_normalized_text_contains_subject_and_body(self) -> None:
        env = email_envelope(
            sender="alice@example.com",
            subject="Team lunch Thursday",
            body="Let's do noon at the usual place",
        )
        text = env["payload"]["normalized_text"]
        assert "Team lunch Thursday" in text
        assert "Let's do noon" in text

    def test_payload_raw_is_dict(self) -> None:
        env = email_envelope(
            sender="alice@example.com",
            subject="Hello",
            body="Test body",
        )
        assert isinstance(env["payload"]["raw"], dict)

    def test_payload_raw_has_headers(self) -> None:
        env = email_envelope(
            sender="alice@example.com",
            subject="Meeting invite",
            body="Come join us",
        )
        raw = env["payload"]["raw"]
        headers = {h["name"]: h["value"] for h in raw["payload"]["headers"]}
        assert headers["From"] == "alice@example.com"
        assert headers["Subject"] == "Meeting invite"

    def test_idempotency_key_is_deterministic(self) -> None:
        """Same inputs produce the same idempotency key."""
        env1 = email_envelope(
            sender="alice@example.com",
            subject="Hello",
            body="Test body",
        )
        env2 = email_envelope(
            sender="alice@example.com",
            subject="Hello",
            body="Test body",
        )
        assert env1["control"]["idempotency_key"] == env2["control"]["idempotency_key"]

    def test_idempotency_key_differs_for_different_inputs(self) -> None:
        """Different inputs produce different idempotency keys."""
        env1 = email_envelope(
            sender="alice@example.com",
            subject="Hello",
            body="Body A",
        )
        env2 = email_envelope(
            sender="alice@example.com",
            subject="Hello",
            body="Body B",  # different body
        )
        assert env1["control"]["idempotency_key"] != env2["control"]["idempotency_key"]

    def test_idempotency_key_starts_with_email_prefix(self) -> None:
        env = email_envelope(
            sender="alice@example.com",
            subject="Hello",
            body="Test body",
        )
        assert env["control"]["idempotency_key"].startswith("email:")

    def test_no_thread_id_no_external_thread_id(self) -> None:
        """Without thread_id, event.external_thread_id should not be set."""
        env = email_envelope(
            sender="alice@example.com",
            subject="Hello",
            body="Test body",
        )
        assert "external_thread_id" not in env["event"]

    def test_thread_id_sets_external_thread_id(self) -> None:
        """With thread_id, event.external_thread_id should be set."""
        env = email_envelope(
            sender="alice@example.com",
            subject="Re: Meeting",
            body="Confirmed!",
            thread_id="thread-abc-123",
        )
        assert env["event"]["external_thread_id"] == "thread-abc-123"

    def test_event_has_observed_at(self) -> None:
        env = email_envelope(
            sender="alice@example.com",
            subject="Hello",
            body="Test body",
        )
        assert env["event"]["observed_at"]  # non-empty RFC3339 timestamp

    def test_event_external_event_id_present(self) -> None:
        env = email_envelope(
            sender="alice@example.com",
            subject="Hello",
            body="Test body",
        )
        assert env["event"]["external_event_id"]  # non-empty

    def test_custom_endpoint_identity(self) -> None:
        env = email_envelope(
            sender="alice@example.com",
            subject="Hello",
            body="Test body",
            endpoint_identity="gmail:myaccount@gmail.com",
        )
        assert env["source"]["endpoint_identity"] == "gmail:myaccount@gmail.com"

    def test_control_policy_tier_default(self) -> None:
        env = email_envelope(
            sender="alice@example.com",
            subject="Hello",
            body="Test body",
        )
        assert env["control"]["policy_tier"] == "default"

    def test_control_policy_tier_override(self) -> None:
        env = email_envelope(
            sender="alice@example.com",
            subject="Urgent!",
            body="Critical message",
            policy_tier="high_priority",
        )
        assert env["control"]["policy_tier"] == "high_priority"


# ---------------------------------------------------------------------------
# telegram_envelope() tests
# ---------------------------------------------------------------------------


class TestTelegramEnvelope:
    """Tests for the telegram_envelope() factory function."""

    def test_schema_version(self) -> None:
        env = telegram_envelope(chat_id=12345, text="Hello")
        assert env["schema_version"] == "ingest.v1"

    def test_source_channel_is_telegram(self) -> None:
        env = telegram_envelope(chat_id=12345, text="Hello")
        assert env["source"]["channel"] == "telegram"

    def test_source_provider_is_telegram(self) -> None:
        env = telegram_envelope(chat_id=12345, text="Hello")
        assert env["source"]["provider"] == "telegram"

    def test_source_endpoint_identity_present(self) -> None:
        env = telegram_envelope(chat_id=12345, text="Hello")
        assert env["source"]["endpoint_identity"]

    def test_sender_identity_is_numeric(self) -> None:
        env = telegram_envelope(chat_id=12345, text="Hello", from_user="test-user")
        # Sender identity is a numeric user_id string
        assert env["sender"]["identity"].isdigit()

    def test_normalized_text_matches_input(self) -> None:
        env = telegram_envelope(chat_id=12345, text="I ran 5km this morning")
        assert env["payload"]["normalized_text"] == "I ran 5km this morning"

    def test_payload_raw_is_dict(self) -> None:
        env = telegram_envelope(chat_id=12345, text="Hello")
        assert isinstance(env["payload"]["raw"], dict)

    def test_payload_raw_has_update_structure(self) -> None:
        env = telegram_envelope(chat_id=12345, text="Hello from test")
        raw = env["payload"]["raw"]
        assert "update_id" in raw
        assert "message" in raw
        assert raw["message"]["text"] == "Hello from test"

    def test_payload_raw_has_chat_id(self) -> None:
        env = telegram_envelope(chat_id=12345, text="Hello")
        raw = env["payload"]["raw"]
        assert raw["message"]["chat"]["id"] == 12345

    def test_payload_raw_has_from_user(self) -> None:
        env = telegram_envelope(chat_id=12345, text="Hello", from_user="alice")
        raw = env["payload"]["raw"]
        assert raw["message"]["from"]["username"] == "alice"

    def test_idempotency_key_format(self) -> None:
        """Idempotency key follows tg:<chat_id>:<message_id> pattern."""
        env = telegram_envelope(chat_id=12345, text="Hello", message_id=999)
        key = env["control"]["idempotency_key"]
        assert key == "tg:12345:999"

    def test_idempotency_key_with_generated_message_id(self) -> None:
        """When message_id is not provided, key still has tg:<chat_id>: prefix."""
        env = telegram_envelope(chat_id=12345, text="Hello")
        key = env["control"]["idempotency_key"]
        assert key.startswith("tg:12345:")

    def test_idempotency_key_is_deterministic(self) -> None:
        """Same inputs produce the same idempotency key."""
        env1 = telegram_envelope(chat_id=12345, text="Hello")
        env2 = telegram_envelope(chat_id=12345, text="Hello")
        assert env1["control"]["idempotency_key"] == env2["control"]["idempotency_key"]

    def test_idempotency_key_differs_for_different_text(self) -> None:
        env1 = telegram_envelope(chat_id=12345, text="Message A")
        env2 = telegram_envelope(chat_id=12345, text="Message B")
        assert env1["control"]["idempotency_key"] != env2["control"]["idempotency_key"]

    def test_explicit_message_id_used_in_key(self) -> None:
        env = telegram_envelope(chat_id=99999, text="Test", message_id=42)
        assert env["control"]["idempotency_key"] == "tg:99999:42"

    def test_external_thread_id_set(self) -> None:
        """Telegram envelopes always have external_thread_id for reply targeting."""
        env = telegram_envelope(chat_id=12345, text="Hello", message_id=100)
        assert env["event"]["external_thread_id"] == "12345:100"

    def test_event_observed_at_present(self) -> None:
        env = telegram_envelope(chat_id=12345, text="Hello")
        assert env["event"]["observed_at"]

    def test_event_external_event_id_present(self) -> None:
        env = telegram_envelope(chat_id=12345, text="Hello")
        assert env["event"]["external_event_id"]

    def test_group_chat_negative_id(self) -> None:
        """Negative chat IDs (group chats) are supported."""
        env = telegram_envelope(chat_id=-100123456, text="Group message")
        assert env["source"]["channel"] == "telegram"
        raw = env["payload"]["raw"]
        assert raw["message"]["chat"]["id"] == -100123456

    def test_custom_endpoint_identity(self) -> None:
        env = telegram_envelope(chat_id=12345, text="Hello", endpoint_identity="mybot")
        assert env["source"]["endpoint_identity"] == "mybot"


# ---------------------------------------------------------------------------
# Ingest.v1 contract validation
# ---------------------------------------------------------------------------


class TestEnvelopeContractCompliance:
    """Verify factory outputs pass the real ingest.v1 Pydantic models."""

    def test_email_envelope_passes_contract_validation(self) -> None:
        """email_envelope() output validates against IngestEnvelopeV1."""
        from butlers.tools.switchboard.routing.contracts import parse_ingest_envelope

        env = email_envelope(
            sender="alice@example.com",
            subject="Team lunch Thursday",
            body="Let's do noon at the usual place",
        )
        parsed = parse_ingest_envelope(env)
        assert parsed.source.channel == "email"
        assert parsed.source.provider == "gmail"
        assert parsed.sender.identity == "alice@example.com"

    def test_telegram_envelope_passes_contract_validation(self) -> None:
        """telegram_envelope() output validates against IngestEnvelopeV1."""
        from butlers.tools.switchboard.routing.contracts import parse_ingest_envelope

        env = telegram_envelope(chat_id=12345, text="I ran 5km this morning")
        parsed = parse_ingest_envelope(env)
        assert parsed.source.channel == "telegram"
        assert parsed.source.provider == "telegram"

    def test_email_with_thread_id_passes_validation(self) -> None:
        """email_envelope with thread_id has valid external_thread_id."""
        from butlers.tools.switchboard.routing.contracts import parse_ingest_envelope

        env = email_envelope(
            sender="bob@example.com",
            subject="Re: Meeting",
            body="Confirmed!",
            thread_id="thread-xyz-789",
        )
        parsed = parse_ingest_envelope(env)
        assert parsed.event.external_thread_id == "thread-xyz-789"


# ---------------------------------------------------------------------------
# Scenario dataclass tests
# ---------------------------------------------------------------------------


class TestScenarioDataclass:
    """Tests for the unified Scenario dataclass structure."""

    def test_scenario_has_required_fields(self) -> None:
        """Scenario dataclass has all required fields per spec."""
        s = Scenario(
            id="test-scenario",
            description="Test",
            envelope={"schema_version": "ingest.v1"},
        )
        # All fields from spec must exist
        assert hasattr(s, "id")
        assert hasattr(s, "description")
        assert hasattr(s, "envelope")
        assert hasattr(s, "expected_routing")
        assert hasattr(s, "expected_tool_calls")
        assert hasattr(s, "db_assertions")
        assert hasattr(s, "tags")
        assert hasattr(s, "timeout_seconds")

    def test_scenario_defaults(self) -> None:
        """Scenario has sensible defaults for optional fields."""
        s = Scenario(
            id="test",
            description="Test",
            envelope={},
        )
        assert s.expected_routing is None
        assert s.expected_tool_calls == []
        assert s.db_assertions == []
        assert s.tags == []
        assert s.timeout_seconds == 60

    def test_scenario_with_db_assertion(self) -> None:
        """Scenario can hold DbAssertion objects."""
        db_assert = DbAssertion(
            butler="health",
            query="SELECT COUNT(*) as count FROM measurements",
            expected=1,
            description="Should have one measurement",
        )
        s = Scenario(
            id="health-test",
            description="Health test",
            envelope={},
            expected_routing="health",
            db_assertions=[db_assert],
            tags=["telegram", "health"],
        )
        assert len(s.db_assertions) == 1
        assert s.db_assertions[0].butler == "health"


# ---------------------------------------------------------------------------
# Scenario corpus tests
# ---------------------------------------------------------------------------


class TestScenarioCorpus:
    """Tests for the ALL_SCENARIOS corpus coverage."""

    def test_scenario_count_in_range(self) -> None:
        """Corpus has between 20 and 30 scenarios as specified."""
        count = len(ALL_SCENARIOS)
        assert 20 <= count <= 30, (
            f"Expected 20-30 scenarios, got {count}. Add more scenarios or remove excess."
        )

    def test_all_scenarios_have_unique_ids(self) -> None:
        """All scenario IDs are unique."""
        ids = [s.id for s in ALL_SCENARIOS]
        assert len(ids) == len(set(ids)), "Duplicate scenario IDs found"

    def test_all_scenarios_have_non_empty_description(self) -> None:
        for s in ALL_SCENARIOS:
            assert s.description, f"Scenario {s.id} has empty description"

    def test_all_scenarios_have_non_empty_envelope(self) -> None:
        for s in ALL_SCENARIOS:
            assert s.envelope, f"Scenario {s.id} has empty envelope"

    def test_all_scenarios_have_at_least_one_tag(self) -> None:
        for s in ALL_SCENARIOS:
            assert s.tags, f"Scenario {s.id} has no tags"

    def test_email_scenarios_tagged_with_email(self) -> None:
        """All email scenarios have the 'email' tag."""
        email_scenarios = [
            s for s in ALL_SCENARIOS if s.envelope.get("source", {}).get("channel") == "email"
        ]
        for s in email_scenarios:
            assert "email" in s.tags, f"Email scenario {s.id} missing 'email' tag"

    def test_telegram_scenarios_tagged_with_telegram(self) -> None:
        """All telegram scenarios have the 'telegram' tag."""
        tg_scenarios = [
            s for s in ALL_SCENARIOS if s.envelope.get("source", {}).get("channel") == "telegram"
        ]
        for s in tg_scenarios:
            assert "telegram" in s.tags, f"Telegram scenario {s.id} missing 'telegram' tag"

    def test_smoke_scenarios_exist(self) -> None:
        """There are smoke-tagged scenarios for fast sanity checking."""
        smoke = [s for s in ALL_SCENARIOS if "smoke" in s.tags]
        assert len(smoke) >= 3, f"Expected at least 3 smoke scenarios, got {len(smoke)}"

    def test_corpus_covers_email_channel(self) -> None:
        """Corpus includes email-channel scenarios."""
        email_s = [s for s in ALL_SCENARIOS if "email" in s.tags]
        assert len(email_s) >= 5

    def test_corpus_covers_telegram_channel(self) -> None:
        """Corpus includes telegram-channel scenarios."""
        tg_s = [s for s in ALL_SCENARIOS if "telegram" in s.tags]
        assert len(tg_s) >= 8

    def test_corpus_covers_health_butler(self) -> None:
        """Corpus includes health butler scenarios."""
        health_s = [s for s in ALL_SCENARIOS if "health" in s.tags]
        assert len(health_s) >= 5

    def test_corpus_covers_calendar_butler(self) -> None:
        """Corpus includes calendar butler scenarios."""
        cal_s = [s for s in ALL_SCENARIOS if "calendar" in s.tags]
        assert len(cal_s) >= 3

    def test_corpus_covers_interactive_scenarios(self) -> None:
        """Corpus includes interactive (conversational → notify) scenarios."""
        interactive_s = [s for s in ALL_SCENARIOS if "interactive" in s.tags]
        assert len(interactive_s) >= 3

    def test_corpus_covers_edge_cases(self) -> None:
        """Corpus includes multi-butler edge case scenarios."""
        edge_cases = [s for s in ALL_SCENARIOS if "edge-case" in s.tags]
        assert len(edge_cases) >= 3

    def test_timeout_seconds_reasonable(self) -> None:
        """All scenarios have a reasonable timeout."""
        for s in ALL_SCENARIOS:
            assert 10 <= s.timeout_seconds <= 300, (
                f"Scenario {s.id} has unreasonable timeout: {s.timeout_seconds}s"
            )


# ---------------------------------------------------------------------------
# Tag-based filtering tests
# ---------------------------------------------------------------------------


class TestScenarioTagFiltering:
    """Tests for get_scenarios_by_tags() function."""

    def test_filter_smoke_tag(self) -> None:
        """Filtering by 'smoke' returns only smoke-tagged scenarios."""
        result = get_scenarios_by_tags(["smoke"])
        assert len(result) > 0
        for s in result:
            assert "smoke" in s.tags

    def test_filter_email_tag(self) -> None:
        result = get_scenarios_by_tags(["email"])
        assert len(result) > 0
        for s in result:
            assert "email" in s.tags

    def test_filter_telegram_health_combined(self) -> None:
        """AND filter: telegram AND health returns subset of each."""
        result = get_scenarios_by_tags(["telegram", "health"])
        for s in result:
            assert "telegram" in s.tags
            assert "health" in s.tags

    def test_filter_nonexistent_tag_returns_empty(self) -> None:
        result = get_scenarios_by_tags(["nonexistent-tag-xyz"])
        assert result == []

    def test_filter_empty_tags_returns_all(self) -> None:
        result = get_scenarios_by_tags([])
        assert result == ALL_SCENARIOS

    def test_smoke_filter_is_subset_of_all(self) -> None:
        smoke = get_scenarios_by_tags(["smoke"])
        all_ids = {s.id for s in ALL_SCENARIOS}
        for s in smoke:
            assert s.id in all_ids
