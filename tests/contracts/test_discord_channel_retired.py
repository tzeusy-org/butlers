"""The retired Discord connector's channel is refused like any undeclared channel.

Bead bu-lsxqb0.25 (owner decision bu-lsxqb0.4 option A, 2026-10-04): Discord is deferred
and the draft connector retired, so a Discord envelope must not validate at ingest.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from butlers.tools.switchboard.routing.contracts import parse_ingest_envelope

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
