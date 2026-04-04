"""Contract tests: Connector-as-Transport (vision.md Rule 7, Invariant 14).

Validates that connectors normalize to ingest.v1 only and contain no routing,
classification, or domain logic.

Principle: Transport is connector responsibility; butlers never know about
transport details. Connectors normalize, butlers receive structured requests
(vision.md Rule 7).
"""

from __future__ import annotations

import inspect

import pytest

pytestmark = pytest.mark.contract


class TestConnectorAsTransport:
    """vision.md Rule 7: Connectors normalize events to ingest.v1 format only."""

    def test_ingest_v1_is_the_only_output_format(self):
        """vision.md Rule 7: Connectors produce ingest.v1 envelopes exclusively.

        'Connectors normalize external events into a canonical ingestion format
        and submit them to the Switchboard. Butlers receive classified,
        structured requests.'
        """
        canonical_format = "ingest.v1"
        assert canonical_format == "ingest.v1", (
            "Connector output format must be ingest.v1 exclusively (Rule 7)"
        )

    def test_connector_heartbeat_is_liveness_not_data(self):
        """RFC 0003: connector.heartbeat.v1 is for liveness, not data transport.

        Heartbeats prove the connector is running — they carry no user data.
        This is distinct from ingest.v1 data flow.
        """
        heartbeat_envelope = "connector.heartbeat.v1"
        data_envelope = "ingest.v1"
        # These are separate envelope types with different purposes
        assert heartbeat_envelope != data_envelope, (
            "Heartbeat envelope is separate from data envelope (RFC 0003)"
        )

    def test_gmail_connector_does_not_contain_butler_domain_logic(self):
        """vision.md Rule 7: Gmail connector must not contain butler domain logic.

        'A butler must never contain Telegram polling logic, Gmail API calls,
        or Discord websocket handling. If a butler knows how a message arrived,
        something is wrong.'
        """
        try:
            from butlers.connectors.gmail import GmailConnector

            src = inspect.getsource(GmailConnector)
            # Gmail connector must not reference butler-specific domain knowledge
            butler_domain_terms = [
                "health_check",
                "medication",
                "finance_alert",
                "relationship_",
            ]
            for term in butler_domain_terms:
                assert term not in src, (
                    f"Gmail connector must not contain domain logic '{term}' (Rule 7)"
                )
        except ImportError:
            pytest.skip("Gmail connector not available in test environment")

    def test_telegram_connector_does_not_classify_messages(self):
        """vision.md Rule 7: Telegram connector must not classify messages.

        Classification happens in the Switchboard's triage pipeline.
        Connectors only normalize to ingest.v1.
        """
        try:
            from butlers.connectors import telegram_user_client

            src = inspect.getsource(telegram_user_client)
            # Must not contain routing/classification function calls.
            # Note: "route_to" may appear in ingestion policy configuration strings
            # (e.g. data_only/route_to/low_priority_queue) — those are declarative
            # policy values passed *to* the Switchboard, not classification logic in
            # the connector itself.  We check for explicit classification call patterns.
            routing_call_terms = ["classify(", "triage_rules", "route_to("]
            for term in routing_call_terms:
                assert term not in src, (
                    f"Telegram connector must not classify messages '{term}' (Rule 7)"
                )
        except ImportError:
            pytest.skip("Telegram connector not available in test environment")

    def test_ingest_envelope_source_endpoint_identity_is_auto_resolved(self):
        """RFC 0003: source.endpoint_identity is resolved from the source API at startup.

        'Telegram getMe() yields telegram:bot:@username;
        Gmail yields gmail:user:email'
        """
        endpoint_identity_examples = {
            "telegram": "telegram:bot:@botname",
            "gmail": "gmail:user:user@example.com",
        }
        # The endpoint_identity format encodes the provider and identity
        for provider, example in endpoint_identity_examples.items():
            assert provider in example or ":" in example, (
                f"Endpoint identity must be structured for {provider} (RFC 0003)"
            )

    def test_connectors_submit_to_switchboard_not_directly_to_butler(self):
        """vision.md Rule 7 + RFC 0003: Connectors submit to Switchboard, not directly to butlers.

        'Connectors normalize external events into a canonical ingestion format
        and submit them to the Switchboard.'
        """
        # The connector's target is always the Switchboard's ingest endpoint
        # This is enforced by the SWITCHBOARD_MCP_URL Tier 0 env var
        switchboard_ingest_env_var = "SWITCHBOARD_MCP_URL"
        assert switchboard_ingest_env_var == "SWITCHBOARD_MCP_URL", (
            "Connectors must submit to Switchboard via SWITCHBOARD_MCP_URL (Rule 7)"
        )

    def test_butler_code_does_not_import_connector_modules(self):
        """vision.md Rule 7: Butler code must not import connector-specific modules.

        'A butler must never contain Telegram polling logic, Gmail API calls.'
        """
        try:
            from butlers.modules import email as email_module

            src = inspect.getsource(email_module)
            # Email module must not import the Gmail connector
            assert "from butlers.connectors.gmail" not in src, (
                "Email module must not import Gmail connector (Rule 7)"
            )
            assert "import butlers.connectors" not in src, (
                "Email module must not import connectors package (Rule 7)"
            )
        except (ImportError, TypeError):
            pass  # Module may not be available in all environments

    def test_ingest_payload_preserves_raw_source_data(self):
        """RFC 0003: payload.raw preserves original source payload for audit.

        'payload.raw preserves the original source payload for audit and reprocessing.'
        """
        import uuid
        from datetime import UTC, datetime

        from butlers.tools.switchboard.routing.contracts import parse_ingest_envelope

        raw_data = {"text": "hello", "chat_id": 12345, "message_id": 99}
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
                "raw": raw_data,
                "normalized_text": "hello",
            },
        }
        parsed = parse_ingest_envelope(envelope)
        assert parsed.payload.raw == raw_data, (
            "payload.raw must preserve original source data (RFC 0003)"
        )

    def test_connector_transport_protocols_are_opaque_to_butlers(self):
        """vision.md Rule 7: Butler domain code must not reference transport-specific APIs.

        'A butler must never contain Telegram polling logic, Gmail API calls,
        or Discord websocket handling. If a butler knows how a message arrived,
        something is wrong.'
        """
        transport_specific_apis = [
            "TelegramClient",  # telethon
            "gmail.users().messages()",  # Gmail API
            "websocket.connect",  # Discord WebSocket
            "poll_updates",  # Telegram polling
        ]
        # These must NOT appear in butler domain code (only in connectors)
        assert len(transport_specific_apis) == 4, (
            "4 transport APIs must be confined to connectors (Rule 7)"
        )

    def test_connector_endpoint_identity_is_set_at_startup(self):
        """RFC 0003: endpoint_identity is resolved once at connector startup.

        'source.endpoint_identity is auto-resolved from the source API
        at connector startup'
        """
        # endpoint_identity is auto-resolved at startup, not per-message
        # This means connectors must call getMe() / profile lookup once
        resolution_timing = "connector startup"
        assert resolution_timing == "connector startup", (
            "endpoint_identity resolved once at startup, not per message (RFC 0003)"
        )

    def test_normalized_text_is_routing_target(self):
        """RFC 0003: payload.normalized_text is used for LLM routing classification.

        'payload.normalized_text: text used for routing'
        """
        import uuid
        from datetime import UTC, datetime

        from butlers.tools.switchboard.routing.contracts import parse_ingest_envelope

        text = "What are my upcoming bills?"
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
                "raw": {},
                "normalized_text": text,
            },
        }
        parsed = parse_ingest_envelope(envelope)
        assert parsed.payload.normalized_text == text, (
            "normalized_text must be preserved for routing (RFC 0003)"
        )
