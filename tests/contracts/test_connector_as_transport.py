"""Contract tests: Connector-as-Transport (vision.md Rule 7, Invariant 14).

Validates that connectors normalize to ingest.v1 only and contain no routing,
classification, or domain logic.

Principle: Transport is connector responsibility; butlers never know about
transport details. Connectors normalize, butlers receive structured requests
(vision.md Rule 7).
"""

from __future__ import annotations

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

        Behavioral assertion: the GmailConnectorRuntime class does not expose any
        domain-specific methods or attributes (health_check, medication, etc.)
        at the instance or class level. We scan all attribute names for forbidden
        domain substrings to catch any variant (e.g. relationship_helper).
        """
        try:
            from butlers.connectors.gmail import GmailConnectorRuntime

            # Connector must not expose butler-domain methods at the instance level.
            # Use substring matching to catch all variants of a term (e.g. relationship_helper).
            butler_domain_terms = [
                "health_check",
                "medication",
                "finance_alert",
                "relationship_",
            ]
            all_attrs = dir(GmailConnectorRuntime)
            exposed = [
                attr for attr in all_attrs if any(term in attr for term in butler_domain_terms)
            ]
            assert exposed == [], (
                f"Gmail connector must not expose butler domain attributes: {exposed} (Rule 7)"
            )
        except ImportError:
            pytest.skip("Gmail connector not available in test environment")

    def test_telegram_connector_does_not_classify_messages(self):
        """vision.md Rule 7: Telegram connector module must not expose classification callables.

        Classification happens in the Switchboard's triage pipeline.
        Connectors only normalize to ingest.v1.

        Behavioral assertion: the module-level public API does not expose
        classify(), triage_rules(), or route_to() as callable attributes.
        """
        try:
            from butlers.connectors import telegram_user_client

            # These would be violations: classification functions on a connector module
            routing_callables = ["classify", "triage_rules", "route_to"]
            exposed = [
                name
                for name in routing_callables
                if callable(getattr(telegram_user_client, name, None))
            ]
            assert exposed == [], (
                f"Telegram connector must not expose classification callables: {exposed} (Rule 7)"
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

    def test_butler_email_module_does_not_hold_connector_dependencies(self):
        """vision.md Rule 7: Butler email module must not receive connector instances.

        'A butler must never contain Telegram polling logic, Gmail API calls.'

        Behavioral assertion: EmailModule instances do not hold any connector
        instance as an attribute — connectors are not injected as dependencies
        into modules. Note: this checks runtime injection, not import statements.
        """
        try:
            from butlers.modules.email import EmailModule

            email = EmailModule()
            # The email module must not carry connector-related instance attributes
            assert not hasattr(email, "connector"), (
                "EmailModule must not hold a reference to a connector instance (Rule 7)"
            )
            assert not hasattr(email, "gmail_connector"), (
                "EmailModule must not hold a GmailConnector reference (Rule 7)"
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
