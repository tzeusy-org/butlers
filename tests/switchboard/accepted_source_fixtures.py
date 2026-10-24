"""Actual accepted-ingest and owner fixtures; never EvidenceProvider stand-ins."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from itertools import count
from uuid import uuid4

from butlers.core.goal_admission import GoalAdmission, SourceRef
from butlers.tools.switchboard.ingestion.accepted_source import (
    AcceptedSourceEvidence,
    ClosedGoalProfile,
)
from butlers.tools.switchboard.ingestion.ingest import ingest_v1

_event_hours = count()


async def accept(
    owner,
    *,
    text="owner instruction; outside data",
    markers=None,
    reference=None,
    sender="987",
    channel="telegram_bot",
    provider="telegram",
    endpoint="telegram:bot:closed-test",
    conversation="telegram:100",
    raw=None,
    attachments=None,
    event_id=None,
    sender_extra=None,
    external_thread_id=None,
):
    event = {
        "external_event_id": event_id or str(uuid4()),
        "external_conversation_id": conversation,
        "reply_target_ref": "100:current",
        # Each fixture event is genuinely distinct even when its visible text
        # matches: real ingest also dedupes same-sender/text within an hour.
        "observed_at": (
            datetime(2020, 1, 1, tzinfo=UTC) + timedelta(hours=next(_event_hours))
        ).isoformat(),
    }
    if external_thread_id is not None:
        event["external_thread_id"] = external_thread_id
    if reference is not None:
        event["in_reply_to_ref"] = reference
    payload = {"raw": {} if raw is None else raw, "normalized_text": text}
    if markers is not None:
        payload["untrusted_spans"] = markers
    if attachments is not None:
        payload["attachments"] = attachments
    result = await ingest_v1(
        owner,
        {
            "schema_version": "ingest.v1",
            "source": {"channel": channel, "provider": provider, "endpoint_identity": endpoint},
            "event": event,
            "sender": {"identity": sender, **(sender_extra or {})},
            "payload": payload,
            "control": {"idempotency_key": str(uuid4())},
        },
        enable_thread_affinity=False,
    )
    return SourceRef("message", str(result.request_id))


@asynccontextmanager
async def verified_owner(pool):
    entity = await pool.fetchval(
        "INSERT INTO public.entities(canonical_name,roles) VALUES($1,ARRAY['owner']) RETURNING id",
        "closed-harness-owner-" + str(uuid4()),
    )
    try:
        for predicate, value in (
            ("has-handle", "telegram:987"),
            ("has-email", "owner@example.test"),
        ):
            await pool.execute(
                "INSERT INTO relationship.entity_facts(subject,predicate,object,object_kind,src) "
                "VALUES($1,$2,$3,'literal','test')",
                entity,
                predicate,
                value,
            )
        yield entity
    finally:
        await pool.execute("DELETE FROM relationship.entity_facts WHERE subject=$1", entity)
        await pool.execute("DELETE FROM public.entities WHERE id=$1", entity)


class ObservedSource(AcceptedSourceEvidence):
    """Observe the real reader output; never replace its lookup or verification."""

    def __init__(self, **kwargs):
        super().__init__(profile=ClosedGoalProfile.HARNESS, **kwargs)
        self.inputs = []

    async def load(self, connection, source):
        result = await super().load(connection, source)
        self.inputs.append(result)
        return result


async def admit_source(runtime, source, *, evidence=None, catalog=None, attempt=None):
    reader = evidence or ObservedSource()
    result = await GoalAdmission(runtime, reader, catalog).admit_submission(
        source, attempt or uuid4()
    )
    return result, reader
