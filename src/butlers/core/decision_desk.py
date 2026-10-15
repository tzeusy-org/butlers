"""Owner Decision Desk intent store (bu-ckkpz.3, ``REQ-owner-decision-desk-001``).

The runtime never reaches the Beads tracker. An owner's choice, from the
dashboard API or a Telegram one-tap, is recorded here as a
``switchboard.decision_intents`` row. The one management workload that holds the
tracker credential (``scripts/beads_decision_applier.py`` in the beads CronJob)
applies it with ``bd`` and records the outcome. Recording is validated against
the same decision digest the Decisions lane renders, so an intent can only name
an option the owner was actually shown.

Recording is idempotent per bead: the database admits one live
(``pending``/``applying``/``applied``) intent per bead, an identical repeat
returns it, and a different option is a conflict. A ``failed`` intent never
blocks a new choice.
"""

from __future__ import annotations

import json
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

import asyncpg

from butlers.jobs.decision_review import DecisionBead, DecisionDigest, compute_decision_digest

IntentSource = Literal["dashboard", "telegram"]
IntentStatus = Literal["pending", "applying", "applied", "failed", "superseded"]

LIVE_STATUSES: frozenset[str] = frozenset({"pending", "applying", "applied"})
# Two cycles of the enabled development bridge's five-minute export cadence.
# These operational windows are not a measured worst-case export-lag bound.
_REOPEN_GRACE = timedelta(minutes=10)
_REOPEN_SNAPSHOT_AGE = timedelta(minutes=10)

_INTENT_COLUMNS = (
    "id, bead_id, option, source, actor, prompt_id, status, attempts, failure_reason, "
    "last_error, created_at, claimed_at, finished_at, updated_at"
)


class DecisionIntentError(Exception):
    """Recording refused. ``reason`` is a stable, categorical code.

    ``kind`` tells an HTTP caller how to answer: ``unavailable`` (the digest
    cannot be read), ``invalid`` (the bead or option fails validation) or
    ``conflict`` (another live intent exists, or the offered options changed).
    """

    def __init__(
        self,
        reason: str,
        *,
        kind: Literal["unavailable", "invalid", "conflict"],
        existing: DecisionIntent | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.kind = kind
        self.existing = existing


@dataclass(frozen=True)
class DecisionIntent:
    id: uuid.UUID
    bead_id: str
    option: str
    source: str
    actor: str
    prompt_id: uuid.UUID | None
    status: str
    attempts: int
    failure_reason: str | None
    last_error: str | None
    created_at: datetime
    claimed_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime

    @classmethod
    def from_row(cls, row: asyncpg.Record | dict[str, Any]) -> DecisionIntent:
        return cls(**{field: row[field] for field in cls.__dataclass_fields__})


@dataclass(frozen=True)
class DecisionPrompt:
    id: uuid.UUID
    bead_id: str
    options: tuple[str, ...]
    default_option: str
    created_at: datetime
    delivery_outcome: str | None
    delivered_at: datetime | None


@dataclass(frozen=True)
class RecordResult:
    intent: DecisionIntent
    created: bool


def find_open_decision(digest: DecisionDigest, bead_id: str) -> DecisionBead:
    """Return the digest's open decision *bead_id*, or raise the named refusal."""
    if not digest.available:
        raise DecisionIntentError("decisions_unavailable", kind="unavailable")
    for bead in digest.open_decisions:
        if bead.id == bead_id:
            if not bead.structured_details_available or bead.options is None:
                raise DecisionIntentError("structured_details_unavailable", kind="invalid")
            return bead
    raise DecisionIntentError("decision_not_open", kind="invalid")


async def live_intent(
    pool: asyncpg.Pool | asyncpg.Connection, bead_id: str
) -> DecisionIntent | None:
    row = await pool.fetchrow(
        f"SELECT {_INTENT_COLUMNS} FROM switchboard.decision_intents "
        "WHERE bead_id = $1 AND status = ANY($2::text[])",
        bead_id,
        sorted(LIVE_STATUSES),
    )
    return DecisionIntent.from_row(row) if row is not None else None


async def record_decision_intent(
    pool: asyncpg.Pool,
    *,
    bead_id: str,
    option: str,
    source: IntentSource,
    actor: str,
    prompt_id: uuid.UUID | None = None,
    digest: DecisionDigest | None = None,
) -> RecordResult:
    """Record one owner choice for *bead_id*. Raises :class:`DecisionIntentError`.

    *digest* defaults to a fresh :func:`compute_decision_digest`; callers that
    already hold one (or tests) pass it in.
    """
    digest = digest if digest is not None else compute_decision_digest()
    async with _intent_transaction(pool) as connection:
        await _reconcile_reopened(connection, [bead_id], digest)
        return await _record_locked(
            connection,
            bead_id=bead_id,
            option=option,
            source=source,
            actor=actor,
            prompt_id=prompt_id,
            digest=digest,
        )


@asynccontextmanager
async def _intent_transaction(pool: asyncpg.Pool | asyncpg.Connection):
    async with AsyncExitStack() as stack:
        connection = (
            await stack.enter_async_context(pool.acquire())
            if isinstance(pool, asyncpg.Pool)
            else pool
        )
        refusal = None
        async with connection.transaction():
            try:
                yield connection
            except DecisionIntentError as exc:
                # A named choice refusal must not roll back proved reopening
                # metadata. Database errors still abort the whole transaction.
                refusal = exc
        if refusal is not None:
            raise refusal


def _aware(value: datetime | None) -> bool:
    return isinstance(value, datetime) and value.utcoffset() is not None


async def _reconcile_reopened(
    connection: asyncpg.Connection, bead_ids: list[str], digest: DecisionDigest
) -> None:
    # Lock history without a status filter: a waiter must observe the row after
    # a concurrent applied -> superseded transition, not miss its old prompt.
    rows = await connection.fetch(
        f"SELECT {_INTENT_COLUMNS} FROM switchboard.decision_intents "
        "WHERE bead_id = ANY($1::text[]) ORDER BY bead_id, id FOR UPDATE",
        sorted(set(bead_ids)),
    )
    exported = digest.export_as_of
    if not (
        digest.available
        and _aware(exported)
        and _aware(digest.checked_at)
        and timedelta(0) <= digest.checked_at - exported <= _REOPEN_SNAPSHOT_AGE
    ):
        return
    open_beads = {bead.id: bead for bead in digest.open_decisions}
    for row in rows:
        bead = open_beads.get(row["bead_id"])
        finished = row["finished_at"]
        if (
            row["status"] == "applied"
            and bead is not None
            and _aware(finished)
            and _aware(bead.updated_at)
            and finished < bead.updated_at <= exported
            and exported - finished >= _REOPEN_GRACE
        ):
            await connection.execute(
                "UPDATE switchboard.decision_intents SET status = 'superseded', updated_at = now() "
                "WHERE id = $1 AND status = 'applied'",
                row["id"],
            )


async def _record_locked(
    connection: asyncpg.Connection,
    *,
    bead_id: str,
    option: str,
    source: IntentSource,
    actor: str,
    prompt_id: uuid.UUID | None,
    digest: DecisionDigest,
    expected_options: tuple[str, ...] | None = None,
) -> RecordResult:
    if prompt_id is not None:
        consumed = await connection.fetchrow(
            f"SELECT {_INTENT_COLUMNS} FROM switchboard.decision_intents "
            "WHERE prompt_id = $1 AND status = 'superseded' LIMIT 1",
            prompt_id,
        )
        if consumed is not None:
            raise DecisionIntentError(
                "intent_conflict:superseded",
                kind="conflict",
                existing=DecisionIntent.from_row(consumed),
            )
    # A live intent still answers before digest validation: after closure an
    # identical repeat remains already recorded even if the digest is unavailable.
    existing = await live_intent(connection, bead_id)
    if existing is not None:
        return _same_or_conflict(existing, option)

    bead = find_open_decision(digest, bead_id)
    if expected_options is not None and tuple(bead.options or ()) != expected_options:
        raise DecisionIntentError("options_changed", kind="conflict")
    if option not in (bead.options or ()):
        raise DecisionIntentError("option_not_offered", kind="invalid")

    try:
        # An insert race aborts only this savepoint, so the winner can be read
        # from the outer transaction without swallowing a failed transaction.
        async with connection.transaction():
            row = await connection.fetchrow(
                f"""
            INSERT INTO switchboard.decision_intents (bead_id, option, source, actor, prompt_id)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING {_INTENT_COLUMNS}
            """,
                bead_id,
                option,
                source,
                actor,
                prompt_id,
            )
    except asyncpg.UniqueViolationError:
        # A concurrent recorder won the live-intent slot between our read and
        # insert; answer from its row exactly as a sequential repeat would.
        existing = await live_intent(connection, bead_id)
        if existing is None:
            raise
        return _same_or_conflict(existing, option)
    return RecordResult(DecisionIntent.from_row(row), created=True)


def _same_or_conflict(existing: DecisionIntent, option: str) -> RecordResult:
    if existing.option == option:
        return RecordResult(existing, created=False)
    raise DecisionIntentError(
        f"intent_conflict:{existing.status}", kind="conflict", existing=existing
    )


async def latest_intents(
    pool: asyncpg.Pool | asyncpg.Connection,
    bead_ids: list[str],
    *,
    digest: DecisionDigest | None = None,
) -> dict[str, DecisionIntent]:
    """Map each bead to its live intent, else its newest terminal history."""
    if not bead_ids:
        return {}
    if digest is not None:
        async with _intent_transaction(pool) as connection:
            await _reconcile_reopened(connection, bead_ids, digest)
            return await latest_intents(connection, bead_ids)
    rows = await pool.fetch(
        f"""
        SELECT DISTINCT ON (bead_id) {_INTENT_COLUMNS}
        FROM switchboard.decision_intents
        WHERE bead_id = ANY($1::text[])
        ORDER BY bead_id, (status IN ('pending', 'applying', 'applied')) DESC, created_at DESC
        """,
        bead_ids,
    )
    return {row["bead_id"]: DecisionIntent.from_row(row) for row in rows}


async def get_prompt(
    pool: asyncpg.Pool | asyncpg.Connection, prompt_id: uuid.UUID
) -> DecisionPrompt | None:
    row = await pool.fetchrow(
        """
        SELECT id, bead_id, options, default_option, created_at, delivery_outcome, delivered_at
        FROM switchboard.decision_prompts WHERE id = $1
        """,
        prompt_id,
    )
    if row is None:
        return None
    return DecisionPrompt(
        id=row["id"],
        bead_id=row["bead_id"],
        options=tuple(_json_list(row["options"])),
        default_option=row["default_option"],
        created_at=row["created_at"],
        delivery_outcome=row["delivery_outcome"],
        delivered_at=row["delivered_at"],
    )


def _json_list(value: Any) -> list[str]:
    # Pools without the JSONB codec (bare asyncpg in tests, the applier) hand
    # JSONB back as text.
    if isinstance(value, str):
        value = json.loads(value)
    return [str(item) for item in value]


async def record_prompt_choice(
    pool: asyncpg.Pool,
    *,
    prompt: DecisionPrompt,
    option_index: int,
    actor: str,
    digest: DecisionDigest | None = None,
) -> RecordResult:
    """Record the Telegram choice at *option_index* of *prompt*'s snapshot.

    The tap names an index into the options the owner was shown. When the
    bead's current options no longer equal that snapshot the tap is refused
    (``options_changed``) rather than mapped onto a different option.
    """
    if not 0 <= option_index < len(prompt.options):
        raise DecisionIntentError("option_not_offered", kind="invalid")
    digest = digest if digest is not None else compute_decision_digest()
    async with _intent_transaction(pool) as connection:
        await _reconcile_reopened(connection, [prompt.bead_id], digest)
        return await _record_locked(
            connection,
            bead_id=prompt.bead_id,
            option=prompt.options[option_index],
            source="telegram",
            actor=actor,
            prompt_id=prompt.id,
            digest=digest,
            expected_options=prompt.options,
        )


__all__ = [
    "LIVE_STATUSES",
    "DecisionIntent",
    "DecisionIntentError",
    "DecisionPrompt",
    "RecordResult",
    "find_open_decision",
    "get_prompt",
    "latest_intents",
    "live_intent",
    "record_decision_intent",
    "record_prompt_choice",
]
