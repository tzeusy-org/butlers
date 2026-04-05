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
    def test_dashboard_accepted_in_all_channel_contracts(self) -> None:
        """IngestEnvelopeV1, RouteRequestContextV1, and RouteSourceMetadataV1 all accept 'dashboard'."""
        from butlers.tools.switchboard.routing.contracts import (
            IngestEnvelopeV1,
            RouteRequestContextV1,
            RouteSourceMetadataV1,
        )

        envelope = IngestEnvelopeV1.model_validate(_dashboard_ingest_payload())
        assert envelope.source.channel == "dashboard"

        ctx = RouteRequestContextV1.model_validate({
            "request_id": _VALID_UUID7,
            "received_at": _now_iso(),
            "source_channel": "dashboard",
            "source_endpoint_identity": "dashboard:butler:general",
            "source_sender_identity": "owner",
        })
        assert ctx.source_channel == "dashboard"

        meta = RouteSourceMetadataV1.model_validate({
            "channel": "dashboard",
            "identity": "dashboard:butler:general",
            "tool_name": "ingest.v1",
        })
        assert meta.channel == "dashboard"


# ---------------------------------------------------------------------------
# Task 1.2 — _ALLOWED_PROVIDERS_BY_CHANNEL maps 'dashboard' → {'internal'}
# ---------------------------------------------------------------------------


class TestAllowedProvidersByChannelDashboard:
    def test_dashboard_provider_validation(self) -> None:
        """dashboard+internal accepted; dashboard+telegram and dashboard+gmail rejected."""
        from butlers.tools.switchboard.routing.contracts import (
            IngestEnvelopeV1,
            _ALLOWED_PROVIDERS_BY_CHANNEL,
        )

        assert "dashboard" in _ALLOWED_PROVIDERS_BY_CHANNEL
        assert _ALLOWED_PROVIDERS_BY_CHANNEL["dashboard"] == frozenset({"internal"})

        envelope = IngestEnvelopeV1.model_validate(_dashboard_ingest_payload())
        assert envelope.source.provider == "internal"

        for bad_provider in ("telegram", "gmail"):
            payload = _dashboard_ingest_payload()
            payload["source"]["provider"] = bad_provider
            with pytest.raises(ValidationError) as exc_info:
                IngestEnvelopeV1.model_validate(payload)
            assert exc_info.value.errors()[0]["type"] == "invalid_source_provider"


# ---------------------------------------------------------------------------
# Task 1.3 — 'dashboard' is in TRIGGER_SOURCES
# ---------------------------------------------------------------------------


class TestTriggerSourcesDashboard:
    async def test_trigger_sources_and_session_create(self) -> None:
        """TRIGGER_SOURCES has dashboard; pre-existing values intact; session_create accepts dashboard; rejects unknown."""
        from butlers.core.sessions import TRIGGER_SOURCES, session_create

        assert "dashboard" in TRIGGER_SOURCES
        for expected in ("tick", "external", "trigger", "route", "healing"):
            assert expected in TRIGGER_SOURCES, f"'{expected}' unexpectedly removed"

        class _FakePool:
            async def fetchval(self, sql: str, *args: Any) -> uuid.UUID:
                return uuid.uuid4()

        pool = _FakePool()
        result = await session_create(
            pool, prompt="Dashboard-triggered session",
            trigger_source="dashboard", request_id=str(uuid.uuid4()),
        )
        assert isinstance(result, uuid.UUID)

        with pytest.raises(ValueError, match="Invalid trigger_source"):
            await session_create(
                pool, prompt="Bad trigger",
                trigger_source="web", request_id=str(uuid.uuid4()),
            )
