"""Private pre-dispatch location input capture and actual session admission.

The API's native reader generations and exact prompt bytes are frozen before
an owning Spawner receives the prompt. Its real session INSERT binds the input
on that same writer transaction; UUIDs and eventual runtime success cannot
stand in for this admission. No HTTP field selects a source or receiving pool.
"""

from __future__ import annotations

import hashlib
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

_dispatchers: dict[Any, tuple[Any, type]] = {}


def register_dispatch_runtime(spawner: Any, result_type: type) -> None:
    """Actual daemon constructor supplies its already constructed runtime.

    Kept dependency-free: native readers must not load runtime or embedding
    implementations merely to check their own stored lineage.
    """
    _dispatchers[spawner._pool] = (spawner, result_type)


def registered_dispatcher(pool: Any, spawner: Any) -> bool:
    entry = _dispatchers.get(pool)
    return entry is not None and entry[0] is spawner


def native_result(result: Any) -> bool:
    return any(isinstance(result, entry[1]) for entry in _dispatchers.values())


@dataclass
class _DispatchInput:
    generation: UUID
    prompt_digest: bytes
    session_id: UUID
    active: bool = True


_current_dispatch_input: ContextVar[_DispatchInput | None] = ContextVar(
    "native_location_dispatch_input", default=None
)


class NativeLocationDispatch:
    """Fixed in-process API dependency adapter for the actual owning Spawner.

    Standalone API deployments keep their existing unavailable-dispatch door.
    Constructor wiring supplies the actual Spawner, never a request callback.
    Ordinary non-location calls retain the original callable contract.
    """

    def __init__(self, spawner: Any) -> None:
        from butlers.chronicler.location_retention import native_copy_pool

        if (
            not registered_dispatcher(spawner._pool, spawner)
            or spawner._config.name != "chronicler"
            or not native_copy_pool(spawner._pool)
        ):
            raise ValueError("Owning dispatch constructor differs")
        self._spawner = spawner

    async def __call__(self, *, prompt: str, trigger_source: str) -> Any:
        # API audit retains the endpoint operation. Spawner uses its adopted
        # fixed internal trigger category, not a request-selected source actor.
        return await self._spawner.trigger(prompt=prompt, trigger_source="trigger")


async def dispatch_with_native_input(pool: Any, dispatch: Any, *, prompt: str, source: str) -> Any:
    from butlers.chronicler.location_export_lifetime import _current_location_export
    from butlers.chronicler.location_retention import PolicyUnavailableError
    from butlers.chronicler.storage import _lock_location_writes

    scope = _current_location_export.get()
    parents = (
        [
            (generation, digest)
            for owner, kind, generation, digest in scope.copies
            if owner is pool and kind == "native_read"
        ]
        if scope is not None
        else []
    )
    if scope is not None and not scope.active:
        raise PolicyUnavailableError("Owning input producer lifetime has ended")
    if not parents:
        return await dispatch(prompt=prompt, trigger_source=source)
    if not isinstance(dispatch, NativeLocationDispatch):
        raise PolicyUnavailableError("Native receiving dispatch is unavailable")
    generation = uuid4()
    digest = hashlib.sha256(prompt.encode("utf-8")).digest()
    async with pool.acquire() as conn:
        async with conn.transaction():
            if await conn.fetchval("SELECT current_schema()") != "chronicler":
                raise PolicyUnavailableError("Owning input producer differs")
            await _lock_location_writes(conn)
            for parent, parent_digest in parents:
                valid = await conn.fetchval(
                    "SELECT count(*)>0 AND bool_and(lineage_known AND exclusive_input) "
                    "FROM location_native_copy_births WHERE copy_generation=$1 "
                    "AND input_digest=$2 AND receiving_server_request=$3 "
                    "AND producer_kind='api_export'",
                    parent,
                    parent_digest,
                    scope.request_id,
                )
                if valid is not True:
                    raise PolicyUnavailableError("Native input lineage is unknown")
            await conn.execute(
                "INSERT INTO location_native_dispatch_inputs "
                "(input_generation,server_request,prompt_digest,parent_count) VALUES($1,$2,$3,$4)",
                generation,
                scope.request_id,
                digest,
                len(parents),
            )
            for parent, parent_digest in parents:
                await conn.execute(
                    "INSERT INTO location_native_dispatch_parents "
                    "(input_generation,copy_generation,input_digest) VALUES($1,$2,$3)",
                    generation,
                    parent,
                    parent_digest,
                )
    async with pool.acquire() as committed:
        count = await committed.fetchval(
            "SELECT count(*) FROM location_native_dispatch_parents p "
            "JOIN location_native_dispatch_inputs i USING(input_generation) "
            "WHERE input_generation=$1 AND i.server_request=$2 AND i.prompt_digest=$3 "
            "AND i.parent_count=$4",
            generation,
            scope.request_id,
            digest,
            len(parents),
        )
    if count != len(parents):
        raise PolicyUnavailableError("Committed input capture is unknown")
    # The fixed receiver reserves its actual server-selected session identity
    # and full source/body binding before Spawner composition or any prompt
    # processing. Session creation later uses exactly this identity; an absent
    # session stays a pending processing holder, never an admitted runtime.
    binding = _DispatchInput(generation, digest, uuid4())
    await reserve_dispatch_receiver(dispatch._spawner._pool, binding, scope.request_id)
    token = _current_dispatch_input.set(binding)
    try:
        return await dispatch(prompt=prompt, trigger_source=source)
    finally:
        binding.active = False
        _current_dispatch_input.reset(token)


async def bind_dispatch_session(conn: Any, session_id: UUID, prompt: str) -> None:
    """Called only by the actual session INSERT, before its writer COMMIT."""
    from butlers.chronicler.location_retention import PolicyUnavailableError
    from butlers.chronicler.storage import _lock_location_writes

    binding = _current_dispatch_input.get()
    if binding is None:
        return
    if not binding.active:
        raise PolicyUnavailableError("Native dispatch lifetime has ended")
    if session_id != binding.session_id:
        raise PolicyUnavailableError("Native receiving session differs")
    reserved = await conn.fetchval(
        "SELECT receiving_session FROM location_native_dispatch_reservations "
        "WHERE input_generation=$1 AND prompt_digest=$2",
        binding.generation,
        binding.prompt_digest,
    )
    if reserved != session_id:
        raise PolicyUnavailableError("Native receiving reservation differs")
    if await conn.fetchval("SELECT current_user") != "butler_chronicler_rw":
        raise PolicyUnavailableError("Owning input receiver differs")
    await _lock_location_writes(conn)
    digest = hashlib.sha256(prompt.encode("utf-8")).digest()
    if digest != binding.prompt_digest:
        raise PolicyUnavailableError("Native dispatch prompt differs")
    stored = await conn.fetchrow(
        "SELECT * FROM location_native_dispatch_inputs WHERE input_generation=$1 FOR UPDATE",
        binding.generation,
    )
    if stored is None or stored["prompt_digest"] != digest:
        raise PolicyUnavailableError("Native input capture differs")
    parents = await conn.fetch(
        "SELECT p.copy_generation,p.input_digest FROM location_native_dispatch_parents p "
        "WHERE p.input_generation=$1 ORDER BY p.copy_generation",
        binding.generation,
    )
    if len(parents) != stored["parent_count"]:
        raise PolicyUnavailableError("Native input cohort differs")
    for parent in parents:
        valid = await conn.fetchval(
            "SELECT count(*)>0 AND bool_and(CASE WHEN $4='native_memory' THEN true "
            "ELSE lineage_known AND exclusive_input END) "
            "FROM location_native_copy_births WHERE copy_generation=$1 AND input_digest=$2 "
            "AND receiving_server_request=$3 AND producer_kind=$4",
            parent["copy_generation"],
            parent["input_digest"],
            stored["server_request"],
            stored.get("origin_kind", "api_export"),
        )
        if valid is not True:
            raise PolicyUnavailableError("Native receiving lineage differs")
    # No update/upsert: a replay cannot assign an old input to a new session.
    await conn.execute(
        "INSERT INTO location_native_dispatch_sessions "
        "(input_generation,receiving_session,prompt_digest) VALUES($1,$2,$3)",
        binding.generation,
        session_id,
        digest,
    )

    from butlers.chronicler.location_memory_derivation import bind_composed_prompt

    await bind_composed_prompt(conn, binding.generation, session_id)


async def write_bound_dispatch_cache(
    api_pool: Any,
    dispatch: Any,
    result: Any,
    *,
    cache_key: str,
    start_at: Any,
    end_at: Any,
    prose: str,
    provenance_refs: list[Any],
) -> bool:
    """Native explain cache uses its fixed receiving writer, same transaction.

    A result/session UUID does not qualify: the stored input reservation must
    belong to this actual API producer scope and real receiving adapter.
    Ordinary existing API callbacks retain their original cache writer.
    """
    from butlers.chronicler.location_export_lifetime import _current_location_export
    from butlers.chronicler.location_retention import (
        PolicyUnavailableError,
        bind_native_cache_inputs,
        lock_native_session_completion,
        native_copy_pool,
        observe_legacy_cache,
        record_legacy_replacement,
    )
    from butlers.chronicler.storage import _lock_location_writes, upsert_tier2_cache

    scope = _current_location_export.get()
    native = scope is not None and any(
        pool is api_pool and kind == "native_read" for pool, kind, _, _ in scope.copies
    )
    if not native:
        return False
    if not isinstance(dispatch, NativeLocationDispatch) or not native_result(result):
        raise PolicyUnavailableError("Native cache receiving result differs")
    pool = dispatch._spawner._pool
    if not native_copy_pool(pool) or result.session_id is None:
        raise PolicyUnavailableError("Native cache receiver is unavailable")
    async with pool.acquire() as conn:
        async with conn.transaction():
            if await conn.fetchval("SELECT current_user") != "butler_chronicler_rw":
                raise PolicyUnavailableError("Native cache writer identity differs")
            await _lock_location_writes(conn)
            admitted = await conn.fetchval(
                "SELECT count(*) FROM location_native_dispatch_sessions s "
                "JOIN location_native_dispatch_inputs i USING(input_generation) "
                "WHERE s.receiving_session=$1 AND i.server_request=$2 "
                "AND s.prompt_digest=i.prompt_digest",
                result.session_id,
                scope.request_id,
            )
            if admitted != 1 or await lock_native_session_completion(conn, result.session_id):
                raise PolicyUnavailableError("Native cache input is unavailable")
            legacy = await observe_legacy_cache(conn, cache_key)
            await upsert_tier2_cache(
                conn,
                cache_key=cache_key,
                start_at=start_at,
                end_at=end_at,
                prose=prose,
                provenance_refs=provenance_refs,
            )
            await bind_native_cache_inputs(conn, cache_key, result)
            await record_legacy_replacement(conn, cache_key, legacy)
    async with pool.acquire() as committed:
        observed = await committed.fetchval(
            "SELECT EXISTS(SELECT 1 FROM location_native_cache_heads h "
            "JOIN location_native_cache_inputs p USING(cache_key,cache_generation) "
            "JOIN location_native_copy_births b USING(copy_generation) "
            "WHERE h.cache_key=$1 AND b.receiving_session=$2)",
            cache_key,
            result.session_id,
        )
    from butlers.chronicler.location_projection import _digest_value
    from butlers.location_retention import content_digest

    async with pool.acquire() as committed:
        current = await committed.fetchrow(
            "SELECT * FROM tier2_cache WHERE cache_key=$1", cache_key
        )
        bound = await committed.fetchval(
            "SELECT body_digest FROM location_native_cache_heads WHERE cache_key=$1", cache_key
        )
    if (
        observed is not True
        or current is None
        or bound != content_digest({"cache": _digest_value(dict(current))})
    ):
        raise PolicyUnavailableError("Committed native cache binding is unknown")
    return True


async def reserve_dispatch_receiver(
    pool: Any, binding: _DispatchInput, server_request: UUID
) -> None:
    from butlers.chronicler.location_retention import PolicyUnavailableError, native_copy_pool
    from butlers.chronicler.storage import _lock_location_writes

    if not native_copy_pool(pool):
        raise PolicyUnavailableError("Native receiving constructor differs")
    async with pool.acquire() as conn:
        async with conn.transaction():
            if await conn.fetchval("SELECT current_user") != "butler_chronicler_rw":
                raise PolicyUnavailableError("Native receiving identity differs")
            await _lock_location_writes(conn)
            source = await conn.fetchrow(
                "SELECT * FROM location_native_dispatch_inputs "
                "WHERE input_generation=$1 FOR UPDATE",
                binding.generation,
            )
            if (
                source is None
                or source["server_request"] != server_request
                or source["prompt_digest"] != binding.prompt_digest
            ):
                raise PolicyUnavailableError("Native receiving source differs")
            parents = await conn.fetch(
                "SELECT copy_generation,input_digest FROM location_native_dispatch_parents "
                "WHERE input_generation=$1 ORDER BY copy_generation",
                binding.generation,
            )
            if len(parents) != source["parent_count"]:
                raise PolicyUnavailableError("Native receiving cohort differs")
            for parent in parents:
                valid = await conn.fetchval(
                    "SELECT count(*)>0 AND bool_and(CASE WHEN $4='native_memory' THEN true "
                    "ELSE lineage_known AND exclusive_input END) "
                    "FROM location_native_copy_births WHERE copy_generation=$1 AND input_digest=$2 "
                    "AND receiving_server_request=$3 AND producer_kind=$4",
                    parent["copy_generation"],
                    parent["input_digest"],
                    server_request,
                    source.get("origin_kind", "api_export"),
                )
                if valid is not True:
                    raise PolicyUnavailableError("Native receiving lineage differs")
            await conn.execute(
                "INSERT INTO location_native_dispatch_reservations "
                "(input_generation,receiving_session,prompt_digest) VALUES($1,$2,$3)",
                binding.generation,
                binding.session_id,
                binding.prompt_digest,
            )
            await conn.execute(
                "INSERT INTO location_native_copy_births "
                "(copy_generation,output_kind,output_id,input_digest,lineage_known,receiving_session,"
                "exclusive_input,producer_kind) "
                "SELECT DISTINCT $1,b.output_kind,b.output_id,$2,b.lineage_known,$3,"
                "b.exclusive_input,'native_dispatch' "
                "FROM location_native_dispatch_parents p JOIN location_native_copy_births b "
                "ON b.copy_generation=p.copy_generation AND b.input_digest=p.input_digest "
                "WHERE p.input_generation=$1",
                binding.generation,
                binding.prompt_digest,
                binding.session_id,
            )
    async with pool.acquire() as committed:
        observed = await committed.fetchval(
            "SELECT receiving_session FROM location_native_dispatch_reservations "
            "WHERE input_generation=$1 AND prompt_digest=$2",
            binding.generation,
            binding.prompt_digest,
        )
    if observed != binding.session_id:
        raise PolicyUnavailableError("Committed receiving reservation is unknown")
