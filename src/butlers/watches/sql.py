"""PostgreSQL ``WatchStore`` over ``public.owner_watches`` (core_364; dark).

Run it on a pool whose role is ``butler_switchboard_rw``: row-level security admits no other
runtime role. The cap is counted under one advisory lock, the same one the table's
``owner_watch_cap`` trigger takes, so two racing approvals cannot both pass; the trigger is the
backstop at the contract maximum. ``write`` is a version CAS. A driver outage raises
``ConnectionError``; a lost CAS raises ``WatchConflict``; a cap refusal raises ``CapExceeded``.
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg

from butlers.watches.model import CapExceeded, Watch, WatchConflict, WatchState

_COLUMNS = (
    "id, version, generation, predicate::text AS predicate, predicate_digest, grant_digest, "
    "sources, categories, delivery, state, created_at, expires_at, approval_digest"
)
_CAP_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('butlers:owner-watch-cap', 0))"


def _watch(row: asyncpg.Record) -> Watch:
    return Watch(
        id=row["id"],
        version=row["version"],
        generation=row["generation"],
        predicate=json.loads(row["predicate"]),
        predicate_digest=row["predicate_digest"],
        grant_digest=row["grant_digest"],
        sources=tuple(row["sources"]),
        categories=tuple(row["categories"]),
        delivery=row["delivery"],
        state=WatchState(row["state"]),
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        approval_digest=row["approval_digest"],
    )


def _values(watch: Watch) -> tuple[Any, ...]:
    return (
        watch.id,
        watch.version,
        watch.generation,
        json.dumps(watch.predicate, sort_keys=True, separators=(",", ":")),
        watch.predicate_digest,
        watch.grant_digest,
        list(watch.sources),
        list(watch.categories),
        watch.delivery,
        watch.state.value,
        watch.created_at,
        watch.expires_at,
        watch.approval_digest,
    )


class PostgresWatchStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get(self, watch_id: str) -> Watch | None:
        try:
            row = await self._pool.fetchrow(
                f"SELECT {_COLUMNS} FROM public.owner_watches WHERE id = $1", watch_id
            )
        except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError) as exc:
            raise ConnectionError(f"watch store unavailable: {type(exc).__name__}") from exc
        return None if row is None else _watch(row)

    async def list_all(self) -> list[Watch]:
        try:
            rows = await self._pool.fetch(
                f"SELECT {_COLUMNS} FROM public.owner_watches ORDER BY created_at, id"
            )
        except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError) as exc:
            raise ConnectionError(f"watch store unavailable: {type(exc).__name__}") from exc
        return [_watch(row) for row in rows]

    async def write(self, watch: Watch, *, expected_version: int | None, cap: int) -> Watch:
        try:
            async with self._pool.acquire() as connection, connection.transaction():
                return await self._write(connection, watch, expected_version, cap)
        except (asyncpg.SerializationError, asyncpg.DeadlockDetectedError) as exc:
            raise WatchConflict(watch.id) from exc
        except asyncpg.CheckViolationError as exc:
            if "owner watches may be active" in str(exc):
                raise CapExceeded(f"at most {cap} watches may be active") from exc
            raise
        except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError) as exc:
            if isinstance(exc, ConnectionError):
                raise
            raise ConnectionError(f"watch store unavailable: {type(exc).__name__}") from exc

    async def _write(
        self, connection: Any, watch: Watch, expected_version: int | None, cap: int
    ) -> Watch:
        current = await connection.fetchrow(
            f"SELECT {_COLUMNS} FROM public.owner_watches WHERE id = $1 FOR UPDATE", watch.id
        )
        if expected_version is None and current is not None:
            return _watch(current)  # a replayed approval returns the committed watch
        if expected_version is not None and (
            current is None or current["version"] != expected_version
        ):
            raise WatchConflict(watch.id)
        activating = watch.state is WatchState.ACTIVE and (
            current is None or current["state"] != WatchState.ACTIVE.value
        )
        if activating:
            await connection.execute(_CAP_LOCK)
            active = await connection.fetchval(
                "SELECT count(*) FROM public.owner_watches WHERE state = 'active' AND id <> $1",
                watch.id,
            )
            if active >= cap:
                raise CapExceeded(f"at most {cap} watches may be active")
        if current is None:
            await connection.execute(
                "INSERT INTO public.owner_watches (id, version, generation, predicate, "
                "predicate_digest, grant_digest, sources, categories, delivery, state, "
                "created_at, expires_at, approval_digest) VALUES ($1, $2, $3, $4::text::jsonb, "
                "$5, $6, $7, $8, $9, $10, $11, $12, $13)",
                *_values(watch),
            )
        else:
            await connection.execute(
                "UPDATE public.owner_watches SET version = $2, generation = $3, delivery = $9, "
                "state = $10, expires_at = $12, approval_digest = $13 WHERE id = $1",
                *_values(watch),
            )
        return watch
