"""Deterministic resolver/projector: connector evidence -> works + taste_signals.

No LLM involved anywhere in this module. Every function here is a pure,
idempotent projection: replaying the same evidence rows (a fresh backfill, a
second projector pass, two concurrent passes) converges on the same ledger
state rather than growing it.

Two evidence sources feed the ledger:

- ``connectors.spotify_listening_sessions`` (core_079): session-level, no
  stable per-track id — only ordered track *names*. Every mention becomes its
  own ``works`` row (``external_ids = '{}'``) because the source genuinely has
  no better identity to offer (see ``works.external_ids`` in
  ``roster/lifestyle/migrations/002_taste_ledger.py``).
- ``connectors.spotify_track_plays`` (core_230): per-play, keyed on the
  track's stable URI, carrying resolved ``completion_ratio``/``skipped``.
  Works from this source dedupe on ``(kind, external_ids->>'primary')``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import asyncpg

_SESSIONS_SOURCE_TABLE = "spotify_listening_sessions"
_PLAYS_SOURCE_TABLE = "spotify_track_plays"


@dataclass(frozen=True)
class BackfillResult:
    """Count of ledger rows actually created by one resolver pass."""

    works_created: int
    signals_created: int


async def _get_or_create_work(
    pool: asyncpg.Pool,
    *,
    kind: str,
    title: str | None,
    external_ids: dict[str, Any],
) -> tuple[Any, bool]:
    """Return ``(work_id, created)``.

    ``ON CONFLICT (kind, (external_ids ->> 'primary')) DO NOTHING`` per the
    ledger's concurrency contract: a resolved external id converges on one
    row; an unresolved one (``external_ids = {}``, ``primary`` absent) never
    conflicts, so every unresolved mention gets its own row by design.
    """
    row = await pool.fetchrow(
        """
        INSERT INTO works (kind, title, external_ids)
        VALUES ($1, $2, $3)
        ON CONFLICT (kind, (external_ids ->> 'primary')) DO NOTHING
        RETURNING id
        """,
        kind,
        title,
        external_ids,
    )
    if row is not None:
        return row["id"], True

    primary = external_ids.get("primary")
    existing = await pool.fetchrow(
        "SELECT id FROM works WHERE kind = $1 AND external_ids ->> 'primary' = $2",
        kind,
        primary,
    )
    assert existing is not None, "ON CONFLICT target implies a matching row exists"
    return existing["id"], False


async def _insert_signal(
    pool: asyncpg.Pool,
    *,
    work_id: Any,
    signal_kind: str,
    source_table: str,
    source_ref: str,
    occurred_at: Any,
    strength: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Insert one taste_signal. Returns True iff a new row was created.

    ``idempotency_key`` is ``<source_table>:<source_ref>:<signal_kind>`` —
    unique per evidence unit, so two concurrent projector passes over the same
    batch converge on exactly one row (``ON CONFLICT (idempotency_key) DO
    NOTHING``).
    """
    idempotency_key = f"{source_table}:{source_ref}:{signal_kind}"
    result = await pool.execute(
        """
        INSERT INTO taste_signals (
            work_id, signal_kind, source_table, source_ref,
            idempotency_key, occurred_at, strength, metadata
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (idempotency_key) DO NOTHING
        """,
        work_id,
        signal_kind,
        source_table,
        source_ref,
        idempotency_key,
        occurred_at,
        strength,
        metadata or {},
    )
    return result == "INSERT 0 1"


async def _already_projected(pool: asyncpg.Pool, idempotency_key: str) -> bool:
    return (
        await pool.fetchval(
            "SELECT 1 FROM taste_signals WHERE idempotency_key = $1", idempotency_key
        )
        is not None
    )


async def _project_unresolved_session_signal(
    pool: asyncpg.Pool,
    *,
    title: str,
    source_ref: str,
    occurred_at: Any,
) -> tuple[bool, bool]:
    """Atomically create one unresolved work and its source-owned signal.

    Unresolved works intentionally have no global identity, so their source
    signal key is the only stable identity available. A transaction-scoped
    advisory lock serializes concurrent projectors for that key and prevents
    the loser from leaving an orphan work behind.
    """
    signal_kind = "session_track"
    idempotency_key = f"{_SESSIONS_SOURCE_TABLE}:{source_ref}:{signal_kind}"
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", idempotency_key
        )
        if await _already_projected(connection, idempotency_key):
            return False, False
        work_id, work_created = await _get_or_create_work(
            connection, kind="track", title=title, external_ids={}
        )
        signal_created = await _insert_signal(
            connection,
            work_id=work_id,
            signal_kind=signal_kind,
            source_table=_SESSIONS_SOURCE_TABLE,
            source_ref=source_ref,
            occurred_at=occurred_at,
        )
        return work_created, signal_created


async def backfill_from_listening_sessions(
    pool: asyncpg.Pool, *, limit: int | None = None
) -> BackfillResult:
    """Project ``connectors.spotify_listening_sessions`` rows into the ledger.

    Every ``track_names`` entry becomes one ``works`` row (unresolved: no
    stable id survives session aggregation) and one ``taste_signals`` row.
    Safe to call repeatedly: rows already projected are skipped via the
    ``taste_signals.idempotency_key`` check, so a second pass over the same
    evidence creates nothing new.
    """
    query = """
        SELECT idempotency_key, track_names, started_at
        FROM connectors.spotify_listening_sessions
        ORDER BY started_at
    """
    if limit is not None:
        query += " LIMIT $1"
        rows = await pool.fetch(query, limit)
    else:
        rows = await pool.fetch(query)

    works_created = 0
    signals_created = 0
    for row in rows:
        track_names = row["track_names"] or []
        session_key = row["idempotency_key"]
        for index, name in enumerate(track_names):
            if not name:
                continue
            source_ref = f"{session_key}:{index}"
            created, inserted = await _project_unresolved_session_signal(
                pool, title=name, source_ref=source_ref, occurred_at=row["started_at"]
            )
            if created:
                works_created += 1
            if inserted:
                signals_created += 1

    return BackfillResult(works_created=works_created, signals_created=signals_created)


def _play_signal_kind(*, observation_precision: str, skipped: bool | None) -> str:
    if observation_precision == "play_only" or skipped is None:
        return "play_only"
    return "listen_skipped" if skipped else "listen_completed"


async def backfill_from_track_plays(
    pool: asyncpg.Pool, *, limit: int | None = None
) -> BackfillResult:
    """Project closed ``connectors.spotify_track_plays`` rows into the ledger.

    Works dedupe on the play's stable ``track_uri``
    (``external_ids = {"primary": track_uri}``); ``strength`` carries the
    play's ``completion_ratio`` (NULL for ``play_only`` gap-fill evidence).
    Open (not-yet-closed) plays are skipped — closing is what resolves the
    signal.
    """
    query = """
        SELECT endpoint_identity, track_uri, track_name, first_seen_ms,
               completion_ratio, skipped, observation_precision, recorded_at
        FROM connectors.spotify_track_plays
        WHERE closed_at IS NOT NULL
        ORDER BY recorded_at
    """
    if limit is not None:
        query += " LIMIT $1"
        rows = await pool.fetch(query, limit)
    else:
        rows = await pool.fetch(query)

    works_created = 0
    signals_created = 0
    for row in rows:
        source_ref = f"{row['endpoint_identity']}:{row['track_uri']}:{row['first_seen_ms']}"
        signal_kind = _play_signal_kind(
            observation_precision=row["observation_precision"], skipped=row["skipped"]
        )
        idempotency_key = f"{_PLAYS_SOURCE_TABLE}:{source_ref}:{signal_kind}"
        if await _already_projected(pool, idempotency_key):
            continue

        work_id, created = await _get_or_create_work(
            pool,
            kind="track",
            title=row["track_name"],
            external_ids={"primary": row["track_uri"]},
        )
        if created:
            works_created += 1

        inserted = await _insert_signal(
            pool,
            work_id=work_id,
            signal_kind=signal_kind,
            source_table=_PLAYS_SOURCE_TABLE,
            source_ref=source_ref,
            occurred_at=row["recorded_at"],
            strength=row["completion_ratio"],
            metadata={"observation_precision": row["observation_precision"]},
        )
        if inserted:
            signals_created += 1

    return BackfillResult(works_created=works_created, signals_created=signals_created)
