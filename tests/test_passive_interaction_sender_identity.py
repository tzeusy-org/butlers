"""Passive-interaction sender identity must survive the connector→Dunbar pipeline.

Batch (buffered) chat envelopes collapse ``sender.identity`` to the literal
string ``"multiple"`` because a flush covers several senders.  The real
per-sender identities travel in ``sender.participants``.  Three layers used to
drop them, so ``interaction_sync`` resolved every batch sender against the
literal ``"multiple"``, matched nothing, and every WhatsApp/Telegram contact
accumulated zero interaction facts — silently pinning them to Dunbar tier 1500
(spec ``dunbar-tier-scoring``, ``passive-interaction-sync``).

Guards, in pipeline order:
  A. the WhatsApp connector populates ``sender.participants``/``owner_sender_id``
  B. ``ingest_v1`` propagates them into ``message_inbox.request_context``
  C. ``interaction_sync`` reads the participant list, not the collapsed field
  D. WhatsApp JID phones match ``has-phone`` triples stored in any format
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

from butlers.identity import _channel_candidates, _extract_whatsapp_jid_phone

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Layer D: phone normalisation for WhatsApp JID cross-reference
# ---------------------------------------------------------------------------


class TestWhatsAppJidCandidates:
    """A WhatsApp JID must yield a normalised-phone candidate."""

    def test_individual_jid_yields_normalised_phone_candidate(self) -> None:
        candidates = _channel_candidates("whatsapp_jid", "6598150802@s.whatsapp.net")
        assert ("phone_digits", "6598150802") in candidates

    def test_group_jid_yields_no_phone_candidate(self) -> None:
        candidates = _channel_candidates("whatsapp_jid", "6598150802-1386556114@g.us")
        assert not [c for c in candidates if c[0] == "phone_digits"]


# ---------------------------------------------------------------------------
# Layer B: ingest_v1 propagates participants into request_context
# ---------------------------------------------------------------------------


def _envelope(sender: dict[str, Any]) -> Any:
    from butlers.tools.switchboard.routing.contracts import IngestEnvelopeV1

    return IngestEnvelopeV1.model_validate(
        {
            "schema_version": "ingest.v1",
            "source": {
                "channel": "whatsapp_user_client",
                "provider": "whatsapp",
                "endpoint_identity": "whatsapp:+6591153887",
            },
            "event": {
                "external_event_id": "batch:1",
                "external_thread_id": "6598150802-1386556114@g.us",
                "observed_at": "2026-08-01T12:50:50+00:00",
            },
            "sender": sender,
            "payload": {"raw": {}, "normalized_text": "hi"},
            "control": {"idempotency_key": "k1"},
        }
    )


class TestRequestContextPropagation:
    def test_participants_reach_request_context(self) -> None:
        from datetime import UTC, datetime

        from butlers.tools.switchboard.ingestion.ingest import _build_request_context

        envelope = _envelope(
            {
                "identity": "multiple",
                "participants": {
                    "6598150802@s.whatsapp.net": "Mummy",
                    "6591153887@s.whatsapp.net": "Tze How Lee",
                },
                "owner_sender_id": "6591153887@s.whatsapp.net",
                "participant_count": 2,
                "chat_type": "group",
            }
        )
        ctx = _build_request_context(
            envelope,
            request_id=uuid.uuid4(),
            received_at=datetime(2026, 8, 1, 12, 50, tzinfo=UTC),
        )

        assert ctx["source_sender_identity"] == "multiple"  # unchanged, back-compat
        assert set(ctx["source_sender_identities"]) == {
            "6598150802@s.whatsapp.net",
            "6591153887@s.whatsapp.net",
        }
        assert ctx["owner_sender_identity"] == "6591153887@s.whatsapp.net"

    def test_absent_participants_omit_keys(self) -> None:
        """Single-message envelopes stay lean — no empty keys added."""
        from datetime import UTC, datetime

        from butlers.tools.switchboard.ingestion.ingest import _build_request_context

        envelope = _envelope({"identity": "6598150802@s.whatsapp.net"})
        ctx = _build_request_context(
            envelope,
            request_id=uuid.uuid4(),
            received_at=datetime(2026, 8, 1, 12, 50, tzinfo=UTC),
        )
        assert "source_sender_identities" not in ctx
        assert "owner_sender_identity" not in ctx


# ---------------------------------------------------------------------------
# Layer A: the WhatsApp connector populates participants
# ---------------------------------------------------------------------------


class TestWhatsAppBatchEnvelopeParticipants:
    def _connector(self) -> Any:
        from butlers.connectors.whatsapp_user_client import (
            WhatsAppUserClientConnector,
            WhatsAppUserClientConnectorConfig,
        )

        config = WhatsAppUserClientConnectorConfig(
            switchboard_mcp_url="http://localhost:1/mcp",
            endpoint_identity="whatsapp:+6591153887",
        )
        return WhatsAppUserClientConnector(config=config, db_pool=AsyncMock())

    def _events(self) -> list[dict[str, Any]]:
        return [
            {
                "message_id": "m1",
                "chat_jid": "6598150802-1386556114@g.us",
                "sender_jid": "6598150802@s.whatsapp.net",
                "timestamp": 1785000000,
                "content": {"text": "dinner tonight?"},
                "raw": {"is_from_me": False},
            },
            {
                "message_id": "m2",
                "chat_jid": "6598150802-1386556114@g.us",
                "sender_jid": "6591153887@s.whatsapp.net",
                "timestamp": 1785000060,
                "content": {"text": "yes!"},
                "raw": {"is_from_me": True},
            },
        ]

    def test_batch_envelope_lists_every_sender(self) -> None:
        conn = self._connector()
        env = conn._build_batch_envelope("6598150802-1386556114@g.us", self._events(), "batch:1")
        participants = env["sender"]["participants"]
        assert set(participants) == {
            "6598150802@s.whatsapp.net",
            "6591153887@s.whatsapp.net",
        }

    def test_owner_sender_id_from_is_from_me(self) -> None:
        """``is_from_me`` is the bridge's own owner marker — no phone compare."""
        conn = self._connector()
        env = conn._build_batch_envelope("6598150802-1386556114@g.us", self._events(), "batch:1")
        assert env["sender"]["owner_sender_id"] == "6591153887@s.whatsapp.net"

    def test_identity_stays_multiple_for_batches(self) -> None:
        conn = self._connector()
        env = conn._build_batch_envelope("6598150802-1386556114@g.us", self._events(), "batch:1")
        assert env["sender"]["identity"] == "multiple"

    def test_batch_envelope_validates_against_ingest_contract(self) -> None:
        """The envelope must survive ingest.v1 validation (extra="forbid")."""
        from butlers.tools.switchboard.routing.contracts import IngestEnvelopeV1

        conn = self._connector()
        env = conn._build_batch_envelope("6598150802-1386556114@g.us", self._events(), "batch:1")
        validated = IngestEnvelopeV1.model_validate(env)
        assert validated.sender.owner_sender_id == "6591153887@s.whatsapp.net"
        assert validated.sender.participants is not None

    def test_lid_senders_translate_to_phone_jids(self) -> None:
        """``@lid`` senders are opaque; only the phone JID form can resolve."""
        conn = self._connector()
        conn._lid_to_phone = {"164772343488740": "6598150802"}
        events = [
            {
                "message_id": "m1",
                "chat_jid": "6598150802-1386556114@g.us",
                "sender_jid": "164772343488740@lid",
                "timestamp": 1785000000,
                "content": {"text": "hi"},
                "raw": {"is_from_me": False},
            }
        ]
        env = conn._build_batch_envelope("6598150802-1386556114@g.us", events, "batch:1")
        assert "6598150802@s.whatsapp.net" in env["sender"]["participants"]

    def test_mapped_lid_is_normalized_in_history_and_text(self) -> None:
        """Spec: REQ-connector-base-spec-001."""
        conn = self._connector()
        conn._lid_to_phone["122204922638508"] = "6591111111"
        envelope = conn._build_batch_envelope(
            "group@g.us",
            [
                {
                    "message_id": "m1",
                    "sender_jid": "122204922638508:7@lid",
                    "content": {"text": "hello"},
                }
            ],
            "batch-1",
        )

        history = envelope["payload"]["raw"]["conversation_history"]
        assert history[0]["sender_identity"] == "6591111111@s.whatsapp.net"
        assert history[0]["sender"] == "Unknown WhatsApp sender 1"
        assert "122204922638508" not in envelope["payload"]["normalized_text"]

    def test_unmapped_lid_keeps_opaque_identity_out_of_llm_labels(self) -> None:
        """Spec: REQ-connector-base-spec-001."""
        conn = self._connector()
        envelope = conn._build_batch_envelope(
            "group@g.us",
            [
                {
                    "message_id": "m1",
                    "sender_jid": "122204922638508:7@lid",
                    "content": {"text": "hello"},
                }
            ],
            "batch-1",
        )

        history = envelope["payload"]["raw"]["conversation_history"]
        assert history[0]["sender_identity"] == "122204922638508@lid"
        assert history[0]["sender"] == "Unknown WhatsApp sender 1"
        assert "122204922638508" not in envelope["payload"]["normalized_text"]
        assert envelope["sender"]["participants"] == {
            "122204922638508@lid": "Unknown WhatsApp sender 1"
        }

    def test_device_ordinal_is_absent_from_history_identity_and_text(self) -> None:
        """Spec: REQ-connector-base-spec-001."""
        conn = self._connector()
        envelope = conn._build_batch_envelope(
            "group@g.us",
            [
                {
                    "message_id": "m1",
                    "sender_jid": "6591111111:7@s.whatsapp.net",
                    "content": {"text": "hello"},
                }
            ],
            "batch-1",
        )

        history = envelope["payload"]["raw"]["conversation_history"]
        assert history[0]["sender_identity"] == "6591111111@s.whatsapp.net"
        assert ":7" not in envelope["payload"]["normalized_text"]

    def test_unknown_group_speakers_have_stable_distinct_neutral_labels(self) -> None:
        """Spec: REQ-connector-base-spec-001."""
        conn = self._connector()
        envelope = conn._build_batch_envelope(
            "group@g.us",
            [
                {
                    "message_id": "m1",
                    "sender_jid": "first:7@lid",
                    "content": {"text": "one"},
                },
                {
                    "message_id": "m2",
                    "sender_jid": "second:4@lid",
                    "content": {"text": "two"},
                },
                {
                    "message_id": "m3",
                    "sender_jid": "first:9@lid",
                    "content": {"text": "three"},
                },
            ],
            "batch-1",
        )

        history = envelope["payload"]["raw"]["conversation_history"]
        assert [entry["sender"] for entry in history] == [
            "Unknown WhatsApp sender 1",
            "Unknown WhatsApp sender 2",
            "Unknown WhatsApp sender 1",
        ]
        assert "first" not in envelope["payload"]["normalized_text"]
        assert "second" not in envelope["payload"]["normalized_text"]


class TestDeviceSuffixedJids:
    """WhatsApp appends a device ordinal for non-primary devices.

    ``"6591153887:33@s.whatsapp.net"`` identifies a handset, not a person.
    Live data showed 16% of sender entries carrying one — including the
    owner's own, whose loss silently degrades outgoing interactions (direction
    weight 10.0) to incoming (1.0).
    """

    def test_phone_extraction_drops_device_ordinal(self) -> None:
        assert _extract_whatsapp_jid_phone("6591153887:33@s.whatsapp.net") == "6591153887"

    def test_phone_extraction_still_handles_plain_jid(self) -> None:
        assert _extract_whatsapp_jid_phone("6591153887@s.whatsapp.net") == "6591153887"

    def test_group_jid_still_yields_no_phone(self) -> None:
        assert _extract_whatsapp_jid_phone("6598150802-1386556114@g.us") is None

    def test_device_suffixed_jid_yields_phone_candidates(self) -> None:
        candidates = _channel_candidates("whatsapp_jid", "6591153887:33@s.whatsapp.net")
        assert ("phone_digits", "6591153887") in candidates


# ---------------------------------------------------------------------------
# Layer C: interaction_sync owner-direction detection
# ---------------------------------------------------------------------------


class TestOwnerDirectionFromConnector:
    """The connector-reported owner id outranks role-based detection.

    It is authoritative even when the owner entity carries no handle fact for
    the channel — which is the norm for WhatsApp, where zero ``has-handle``
    triples exist and resolution runs through phone numbers.
    """

    def _connector(self) -> Any:
        from butlers.connectors.whatsapp_user_client import (
            WhatsAppUserClientConnector,
            WhatsAppUserClientConnectorConfig,
        )

        return WhatsAppUserClientConnector(
            config=WhatsAppUserClientConnectorConfig(
                switchboard_mcp_url="http://localhost:1/mcp",
                endpoint_identity="whatsapp:+6591153887",
            ),
            db_pool=AsyncMock(),
        )

    def test_owner_detected_through_device_suffixed_lid(self) -> None:
        conn = self._connector()
        conn._lid_to_phone = {"122204922638508": "6591153887"}
        participants, owner = conn._extract_participants(
            [
                {
                    "message_id": "m1",
                    "sender_jid": "122204922638508:33@lid",
                    "timestamp": 1785000000,
                    "content": {"text": "on my way"},
                    "raw": {"is_from_me": True},
                }
            ]
        )
        assert owner == "6591153887@s.whatsapp.net"
        assert "6591153887@s.whatsapp.net" in participants

    def test_owner_survives_an_untranslatable_co_sender(self) -> None:
        """An unmappable LID must not cost the batch its direction signal."""
        conn = self._connector()
        conn._lid_to_phone = {"122204922638508": "6591153887"}
        _, owner = conn._extract_participants(
            [
                {
                    "message_id": "m1",
                    "sender_jid": "999999999999999@lid",  # unmapped
                    "raw": {"is_from_me": False},
                },
                {
                    "message_id": "m2",
                    "sender_jid": "122204922638508@lid",
                    "raw": {"is_from_me": True},
                },
            ]
        )
        assert owner == "6591153887@s.whatsapp.net"

    def test_unmappable_lid_is_retained_as_a_device_free_opaque_identity(self) -> None:
        """Spec: REQ-connector-base-spec-001."""
        conn = self._connector()
        conn._lid_to_phone = {}
        participants, _ = conn._extract_participants(
            [{"message_id": "m1", "sender_jid": "999999999999999@lid", "raw": {}}]
        )
        assert participants == {"999999999999999@lid": "Unknown WhatsApp sender 1"}
