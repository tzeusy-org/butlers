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

from butlers.identity import _channel_candidates, _normalize_phone_digits

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Layer D: phone normalisation for WhatsApp JID cross-reference
# ---------------------------------------------------------------------------


class TestPhoneNormalisation:
    """``has-phone`` triples are stored in wildly inconsistent formats."""

    @pytest.mark.parametrize(
        "stored",
        [
            "+65 9815 0802",  # spaced international (Google Contacts export)
            "+6598150802",  # compact international
            "6598150802",  # bare with country code
            "+65-9815-0802",  # dashed
            "(65) 9815 0802",  # parenthesised
        ],
    )
    def test_stored_formats_normalise_to_same_digits(self, stored: str) -> None:
        assert _normalize_phone_digits(stored) == "6598150802"

    def test_normalisation_drops_non_digits_only(self) -> None:
        assert _normalize_phone_digits("+1 (646) 693-8892") == "16466938892"

    def test_empty_and_junk_return_none(self) -> None:
        assert _normalize_phone_digits("") is None
        assert _normalize_phone_digits("not-a-phone") is None


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


# ---------------------------------------------------------------------------
# Layer C: interaction_sync reads the participant list, not "multiple"
# ---------------------------------------------------------------------------


class TestInteractionSyncSenderExtraction:
    """The job's inbox query must unnest source_sender_identities."""

    def _query(self) -> str:
        import sys

        mod = sys.modules.get("butlers.jobs._roster.relationship_jobs")
        if mod is None:
            from butlers.jobs._roster_loader import load_roster_jobs

            mod = load_roster_jobs("relationship")
        import inspect

        return inspect.getsource(mod.run_interaction_sync)

    def test_query_unnests_participant_identities(self) -> None:
        src = self._query()
        assert "source_sender_identities" in src, (
            "interaction_sync must read the per-sender identity list; reading only "
            "source_sender_identity resolves every batch against the literal 'multiple'"
        )

    def test_query_excludes_the_multiple_sentinel(self) -> None:
        src = self._query()
        assert "'unknown', 'multiple'" in src, (
            "the 'multiple' collapse sentinel must never be treated as a real sender"
        )

    def test_uses_canonical_resolver_not_bespoke_join(self) -> None:
        src = self._query()
        assert "resolve_contacts_by_channel_bulk" in src
        # The old exact-equality join missed telegram's "telegram:" prefix.
        assert "ef.object      = pairs.ci_value" not in src
