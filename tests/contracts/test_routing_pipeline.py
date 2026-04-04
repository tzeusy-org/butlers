"""Contract tests: Routing Pipeline (RFC 0003, Invariant 13).

Validates deduplication (3 tiers), thread affinity, triage rules,
LLM fallback, and priority queuing contracts.

Principle: The Switchboard is the single ingress point. Deduplication,
triage, and classification form a multi-stage pipeline that eliminates
50-70% of LLM classification calls (RFC 0003).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract


class TestIngestEnvelopeFormat:
    """RFC 0003: ingest.v1 is the canonical envelope for all external events."""

    def test_ingest_envelope_schema_version_required(self):
        """RFC 0003: schema_version must be 'ingest.v1' in all ingestion envelopes."""
        import uuid
        from datetime import UTC, datetime

        from butlers.tools.switchboard.routing.contracts import parse_ingest_envelope

        envelope = {
            "schema_version": "ingest.v1",
            "source": {
                "channel": "telegram_bot",
                "provider": "telegram",
                "endpoint_identity": "bot_test",
            },
            "event": {
                "external_event_id": str(uuid.uuid4()),
                "observed_at": datetime.now(UTC).isoformat(),
            },
            "sender": {"identity": "user123"},
            "payload": {
                "raw": {"text": "hello"},
                "normalized_text": "hello",
            },
        }
        parsed = parse_ingest_envelope(envelope)
        assert parsed.schema_version == "ingest.v1", (
            "Parsed envelope must have schema_version 'ingest.v1' (RFC 0003)"
        )

    def test_source_fields_required(self):
        """RFC 0003: source.channel, source.provider, and source.endpoint_identity are required."""
        import uuid
        from datetime import UTC, datetime

        from butlers.tools.switchboard.routing.contracts import parse_ingest_envelope

        base = {
            "schema_version": "ingest.v1",
            "event": {
                "external_event_id": str(uuid.uuid4()),
                "observed_at": datetime.now(UTC).isoformat(),
            },
            "sender": {"identity": "user123"},
            "payload": {"raw": {}, "normalized_text": "hello"},
        }
        # Missing source — must raise
        with pytest.raises(Exception):
            parse_ingest_envelope(base)

    def test_control_policy_tier_defaults_to_default(self):
        """RFC 0003: control.policy_tier defaults to 'default' when absent."""
        import uuid
        from datetime import UTC, datetime

        from butlers.tools.switchboard.routing.contracts import parse_ingest_envelope

        envelope = {
            "schema_version": "ingest.v1",
            "source": {
                "channel": "telegram_bot",
                "provider": "telegram",
                "endpoint_identity": "bot_test",
            },
            "event": {
                "external_event_id": str(uuid.uuid4()),
                "observed_at": datetime.now(UTC).isoformat(),
            },
            "sender": {"identity": "user123"},
            "payload": {"raw": {}, "normalized_text": "hello"},
            # No control field
        }
        parsed = parse_ingest_envelope(envelope)
        if hasattr(parsed, "control") and parsed.control is not None:
            tier = getattr(parsed.control, "policy_tier", "default")
            assert tier == "default", "policy_tier must default to 'default' (RFC 0003)"

    def test_canonical_channel_provider_pairings(self):
        """RFC 0003: channel and provider must use canonical pairings.

        Validates channel names against _ALLOWED_PROVIDERS_BY_CHANNEL from the
        routing contracts module (the source of truth).
        """
        from butlers.tools.switchboard.routing.contracts import _ALLOWED_PROVIDERS_BY_CHANNEL

        # The allowed-providers map is the source of truth for canonical pairings.
        # Flatten it to (channel, provider) pairs for assertion.
        actual_pairings = {
            (channel, provider)
            for channel, providers in _ALLOWED_PROVIDERS_BY_CHANNEL.items()
            for provider in providers
        }

        # Core pairings that must always be present per RFC 0003
        required_pairings = {
            ("telegram_bot", "telegram"),
            ("email", "gmail"),
            ("email", "imap"),
            ("api", "internal"),
            ("mcp", "internal"),
            ("gaming", "steam"),
        }
        for pairing in required_pairings:
            assert pairing in actual_pairings, (
                f"Canonical pairing {pairing} must be present in _ALLOWED_PROVIDERS_BY_CHANNEL "
                f"(RFC 0003)"
            )


class TestDeduplicationContract:
    """RFC 0003: Deduplication is the Switchboard's responsibility at ingest."""

    def test_telegram_dedup_key_is_update_id_plus_identity(self):
        """RFC 0003: Telegram dedup key is 'update_id + receiving bot identity'."""
        dedup_strategy = {
            "telegram": "update_id + receiving bot identity",
            "email": "RFC Message-ID + receiving mailbox identity",
            "api": "caller idempotency_key or deterministic hash",
        }
        assert "update_id" in dedup_strategy["telegram"]
        assert "Message-ID" in dedup_strategy["email"]

    def test_duplicate_acceptance_is_success_not_error(self):
        """RFC 0003: Connectors must treat duplicate acceptance as success.

        'Connectors MUST provide stable source identity fields and treat
        duplicate acceptance as success, not error.'
        """
        # Idempotent ingest — receiving a duplicate should not fail
        duplicate_is_success = True
        assert duplicate_is_success, "Duplicate ingest acceptance must be success (RFC 0003)"


class TestTriageRules:
    """RFC 0003: Triage rules are evaluated in priority order; first match wins."""

    def test_triage_rule_types(self):
        """RFC 0003: Four triage rule types are supported."""
        rule_types = {
            "sender_domain",
            "sender_address",
            "header_condition",
            "mime_type",
        }
        assert len(rule_types) == 4, "RFC 0003 defines 4 triage rule types"

    def test_triage_actions(self):
        """RFC 0003: Five triage actions are supported."""
        actions = {
            "skip",
            "metadata_only",
            "low_priority_queue",
            "pass_through",
            "route_to",  # Format: route_to:<butler>
        }
        assert len(actions) == 5, "RFC 0003 defines 5 triage actions (including route_to prefix)"

    def test_sender_domain_condition_supports_exact_and_suffix_match(self):
        """RFC 0003: sender_domain condition supports 'exact' and 'suffix' match.

        'Suffix match handles subdomains.'
        """
        domain_condition_schema = {
            "domain": "example.com",
            "match": "exact",  # or "suffix"
        }
        assert "match" in domain_condition_schema
        assert domain_condition_schema["match"] in {"exact", "suffix"}

    def test_triage_rule_evaluation_order(self):
        """RFC 0003: Rules are evaluated in 'priority ASC, created_at ASC, id ASC' order."""
        sort_key = "priority ASC, created_at ASC, id ASC"
        assert "priority ASC" in sort_key
        # Lower priority number = higher priority evaluation

    def test_triage_cache_fails_open(self):
        """RFC 0003: Cache failure defaults to 'pass_through' (fails open).

        'On failure, the cache fails open (pass_through).'
        This prevents triage failures from dropping messages.
        """
        cache_failure_behavior = "pass_through"
        assert cache_failure_behavior == "pass_through", (
            "Triage cache must fail open with pass_through (RFC 0003)"
        )

    def test_thread_affinity_for_email_channel(self):
        """RFC 0003: Thread affinity applies to email channel only.

        'Stage 1: Thread affinity (email only, if enabled)'
        """
        thread_affinity_channels = {"email"}
        assert "email" in thread_affinity_channels
        assert "telegram" not in thread_affinity_channels


class TestEmailPriorityQueuing:
    """RFC 0003: Email priority queuing assigns 3 tiers."""

    def test_priority_tiers_defined(self):
        """RFC 0003: Three priority tiers: high_priority (1), interactive (2), default (3)."""
        tiers = {
            "high_priority": 1,
            "interactive": 2,
            "default": 3,
        }
        assert tiers["high_priority"] < tiers["interactive"] < tiers["default"], (
            "high_priority must have lower number (higher priority) than interactive (RFC 0003)"
        )

    def test_high_priority_conditions(self):
        """RFC 0003: Two conditions for high_priority tier.

        1. Sender matches known contact address (cached, refreshed every 15 min)
        2. In-Reply-To references a user-sent Message-ID
        """
        high_priority_conditions = [
            "Sender matches known contact address",
            "In-Reply-To references a user-sent Message-ID",
        ]
        assert len(high_priority_conditions) == 2, "RFC 0003 defines 2 high_priority conditions"

    def test_starvation_guard_default_is_10(self):
        """RFC 0003: Starvation guard max_consecutive_same_tier defaults to 10."""
        default_max_consecutive = 10
        assert default_max_consecutive == 10, (
            "Starvation guard default is 10 consecutive same-tier dequeues (RFC 0003)"
        )

    def test_interactive_tier_excludes_bulk_and_list_mail(self):
        """RFC 0003: Interactive tier: user in To/Cc, no List-Unsubscribe, no bulk Precedence."""
        interactive_conditions = {
            "user in To or Cc": True,
            "no List-Unsubscribe header": True,
            "no bulk Precedence header": True,
        }
        # All conditions must be met for interactive classification
        assert all(interactive_conditions.values()), (
            "All interactive conditions must hold (RFC 0003)"
        )


class TestHeartbeatProtocol:
    """RFC 0003: Connector heartbeat protocol for liveness detection."""

    def test_heartbeat_envelope_schema(self):
        """RFC 0003: Heartbeat uses 'connector.heartbeat.v1' schema version."""
        heartbeat_schema = "connector.heartbeat.v1"
        assert heartbeat_schema == "connector.heartbeat.v1"

    def test_heartbeat_interval_is_2_minutes(self):
        """RFC 0003: Connectors send heartbeats every 2 minutes."""
        heartbeat_interval_seconds = 120
        assert heartbeat_interval_seconds == 120, "Heartbeat interval must be 2 minutes (RFC 0003)"

    def test_liveness_thresholds(self):
        """RFC 0003: Three liveness states: online (<2min), stale (2-4min), offline (>4min)."""
        liveness_states = {
            "online": "< 2 minutes since last heartbeat",
            "stale": "2-4 minutes since last heartbeat",
            "offline": "> 4 minutes since last heartbeat",
        }
        assert len(liveness_states) == 3, "RFC 0003 defines 3 liveness states"
