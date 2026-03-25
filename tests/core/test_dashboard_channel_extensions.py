"""Unit tests for dashboard source channel and trigger source extensions.

Covers tasks 1.1–1.4 from openspec/changes/dashboard-conversational-input/tasks.md:

  1.1  'dashboard' is present in the SourceChannel Literal.
  1.2  _ALLOWED_PROVIDERS_BY_CHANNEL maps 'dashboard' → frozenset({'internal'}).
  1.3  'dashboard' is present in TRIGGER_SOURCES.
  1.4  dashboard channel+provider validation and trigger source validation tests.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_UUID7 = "018f52f3-9d8a-7ef2-8f2d-9fb6b32f12aa"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _dashboard_ingest_payload() -> dict[str, Any]:
    """Minimal valid ingest.v1 envelope with dashboard channel."""
    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "dashboard",
            "provider": "internal",
            "endpoint_identity": "dashboard:butler:general",
        },
        "event": {
            "external_event_id": "conv-123",
            "external_thread_id": "conv-123",
            "observed_at": _now_iso(),
        },
        "sender": {"identity": "owner"},
        "payload": {
            "raw": {"text": "Hello"},
            "normalized_text": "Hello",
        },
    }


# ---------------------------------------------------------------------------
# Task 1.1 — 'dashboard' is in SourceChannel Literal
# ---------------------------------------------------------------------------


class TestSourceChannelContainsDashboard:
    def test_dashboard_is_valid_source_channel(self) -> None:
        """IngestEnvelopeV1 accepts 'dashboard' as source.channel."""
        from butlers.tools.switchboard.routing.contracts import IngestEnvelopeV1

        envelope = IngestEnvelopeV1.model_validate(_dashboard_ingest_payload())
        assert envelope.source.channel == "dashboard"

    def test_dashboard_channel_preserved_in_route_context(self) -> None:
        """RouteRequestContextV1 accepts 'dashboard' as source_channel."""
        from butlers.tools.switchboard.routing.contracts import RouteRequestContextV1

        ctx = RouteRequestContextV1.model_validate(
            {
                "request_id": _VALID_UUID7,
                "received_at": _now_iso(),
                "source_channel": "dashboard",
                "source_endpoint_identity": "dashboard:butler:general",
                "source_sender_identity": "owner",
            }
        )
        assert ctx.source_channel == "dashboard"

    def test_dashboard_channel_in_route_source_metadata(self) -> None:
        """RouteSourceMetadataV1 accepts 'dashboard' as channel."""
        from butlers.tools.switchboard.routing.contracts import RouteSourceMetadataV1

        meta = RouteSourceMetadataV1.model_validate(
            {
                "channel": "dashboard",
                "identity": "dashboard:butler:general",
                "tool_name": "ingest.v1",
            }
        )
        assert meta.channel == "dashboard"


# ---------------------------------------------------------------------------
# Task 1.2 — _ALLOWED_PROVIDERS_BY_CHANNEL maps 'dashboard' → {'internal'}
# ---------------------------------------------------------------------------


class TestAllowedProvidersByChannelDashboard:
    def test_dashboard_maps_to_internal_provider(self) -> None:
        """_ALLOWED_PROVIDERS_BY_CHANNEL['dashboard'] == frozenset({'internal'})."""
        from butlers.tools.switchboard.routing.contracts import _ALLOWED_PROVIDERS_BY_CHANNEL

        assert "dashboard" in _ALLOWED_PROVIDERS_BY_CHANNEL
        assert _ALLOWED_PROVIDERS_BY_CHANNEL["dashboard"] == frozenset({"internal"})

    def test_dashboard_with_internal_provider_accepted(self) -> None:
        """Ingest envelope with dashboard+internal is valid."""
        from butlers.tools.switchboard.routing.contracts import IngestEnvelopeV1

        envelope = IngestEnvelopeV1.model_validate(_dashboard_ingest_payload())
        assert envelope.source.provider == "internal"

    def test_dashboard_with_telegram_provider_rejected(self) -> None:
        """dashboard channel with a non-internal provider is rejected."""
        from butlers.tools.switchboard.routing.contracts import IngestEnvelopeV1

        payload = _dashboard_ingest_payload()
        payload["source"]["provider"] = "telegram"

        with pytest.raises(ValidationError) as exc_info:
            IngestEnvelopeV1.model_validate(payload)

        error = exc_info.value.errors()[0]
        assert error["type"] == "invalid_source_provider"

    def test_dashboard_with_gmail_provider_rejected(self) -> None:
        """dashboard channel with gmail provider is rejected."""
        from butlers.tools.switchboard.routing.contracts import IngestEnvelopeV1

        payload = _dashboard_ingest_payload()
        payload["source"]["provider"] = "gmail"

        with pytest.raises(ValidationError) as exc_info:
            IngestEnvelopeV1.model_validate(payload)

        error = exc_info.value.errors()[0]
        assert error["type"] == "invalid_source_provider"


# ---------------------------------------------------------------------------
# Task 1.3 — 'dashboard' is in TRIGGER_SOURCES
# ---------------------------------------------------------------------------


class TestTriggerSourcesDashboard:
    def test_dashboard_in_trigger_sources(self) -> None:
        """TRIGGER_SOURCES frozenset contains 'dashboard'."""
        from butlers.core.sessions import TRIGGER_SOURCES

        assert "dashboard" in TRIGGER_SOURCES

    def test_trigger_sources_still_contains_existing_values(self) -> None:
        """TRIGGER_SOURCES continues to include all pre-existing values."""
        from butlers.core.sessions import TRIGGER_SOURCES

        for expected in ("tick", "external", "trigger", "route", "healing"):
            assert expected in TRIGGER_SOURCES, (
                f"'{expected}' unexpectedly removed from TRIGGER_SOURCES"
            )

    async def test_session_create_accepts_dashboard_trigger_source(self) -> None:
        """session_create does not raise for trigger_source='dashboard'."""
        from butlers.core.sessions import session_create

        class _FakePool:
            async def fetchval(self, sql: str, *args: Any) -> uuid.UUID:
                return uuid.uuid4()

        pool = _FakePool()
        result = await session_create(
            pool,
            prompt="Dashboard-triggered session",
            trigger_source="dashboard",
            request_id=str(uuid.uuid4()),
        )
        assert isinstance(result, uuid.UUID)

    async def test_session_create_still_rejects_unknown_trigger_source(self) -> None:
        """session_create still rejects unknown trigger sources after the extension."""
        from butlers.core.sessions import session_create

        class _FakePool:
            async def fetchval(self, sql: str, *args: Any) -> uuid.UUID:
                return uuid.uuid4()

        pool = _FakePool()
        with pytest.raises(ValueError, match="Invalid trigger_source"):
            await session_create(
                pool,
                prompt="Bad trigger",
                trigger_source="web",
                request_id=str(uuid.uuid4()),
            )
