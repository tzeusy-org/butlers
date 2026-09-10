"""Deterministic resolver/projector: connector evidence -> works + taste_signals.

No LLM involved anywhere in this module. Every function here is a pure,
idempotent projection: replaying the same evidence rows (a fresh backfill, a
second projector pass, two concurrent passes) converges on the same ledger
state rather than growing it.

Two evidence sources feed the ledger:

- ``connectors.spotify_listening_sessions`` (core_079): session-level, no
  stable per-track id — only ordered track *names*. A mention not covered by
  modern per-play evidence becomes its own ``works`` row
  (``external_ids = '{}'``) because the source genuinely has no better
  identity to offer (see ``works.external_ids`` in
  ``roster/lifestyle/migrations/002_taste_ledger.py``).
- ``connectors.spotify_track_plays`` (core_230): per-play, keyed on the
  track's stable URI, carrying resolved ``completion_ratio``/``skipped``.
  Works from this source dedupe on ``(kind, external_ids->>'primary')``.
"""

from __future__ import annotations

from collections import Counter
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
    session_key: str,
    occurrence_number: int,
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
        modern_count = await connection.fetchval(
            """
            SELECT count(*)
            FROM connectors.spotify_listening_sessions session
            JOIN connectors.spotify_track_plays play
              ON play.endpoint_identity = session.endpoint_identity
             AND play.track_name = $2
             AND play.first_seen_ms BETWEEN
                 (extract(epoch FROM session.started_at) * 1000)::bigint
                 AND (extract(epoch FROM session.ended_at) * 1000)::bigint
            WHERE session.idempotency_key = $1
            """,
            session_key,
            title,
        )
        if modern_count >= occurrence_number:
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

    A track-name occurrence is treated as legacy evidence only when no
    same-endpoint per-play row with that name falls inside the session. The
    per-play source has the stable track URI and therefore takes precedence;
    this prevents modern connector history from being projected once as an
    unresolved session mention and again as a resolved play. Repeated names
    are reconciled by count so one modern play suppresses only one session
    occurrence.

    Remaining ``track_names`` entries each become one unresolved ``works`` row
    and one ``taste_signals`` row. Safe to call repeatedly: rows already
    projected are skipped via the ``taste_signals.idempotency_key`` check.
    """
    query = """
        SELECT
            s.idempotency_key,
            s.track_names,
            s.started_at,
            ARRAY(
                SELECT p.track_name
                FROM connectors.spotify_track_plays p
                WHERE p.endpoint_identity = s.endpoint_identity
                  AND p.track_name IS NOT NULL
                  AND p.first_seen_ms BETWEEN
                      (extract(epoch FROM s.started_at) * 1000)::bigint
                      AND (extract(epoch FROM s.ended_at) * 1000)::bigint
                ORDER BY p.first_seen_ms
            ) AS modern_track_names
        FROM connectors.spotify_listening_sessions s
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
        modern_names = Counter(row["modern_track_names"] or [])
        session_key = row["idempotency_key"]
        for index, name in enumerate(track_names):
            if not name:
                continue
            if modern_names[name] > 0:
                modern_names[name] -= 1
                continue
            source_ref = f"{session_key}:{index}"
            created, inserted = await _project_unresolved_session_signal(
                pool,
                title=name,
                source_ref=source_ref,
                occurred_at=row["started_at"],
                session_key=session_key,
                occurrence_number=sum(candidate == name for candidate in track_names[: index + 1]),
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
        async with pool.acquire() as connection, connection.transaction():
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", idempotency_key
            )
            session_source_refs = await connection.fetch(
                """
                SELECT session.idempotency_key || ':' || (entry.ordinality - 1) AS source_ref
                FROM connectors.spotify_listening_sessions session
                CROSS JOIN LATERAL (
                    SELECT track.ordinality,
                           row_number() OVER (ORDER BY track.ordinality) AS occurrence_number
                    FROM jsonb_array_elements_text(session.track_names)
                         WITH ORDINALITY AS track(track_name, ordinality)
                    WHERE track.track_name = $3
                ) entry
                WHERE session.endpoint_identity = $1
                  AND $2::bigint BETWEEN
                      (extract(epoch FROM session.started_at) * 1000)::bigint
                      AND (extract(epoch FROM session.ended_at) * 1000)::bigint
                  AND entry.occurrence_number = (
                      SELECT count(*)
                      FROM connectors.spotify_track_plays candidate
                      WHERE candidate.endpoint_identity = session.endpoint_identity
                        AND candidate.track_name = $3
                        AND candidate.first_seen_ms BETWEEN
                            (extract(epoch FROM session.started_at) * 1000)::bigint
                            AND (extract(epoch FROM session.ended_at) * 1000)::bigint
                        AND (candidate.first_seen_ms, candidate.track_uri)
                            <= ($2::bigint, $4::text)
                  )
                ORDER BY source_ref
                """,
                row["endpoint_identity"],
                row["first_seen_ms"],
                row["track_name"],
                row["track_uri"],
            )
            for session_source_ref in session_source_refs:
                session_idempotency_key = (
                    f"{_SESSIONS_SOURCE_TABLE}:{session_source_ref['source_ref']}:session_track"
                )
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                    session_idempotency_key,
                )

            modern_signal = await connection.fetchrow(
                "SELECT work_id FROM taste_signals WHERE idempotency_key = $1",
                idempotency_key,
            )
            if modern_signal is None:
                work_id, created = await _get_or_create_work(
                    connection,
                    kind="track",
                    title=row["track_name"],
                    external_ids={"primary": row["track_uri"]},
                )
                inserted = await _insert_signal(
                    connection,
                    work_id=work_id,
                    signal_kind=signal_kind,
                    source_table=_PLAYS_SOURCE_TABLE,
                    source_ref=source_ref,
                    occurred_at=row["recorded_at"],
                    strength=row["completion_ratio"],
                    metadata={"observation_precision": row["observation_precision"]},
                )
            else:
                work_id = modern_signal["work_id"]
                created = False
                inserted = False

            # A prior projection may have seen only the session summary and
            # created an unresolved work for this occurrence. Once the
            # URI-backed play arrives, replace that weaker evidence in the
            # same transaction as the modern signal. Repeated titles consume
            # at most one legacy occurrence per distinct play.
            legacy_rows = await connection.fetch(
                """
                SELECT signal.id, signal.work_id
                FROM taste_signals signal
                JOIN works work ON work.id = signal.work_id
                WHERE signal.source_table = $1
                  AND signal.source_ref = ANY($2::text[])
                  AND work.title IS NOT DISTINCT FROM $3
                ORDER BY signal.source_ref
                FOR UPDATE OF signal
                """,
                _SESSIONS_SOURCE_TABLE,
                [session_source_ref["source_ref"] for session_source_ref in session_source_refs],
                row["track_name"],
            )
            for legacy in legacy_rows:
                await connection.execute("DELETE FROM taste_signals WHERE id = $1", legacy["id"])
                await connection.execute(
                    "UPDATE verdicts SET work_id = $1 WHERE work_id = $2",
                    work_id,
                    legacy["work_id"],
                )
                await connection.execute(
                    """
                    DELETE FROM works work
                    WHERE work.id = $1
                      AND NOT EXISTS (
                          SELECT 1 FROM taste_signals signal WHERE signal.work_id = work.id
                      )
                    """,
                    legacy["work_id"],
                )

            if created:
                works_created += 1
            if inserted:
                signals_created += 1

    return BackfillResult(works_created=works_created, signals_created=signals_created)
