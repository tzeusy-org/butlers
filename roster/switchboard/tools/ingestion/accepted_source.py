"""Server-owned accepted-source reader for the dark M1 closed harness only.

Not an MCP/HTTP tool, not called by ingest/schedules/dashboard, and not a
profile deployment switch. Local trusted construction is the only caller.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from uuid import UUID

from butlers.core.fact_authority import FactWriteContext, dashboard_origin_digest
from butlers.core.goal_admission import Evidence, EvidenceRefused, SourceRef, digest
from butlers.identity import resolve_owner_channel_via_definer
from butlers.tools.switchboard.connector.heartbeat import (
    VALID_CONNECTOR_TYPES,
    registered_channel_kind,
)
from butlers.tools.switchboard.ingestion.feed_admission import DeterministicFeeds
from butlers.tools.switchboard.ingestion.goal_input import (
    GoalInput,
    prepare_goal_input,
    scoped_reply_ref,
)


class ClosedGoalProfile(Enum):
    HARNESS = "closed_trusted_harness"


@dataclass(frozen=True)
class AcceptedEvidence(Evidence):
    prepared_input: GoalInput | None = None


def _object(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise EvidenceRefused("accepted_source_unavailable") from None
    if not isinstance(value, dict):
        raise EvidenceRefused("accepted_source_unavailable")
    return value


def _connector(channel, provider):
    if channel == "email" and provider == "gmail":
        return "gmail"
    if channel in {"telegram_bot", "telegram_user_client"} and provider == "telegram":
        return channel
    if channel in {"whatsapp", "whatsapp_user_client"} and provider == "whatsapp":
        return "whatsapp_user_client"
    if channel == "dashboard" and provider == "internal":
        return "dashboard"
    if channel == provider and channel in VALID_CONNECTOR_TYPES:
        return channel
    raise EvidenceRefused("accepted_source_identity_invalid")


async def _live_owner(connection, expected=None):
    rows = await connection.fetch(
        "SELECT id,created_at FROM public.entities WHERE 'owner'=ANY(roles) "
        "AND metadata->>'merged_into' IS NULL AND metadata->>'deleted_at' IS NULL "
        "ORDER BY id FOR KEY SHARE"
    )
    if len(rows) != 1 or (expected is not None and rows[0]["id"] != expected):
        return None
    return rows[0]


class AcceptedSourceEvidence:
    def __init__(self, *, profile: ClosedGoalProfile, feeds: DeterministicFeeds | None = None):
        if profile is not ClosedGoalProfile.HARNESS:
            raise EvidenceRefused("goal_profile_unavailable")
        self.feeds = feeds

    async def _dashboard_owner(self, connection, raw, text):
        metadata = (raw.get("payload") or {}).get("raw") or {}
        try:
            message_id = UUID(metadata["message_id"])
        except (KeyError, ValueError, TypeError):
            return None
        row = await connection.fetchrow(
            "SELECT content,role,fact_owner_admission FROM public.dashboard_messages WHERE id=$1",
            message_id,
        )
        if not row or row["role"] != "user":
            return None
        stamp = row["fact_owner_admission"]
        if not isinstance(stamp, dict):
            return None
        try:
            report = FactWriteContext.from_record(stamp)
        except (ValueError, TypeError):
            raise EvidenceRefused("owner_source_unavailable") from None
        origin_digest = dashboard_origin_digest(raw)
        if (
            origin_digest is None
            or report.live_entity_id is None
            or not report.owner_class
            or row["content"] != metadata.get("message")
            or stamp.get("accepted_text_digest") != hashlib.sha256(text.encode()).hexdigest()
            or stamp.get("accepted_origin_digest") != origin_digest
        ):
            return None
        live = await _live_owner(connection, report.live_entity_id)
        return live["id"] if live and live["created_at"] == report.entity_created_at else None

    async def load(self, connection, source: SourceRef) -> AcceptedEvidence:
        if not connection.is_in_transaction():
            raise EvidenceRefused("accepted_source_transaction_required")
        if source.kind != "message":
            raise EvidenceRefused("accepted_source_identity_invalid")
        try:
            request_id = UUID(source.id)
        except ValueError:
            raise EvidenceRefused("accepted_source_identity_invalid") from None
        rows = await connection.fetch(
            "SELECT id,request_context,raw_payload,normalized_text,attachments "
            "FROM switchboard.message_inbox WHERE request_context->>'request_id'=$1 "
            "AND direction='inbound' FOR UPDATE",
            str(request_id),
        )
        if len(rows) != 1:
            raise EvidenceRefused("accepted_source_unavailable")
        row = rows[0]
        context, raw = _object(row["request_context"]), _object(row["raw_payload"])
        src, event, sender = (_object(raw.get(k)) for k in ("source", "event", "sender"))
        channel, endpoint = context.get("source_channel"), context.get("source_endpoint_identity")
        if (
            not isinstance(channel, str)
            or not isinstance(endpoint, str)
            or not endpoint
            or src.get("channel") != channel
            or src.get("endpoint_identity") != endpoint
            or sender.get("identity") != context.get("source_sender_identity")
            or context.get("ingestion_tier") == "metadata"
        ):
            raise EvidenceRefused("accepted_source_identity_invalid")
        connector = _connector(channel, src.get("provider"))
        kind = await registered_channel_kind(connection, connector, endpoint)
        text = row["normalized_text"]
        if not isinstance(text, str):
            raise EvidenceRefused("accepted_input_unavailable")
        clean_context = {k: v for k, v in context.items() if k != "_fact_source_report"}
        bound_digest = digest([clean_context, raw, text, row["attachments"]])
        payload = _object(raw.get("payload"))
        retained = {"raw": payload.get("raw"), "normalized_text": text}
        if row["attachments"] is not None:
            retained["attachments"] = row["attachments"]
        if "untrusted_spans" in payload:
            retained["untrusted_spans"] = payload["untrusted_spans"]
        reference = event.get("in_reply_to_ref")
        if reference is not None and (not isinstance(reference, str) or not reference):
            raise EvidenceRefused("reply_source_unavailable")
        entity = None
        if kind == "message":
            if channel == "dashboard":
                entity = await self._dashboard_owner(connection, raw, text)
            else:
                resolved = await resolve_owner_channel_via_definer(
                    connection, channel, sender["identity"], raise_on_error=True
                )
                if resolved:
                    live = await _live_owner(connection, resolved[0].entity_id)
                    entity = live["id"] if live else None
                if channel == "email":
                    # The retained From address and provider-domain authentication
                    # verdict are not authenticated owner-principal evidence (mail
                    # import can supply those headers). No owner stamp exists here.
                    entity = None
        prepared = prepare_goal_input(
            source.id, bound_digest, retained, reference, owner=entity is not None
        )
        if kind == "feed":
            if self.feeds is None:
                raise EvidenceRefused("feed_receiver_unavailable")
            obligation = await self.feeds.record(source, connector, endpoint, raw)
            return AcceptedEvidence(
                "feed",
                endpoint,
                outside_content=True,
                obligation=obligation,
                record_only=obligation is None,
                prepared_input=prepared,
            )
        delivery, referenced_exposed = await self._reply(
            connection, context, raw, reference, entity
        )
        return AcceptedEvidence(
            "message",
            endpoint,
            owner=entity is not None,
            outside_content=prepared.marked or referenced_exposed,
            reply_delivery=delivery,
            owner_entity=entity,
            owner_channel=channel,
            prepared_input=prepared,
        )

    async def _reply(self, connection, context, raw, reference, entity):
        if reference is None:
            return None, False
        conversation = context.get("external_conversation_id")
        if not isinstance(conversation, str) or not conversation:
            return None, False
        channel, endpoint = context["source_channel"], context["source_endpoint_identity"]
        if entity is not None:
            scoped = scoped_reply_ref(
                transport=raw["source"]["provider"],
                endpoint=endpoint,
                conversation=conversation,
                recipient_entity=entity,
                recipient_channel=channel,
                locator=reference,
            )
            result = await connection.fetchrow(
                "SELECT e.delivery_ref,g.exposed FROM switchboard.effects e "
                "JOIN switchboard.goals g ON g.id=e.goal_id "
                "WHERE e.delivery_ref=$1 AND e.recipient_entity=$2 AND e.recipient_channel=$3",
                scoped,
                entity,
                channel,
            )
            if result:
                return result["delivery_ref"], result["exposed"] is not False
        # Provider-native inbound lookup: scope before reading any content/stamp.
        event_id = reference
        if raw["source"]["provider"] == "telegram":
            chat, separator, event_id = reference.partition(":")
            if not separator or conversation != f"telegram:{chat}":
                return None, False
        referenced = await connection.fetch(
            "SELECT request_context FROM switchboard.message_inbox WHERE direction='inbound' "
            "AND request_context->>'source_channel'=$1 "
            "AND request_context->>'source_endpoint_identity'=$2 "
            "AND request_context->>'external_conversation_id'=$3 "
            "AND raw_payload->'source'->>'provider'=$4 "
            "AND raw_payload->'event'->>'external_event_id'=$5",
            channel,
            endpoint,
            conversation,
            raw["source"]["provider"],
            event_id,
        )
        if not referenced:
            return None, False
        # There is no trusted input stamp on legacy history. Refuse to upgrade
        # it from third-party/unknown merely because a role-looking label exists.
        return None, True
