"""Private OwnTracks webhook/replay input lifetimes under its configured writer.

A fixed native constructor captures actual canonical bytes before processing.
Neither a request field nor a timeout may close this input. The actual task and
ASGI observers end only their own system-owned lifetimes, never the phone,
Switchboard receiver, another runtime or the server's remote recipient.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid4

import asyncpg

from butlers.location_retention import content_digest, logical_digest

InputKind = Literal["webhook_server", "webhook_processing", "replay_processing"]
_KIND_CODE = {"webhook_server": 1, "webhook_processing": 2, "replay_processing": 3}
_MUTEX = "owntracks:retention:source"
_logger = logging.getLogger(__name__)


def _log_input_failure(stage: str, exc: Exception) -> None:
    """Fixed producer stage/class/code only; no bodies, rows or exception args."""
    from butlers.chronicler.location_policy import closed_failure

    if stage not in {
        "server_birth",
        "input_birth",
        "processing",
        "point_write",
        "server_end",
        "input_end",
    }:
        stage = "unknown"
    category, label, state = closed_failure(exc)
    _logger.warning(
        "OwnTracks input failure stage=%s category=%s sqlstate=%s class=%s",
        stage,
        category,
        state,
        label,
    )


@dataclass(frozen=True)
class _ServerBinding:
    incarnation: UUID
    generation: UUID


@dataclass(frozen=True)
class _InputBinding:
    incarnation: UUID
    bundle: UUID
    logical_digest: bytes
    raw_digest: bytes
    inputs: tuple[tuple[UUID, InputKind], ...]


class OwnTracksInputCopies:
    """Constructor-owned producer; opaque bindings never enter HTTP/MCP wires.

    The existing connector pool and actual current role are checked for every
    transaction. Unknown commit/readback never grants processing or creates a
    replacement generation. Missing terminal history remains an active holder,
    including across process restart; elapsed time is not a disposal witness.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        if not isinstance(pool, asyncpg.Pool):
            raise ValueError("native input pool differs")
        self.pool = pool
        self.incarnation = uuid4()
        self._bindings: dict[int, _InputBinding] = {}
        self._ended: set[tuple[int, InputKind]] = set()
        self._acked: set[tuple[int, InputKind]] = set()
        self._servers: dict[int, _ServerBinding] = {}
        self._server_admitted: set[int] = set()
        self._server_ended: set[int] = set()
        self._server_inputs: dict[int, _InputBinding] = {}
        self._processing_started: set[int] = set()
        self._processing_reserved: set[int] = set()
        self._reconcile_lock = asyncio.Lock()
        self._closing = False

    def close_admission(self) -> None:
        """Fixed source shutdown stops NEW body/read reservations only.

        This does not end any captured lifetime or certify an old incarnation.
        Existing original bindings must still earn their actual end/readback.
        """
        self._closing = True

    def allocate_server(self) -> _ServerBinding:
        """Fixed ASGI constructor allocates before any body receive/parsing."""
        if self._closing or len(self._servers) >= 1024 or len(self._bindings) >= 1024:
            raise ValueError("native input capacity is unavailable")
        binding = _ServerBinding(self.incarnation, uuid4())
        self._servers[id(binding)] = binding
        return binding

    async def commit_server(self, binding: _ServerBinding) -> None:
        if self._servers.get(id(binding)) is not binding:
            raise ValueError("native server binding differs")
        async with asyncio.timeout(5):
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    await _lock_writer(conn)
                    await conn.execute(
                        "INSERT INTO connectors.owntracks_input_server_births "
                        "(copy_generation,incarnation) VALUES($1,$2) "
                        "ON CONFLICT(copy_generation) DO NOTHING",
                        binding.generation,
                        self.incarnation,
                    )
            async with self.pool.acquire() as observed:
                actual = await observed.fetchval(
                    "SELECT incarnation FROM connectors.owntracks_input_server_births "
                    "WHERE copy_generation=$1",
                    binding.generation,
                )
                if actual != self.incarnation:
                    raise ValueError("native server committed birth is unknown")
        self._server_admitted.add(id(binding))

    def observed_server_end(self, binding: _ServerBinding) -> None:
        if self._servers.get(id(binding)) is not binding:
            raise ValueError("native server lifetime differs")
        self._server_ended.add(id(binding))

    async def finish_server(self, binding: _ServerBinding) -> None:
        """Close only an actually ended native outer server task, same identity."""
        if self._servers.get(id(binding)) is not binding or id(binding) not in self._server_ended:
            raise ValueError("native server lifetime is still active")
        child = self._server_inputs.get(id(binding))
        if child is not None:
            # A failed birth/readback never hands the binding to processing.
            # Settle the SAME original UUID set under the source mutex; absence
            # is acceptable only for this private never-issued allocation.
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    await _lock_writer(conn)
                    born = await conn.fetch(
                        "SELECT copy_generation,incarnation,copy_bundle,bundle_count,"
                        "logical_source_digest,raw_digest,copy_kind,producer_contract,"
                        "server_generation FROM connectors.owntracks_input_copy_births "
                        "WHERE copy_generation=ANY($1::uuid[]) ORDER BY copy_generation",
                        [generation for generation, _ in child.inputs],
                    )
                    expected = {
                        generation: (
                            generation,
                            self.incarnation,
                            child.bundle,
                            len(child.inputs),
                            child.logical_digest,
                            child.raw_digest,
                            _KIND_CODE[kind],
                            1,
                            binding.generation,
                        )
                        for generation, kind in child.inputs
                    }
                    if (
                        not born
                        and id(child) not in self._processing_started
                        and id(child) not in self._processing_reserved
                    ):
                        self._bindings.pop(id(child), None)
                        self._server_inputs.pop(id(binding), None)
                        child = None
                    elif len(born) != len(expected) or any(
                        tuple(row.values()) != expected.get(row["copy_generation"]) for row in born
                    ):
                        raise ValueError("native input settled birth cohort differs")
            if child is not None:
                issued = (
                    id(child) in self._processing_started or id(child) in self._processing_reserved
                )
                self.observed_end(child, "webhook_server")
                await self.finish(child, "webhook_server")
                if not issued:
                    # Fixed constructor knows no processing task was ever
                    # issued; the actual outer task has now released its input.
                    self.observed_end(child, "webhook_processing")
                    await self.finish(child, "webhook_processing")
        async with asyncio.timeout(5):
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    await _lock_writer(conn)
                    actual = await conn.fetchval(
                        "SELECT incarnation FROM connectors.owntracks_input_server_births "
                        "WHERE copy_generation=$1 FOR UPDATE",
                        binding.generation,
                    )
                    if actual is None and id(binding) not in self._server_admitted:
                        # Exact original allocation never admitted body receive;
                        # settled source mutex excludes a still-committing birth.
                        self._discard_server(binding)
                        return
                    if actual != self.incarnation:
                        raise ValueError("native server original birth differs")
                    await conn.execute(
                        "INSERT INTO connectors.owntracks_input_server_ends(copy_generation) "
                        "VALUES($1) ON CONFLICT(copy_generation) DO NOTHING",
                        binding.generation,
                    )
            async with self.pool.acquire() as observed:
                if not await observed.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM connectors.owntracks_input_server_ends "
                    "WHERE copy_generation=$1)",
                    binding.generation,
                ):
                    raise ValueError("native server committed end is unknown")
        self._discard_server(binding)

    def _discard_server(self, binding: _ServerBinding) -> None:
        self._servers.pop(id(binding), None)
        self._server_admitted.discard(id(binding))
        self._server_ended.discard(id(binding))
        child = self._server_inputs.pop(id(binding), None)
        if child is not None:
            self._discard_closed_input(child)

    def _discard_closed_input(self, binding: _InputBinding) -> None:
        if not all((id(binding), kind) in self._acked for _, kind in binding.inputs):
            return
        # Keep the original child until the header's separate committed end is
        # observed. Losing that capability on a failed header readback would
        # make the same original allocation impossible to reconcile.
        if any(server.generation == binding.bundle for server in self._servers.values()):
            return
        self._bindings.pop(id(binding), None)
        self._processing_started.discard(id(binding))
        self._processing_reserved.discard(id(binding))
        for _, kind in binding.inputs:
            self._ended.discard((id(binding), kind))
            self._acked.discard((id(binding), kind))

    async def reconcile_observed_ends(self) -> None:
        """Retry only this producer's actually observed ended lifetimes.

        No stored age, missing process or caller status grants an end. Original
        capabilities survive unknown acknowledgements and are retried unchanged.
        A restart cannot inherit this process-local lifetime proof.
        """
        async with self._reconcile_lock:
            for server in tuple(self._servers.values()):
                if id(server) in self._server_ended:
                    await self.finish_server(server)
            for binding in tuple(self._bindings.values()):
                for _, kind in binding.inputs:
                    if (id(binding), kind) in self._ended:
                        await self.finish(binding, kind)
                        if id(binding) not in self._bindings:
                            break

    async def reserve(
        self,
        endpoint: str,
        raw: dict[str, Any],
        *,
        replay: bool = False,
        server: _ServerBinding | None = None,
    ) -> _InputBinding | None:
        from butlers.connectors.owntracks import extract_tst

        if raw.get("_type") != "location":
            return None  # Independently classified native protocol types.
        stamp = extract_tst(raw)
        if stamp is None or not endpoint.startswith("owntracks:"):
            raise ValueError("native input source differs")
        kinds: tuple[InputKind, ...] = (
            ("replay_processing",) if replay else ("webhook_server", "webhook_processing")
        )
        if not replay and (
            server is None
            or self._servers.get(id(server)) is not server
            or id(server) not in self._server_admitted
        ):
            raise ValueError("native input server birth is unavailable")
        if server is not None and id(server) in self._server_inputs:
            # A failed/unknown first publication remains the SAME allocation.
            # A second reserve cannot overwrite its removal-only capability.
            raise ValueError("native input server bundle was already captured")
        server_generation = None if replay else server.generation
        binding = _InputBinding(
            self.incarnation,
            uuid4() if replay else server_generation,
            logical_digest(f"owntracks:{endpoint}:{stamp}:location"),
            content_digest(raw),
            tuple(
                (server_generation if kind == "webhook_server" else uuid4(), kind) for kind in kinds
            ),
        )
        # These private metadata capabilities exist for removal-only recovery
        # even if publication/independent readback is unknown. They are never
        # returned to the dispatcher until the entire birth readback succeeds.
        self._bindings[id(binding)] = binding
        if server is not None:
            self._server_inputs[id(server)] = binding
        async with asyncio.timeout(5):
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    await _lock_writer(conn)
                    if await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM connectors.owntracks_filtered_copy_floors "
                        "WHERE logical_source_digest=$1) OR EXISTS(SELECT 1 FROM "
                        "connectors.owntracks_retention_tombstones WHERE logical_source_digest=$1)",
                        binding.logical_digest,
                    ):
                        raise ValueError("native input source is closed")
                    for generation, kind in binding.inputs:
                        await conn.execute(
                            "INSERT INTO connectors.owntracks_input_copy_births "
                            "(copy_generation,incarnation,copy_bundle,bundle_count,"
                            "logical_source_digest,raw_digest,copy_kind,producer_contract,"
                            "server_generation) "
                            "VALUES($1,$2,$3,$4,$5,$6,$7,1,$8)",
                            generation,
                            binding.incarnation,
                            binding.bundle,
                            len(binding.inputs),
                            binding.logical_digest,
                            binding.raw_digest,
                            _KIND_CODE[kind],
                            server_generation,
                        )
            # Actual independent committed readback precedes prompt/ingest bytes.
            async with self.pool.acquire() as observed:
                for generation, kind in binding.inputs:
                    row = await observed.fetchrow(
                        "SELECT incarnation,copy_bundle,bundle_count,logical_source_digest,"
                        "raw_digest,copy_kind,producer_contract,server_generation "
                        "FROM "
                        "connectors.owntracks_input_copy_births WHERE copy_generation=$1",
                        generation,
                    )
                    if row is None or tuple(row.values()) != (
                        binding.incarnation,
                        binding.bundle,
                        len(binding.inputs),
                        binding.logical_digest,
                        binding.raw_digest,
                        _KIND_CODE[kind],
                        1,
                        server_generation,
                    ):
                        raise ValueError("native input committed birth is unknown")
        self._bindings[id(binding)] = binding
        if server is not None:
            self._server_inputs[id(server)] = binding
        return binding

    def require_body(self, binding: _InputBinding, endpoint: str, raw: dict[str, Any]) -> None:
        from butlers.connectors.owntracks import extract_tst

        if (
            self._bindings.get(id(binding)) is not binding
            or binding.incarnation != self.incarnation
            or (stamp := extract_tst(raw)) is None
            or logical_digest(f"owntracks:{endpoint}:{stamp}:location") != binding.logical_digest
            or content_digest(raw) != binding.raw_digest
        ):
            raise ValueError("native input body changed")

    def reserve_processing(self, binding: _InputBinding) -> None:
        """Fence cross-loop task submission before its future can run.

        This is an in-process hold, not a successful task-issuance witness. The
        outer server end cannot infer never-issued processing during submission.
        """
        if self._bindings.get(id(binding)) is not binding:
            raise ValueError("native input processing reservation differs")
        self._processing_reserved.add(id(binding))

    def processing_not_issued(self, binding: _InputBinding) -> None:
        """Fixed dispatcher settled submission failure and closed its coroutine."""
        if self._bindings.get(id(binding)) is not binding:
            raise ValueError("native input processing reservation differs")
        self._processing_reserved.discard(id(binding))

    def processing_started(self, binding: _InputBinding) -> None:
        """Fixed dispatcher records actual successful task issuance only."""
        if self._bindings.get(id(binding)) is not binding:
            raise ValueError("native input issued binding differs")
        self._processing_started.add(id(binding))
        self._processing_reserved.discard(id(binding))

    def processing_generation(self, binding: _InputBinding) -> UUID:
        """Select only this producer's actual private processing birth."""
        if self._bindings.get(id(binding)) is not binding:
            raise ValueError("native input processing binding differs")
        return next(
            generation for generation, kind in binding.inputs if kind.endswith("processing")
        )

    def observed_end(self, binding: _InputBinding, kind: InputKind) -> None:
        """Only actual constructor-fixed task/ASGI observers call this method."""
        if self._bindings.get(id(binding)) is not binding or kind not in {
            captured for _, captured in binding.inputs
        }:
            raise ValueError("native input lifetime differs")
        self._ended.add((id(binding), kind))

    async def finish(self, binding: _InputBinding, kind: InputKind) -> None:
        """Retry only an already observed native end, preserving its exact birth."""
        if self._bindings.get(id(binding)) is not binding or (id(binding), kind) not in self._ended:
            raise ValueError("native input lifetime is still active")
        generation = next(generation for generation, captured in binding.inputs if captured == kind)
        async with asyncio.timeout(5):
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    await _lock_writer(conn)
                    birth = await conn.fetchrow(
                        "SELECT incarnation,copy_bundle,bundle_count,logical_source_digest,"
                        "raw_digest,copy_kind,producer_contract,server_generation "
                        "FROM "
                        "connectors.owntracks_input_copy_births "
                        "WHERE copy_generation=$1 FOR UPDATE",
                        generation,
                    )
                    if birth is None or tuple(birth.values()) != (
                        self.incarnation,
                        binding.bundle,
                        len(binding.inputs),
                        binding.logical_digest,
                        binding.raw_digest,
                        _KIND_CODE[kind],
                        1,
                        binding.bundle if kind != "replay_processing" else None,
                    ):
                        raise ValueError("native input original birth differs")
                    await conn.execute(
                        "INSERT INTO connectors.owntracks_input_copy_ends"
                        "(copy_generation,raw_digest) "
                        "VALUES($1,$2) ON CONFLICT(copy_generation) DO NOTHING",
                        generation,
                        binding.raw_digest,
                    )
            async with self.pool.acquire() as observed:
                digest = await observed.fetchval(
                    "SELECT raw_digest FROM connectors.owntracks_input_copy_ends "
                    "WHERE copy_generation=$1",
                    generation,
                )
                if digest != binding.raw_digest:
                    raise ValueError("native input committed end is unknown")
        self._acked.add((id(binding), kind))
        self._discard_closed_input(binding)


async def _lock_writer(conn: Any) -> None:
    if await conn.fetchval("SELECT current_user") != "connector_writer":
        raise ValueError("native input writer differs")
    await conn.execute("SET LOCAL lock_timeout='2s'")
    await conn.execute("SET LOCAL statement_timeout='5s'")
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", _MUTEX)


async def require_inputs_ended(
    conn: Any, generation: UUID | None, logical: bytes, raw_digest: bytes
) -> None:
    """Check every original input bundle under the owning source mutex.

    The generation comes from the immutable point birth, never the plan or an
    HTTP/MCP field. Zero/legacy/partial ancestry cannot become an empty closed
    cohort. Every same-source bundle must retain its complete original kind set
    and independently committed native end before preparation/raw deletion.
    """
    if generation is None:
        raise ValueError("native input original source birth is unavailable")
    if await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM connectors.owntracks_input_server_births h "
        "LEFT JOIN connectors.owntracks_input_server_ends e USING(copy_generation) "
        "WHERE e.copy_generation IS NULL AND (NOT EXISTS(SELECT 1 FROM "
        "connectors.owntracks_input_copy_births b WHERE b.server_generation=h.copy_generation) "
        "OR EXISTS(SELECT 1 FROM connectors.owntracks_input_copy_births b "
        "WHERE b.server_generation=h.copy_generation AND b.logical_source_digest=$1)))",
        logical,
    ):
        raise ValueError("native input server cohort is still active")

    rows = await conn.fetch(
        "SELECT b.*, e.raw_digest AS ended_digest FROM "
        "connectors.owntracks_input_copy_births b LEFT JOIN "
        "connectors.owntracks_input_copy_ends e USING(copy_generation) "
        "WHERE b.logical_source_digest=$1 ORDER BY b.copy_bundle,b.copy_generation",
        logical,
    )
    selected = next((r for r in rows if r["copy_generation"] == generation), None)
    if selected is None or selected["copy_kind"] not in {2, 3}:
        raise ValueError("native input original source birth differs")
    bundles: dict[UUID, list[Any]] = {}
    for row in rows:
        if (
            row["producer_contract"] != 1
            or row["logical_source_digest"] != logical
            or row["raw_digest"] != raw_digest
            or row["ended_digest"] != raw_digest
            or (row["copy_kind"] in {1, 2} and row["server_generation"] != row["copy_bundle"])
            or (row["copy_kind"] == 3 and row["server_generation"] is not None)
        ):
            raise ValueError("native input source cohort is still active")
        bundles.setdefault(row["copy_bundle"], []).append(row)
    for bundle in bundles.values():
        original = bundle[0]
        kinds = [r["copy_kind"] for r in bundle]
        count = original["bundle_count"]
        if (
            len(bundle) != count
            or len(set(kinds)) != count
            or set(kinds) != ({1, 2} if count == 2 else {3})
            or any(
                r["bundle_count"] != count or r["incarnation"] != original["incarnation"]
                for r in bundle
            )
        ):
            raise ValueError("native input original bundle is incomplete")


class OwnTracksInputMiddleware:
    """Observe the actual native server scope after its inner application ends.

    The fixed connector installs this wrapper. The route stores only its
    privately minted binding in that actual scope after authentication; no
    header, client ACK or remote-recipient claim participates.
    """

    def __init__(self, app: Any, *, connector: Any) -> None:
        self.app = app
        self.connector = connector

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("path") != "/owntracks/webhook":
            await self.app(scope, receive, send)
            return
        if not self.connector._native_server_authorized(scope):
            await self.app(scope, receive, send)
            return
        try:
            server = self.connector._allocate_native_server()
        except ValueError:
            # Fixed native capacity/shutdown refusal BEFORE receiving a body;
            # no raw input exists and no caller state is used as a disposition.
            await send(
                {
                    "type": "http.response.start",
                    "status": 503,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send(
                {"type": "http.response.body", "body": b'{"error":"Native input unavailable"}'}
            )
            return
        task = asyncio.current_task()
        if server is None or task is None:
            if server is None:
                await self.app(scope, receive, send)  # Explicit unconfigured legacy mode only.
                return
            raise ValueError("native server task is unavailable")

        def actual_server_ended(completed: asyncio.Task) -> None:
            if not completed.cancelled() and completed.exception() is None:
                self.connector._observe_native_server_end(server)

        task.add_done_callback(actual_server_ended)
        try:
            await self.connector._commit_native_server(server)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log_input_failure("server_birth", exc)
            # No body receive/parse was ever admitted. Fixed refusal contains
            # no raw argument/exception and leaves actual Task-end recovery.
            await send(
                {
                    "type": "http.response.start",
                    "status": 503,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send(
                {"type": "http.response.body", "body": b'{"error":"Native input unavailable"}'}
            )
            return
        slot: list[Any] = [server]
        scope["_owntracks_native_input"] = slot
        try:
            await self.app(scope, receive, send)
        finally:
            scope.pop("_owntracks_native_input", None)
