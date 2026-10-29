"""The retired Discord connector is absent from active ingestion and OAuth surfaces.

Bead bu-lsxqb0.25 (owner decision bu-lsxqb0.4 option A, 2026-10-04): Discord is deferred
and the draft connector retired, so a Discord envelope must not validate at ingest.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from butlers.api.oauth_scope_registry import (
    APPLICABILITY_MATRIX,
    get_applicability,
    get_scope_manifest,
)
from butlers.api.routers.ingestion_connectors import list_available_connectors
from butlers.tools.switchboard.contracts import parse_ingest_envelope

pytestmark = pytest.mark.unit


def test_a_discord_channel_envelope_is_refused_as_an_undeclared_channel() -> None:
    envelope = {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "discord",
            "provider": "discord",
            "endpoint_identity": "discord:user",
        },
        "event": {
            "external_event_id": str(uuid.uuid4()),
            "external_conversation_id": "discord:1",
            "reply_target_ref": "1:2",
            "observed_at": datetime.now(UTC).isoformat(),
        },
        "sender": {"identity": "user123"},
        "payload": {"raw": {"text": "hi"}, "normalized_text": "hi"},
    }

    with pytest.raises(ValidationError) as excinfo:
        parse_ingest_envelope(envelope)

    assert any(err["loc"] == ("source", "channel") for err in excinfo.value.errors())


async def test_discord_is_absent_from_available_connectors() -> None:
    response = await list_available_connectors()

    assert "discord_user" not in {profile.connector_type for profile in response.data}
    assert all(profile.channel != "discord" for profile in response.data)
    assert all(profile.provider != "discord" for profile in response.data)


def test_discord_has_no_oauth_manifest_or_auth_registration() -> None:
    for connector_type in ("discord", "discord_user"):
        assert get_scope_manifest(connector_type) is None
        assert connector_type not in APPLICABILITY_MATRIX
        assert get_applicability(connector_type).oauth_supported is False
