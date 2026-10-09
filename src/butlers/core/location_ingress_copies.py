"""Switchboard-owned receiving input lifetimes, separate from connector ends.

Only the fixed owning daemon registers this writer. A native ASGI scope and
the actual SDK handler Task reserve their own original generations. Request
headers, accepted UUIDs and a remote caller's end cannot settle these copies.
Interrupted/unbound history remains unavailable until its own proof closes.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from butlers.core.location_copy_retention import CopyFloorUnavailable
from butlers.location_retention import content_digest, logical_digest

logger = logging.getLogger(__name__)

_MUTEX = "location:switchboard-ingress"
_writers: dict[asyncpg.Pool, SwitchboardInputCopies] = {}
_HTTP_CELL = "butlers.location.ingress_input"


@dataclass(frozen=True)
class _Header:
    generation: UUID
    task: asyncio.Task


@dataclass(eq=False)
class _Input:
    generation: UUID
    server: UUID
    dedupe_digest: bytes
    envelope_digest: bytes
    task: asyncio.Task | None
    handler: UUID | None = None
    kind: int = 1


@dataclass(eq=False)
class _RequestInput:
    runtime: SwitchboardInputCopies
    header: _Header
    queued: _Input | None = None


def _ingest_envelope(arguments: Any) -> dict | None:
    if not isinstance(arguments, dict):
        return None
    required = ("schema_version", "source", "event", "sender", "payload")
    if not all(key in arguments for key in required):
        return None
    envelope = {key: arguments[key] for key in required}
    if arguments.get("control") is not None:
        envelope["control"] = arguments["control"]
    return envelope


def _owntracks(envelope: dict) -> bool:
    source = envelope.get("source")
    return isinstance(source, dict) and source.get("provider") == "owntracks"


def _queued_envelope(body: bytes) -> dict | None:
    # Classification has a bounded private buffer; generic SDK bytes are
    # forwarded unchanged. An unclassifiable native call cannot mint a later
    # birth from a caller's label or after the HTTP lifetime has ended.
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate")
            result[key] = value
        return result

    try:
        message = json.loads(body, object_pairs_hook=unique)
    except (ValueError, UnicodeError, RecursionError):
        return None
    if not isinstance(message, dict) or message.get("method") != "tools/call":
        return None
    params = message.get("params")
    if not isinstance(params, dict) or params.get("name") != "ingest":
        return None
    return _ingest_envelope(params.get("arguments"))


class SwitchboardInputCopies:
    """Real configured Pool/role, private immutable identities, no caller mint."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        if not isinstance(pool, asyncpg.Pool):
            raise CopyFloorUnavailable("ingress_constructor_unavailable")
        if pool in _writers:
            raise CopyFloorUnavailable("ingress_constructor_differs")
        self.pool, self.incarnation = pool, uuid4()
        self.active = True
        self._headers: dict[int, _Header] = {}
        self._inputs: dict[int, _Input] = {}
        self._ended_headers: set[int] = set()
        self._ended_inputs: set[int] = set()
        self._cleared_inputs: set[int] = set()
        self._settlers: set[asyncio.Task] = set()
        self._reconcile_lock = asyncio.Lock()
        _writers[pool] = self

    @asynccontextmanager
    async def writer(self):
        async with asyncio.timeout(5):
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    actual = await conn.fetchrow("SELECT current_schema(),current_user")
                    if tuple(actual.values()) != (
                        "switchboard",
                        "butler_switchboard_rw",
                    ):
                        raise CopyFloorUnavailable("ingress_owning_identity_differs")
                    await conn.execute("SET LOCAL lock_timeout='2s'")
                    await conn.execute("SET LOCAL statement_timeout='5s'")
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", _MUTEX
                    )
                    yield conn

    def _observe(self, binding: _Header | _Input, *, header: bool) -> None:
        # The callback is registered on this actual source-owned Task. Future
        # completion, cancellation requests and client ACKs never invoke it.
        def ended(task: asyncio.Task) -> None:
            if task is not binding.task or not task.done():
                return
            target = self._ended_headers if header else self._ended_inputs
            target.add(id(binding))
            settling = asyncio.create_task(self.reconcile_observed_ends())
            self._settlers.add(settling)
            settling.add_done_callback(self._settled)

        if binding.task is None:
            raise CopyFloorUnavailable("ingress_handler_not_claimed")
        binding.task.add_done_callback(ended)

    def _settled(self, task: asyncio.Task) -> None:
        self._settlers.discard(task)
        if not task.cancelled():
            task.exception()  # Drain only; failed readback retains original binding.

    async def reserve_header(self) -> _Header:
        task = asyncio.current_task()
        if not self.active or task is None or len(self._headers) >= 1024:
            raise CopyFloorUnavailable("ingress_server_unavailable")
        binding = _Header(uuid4(), task)
        self._headers[id(binding)] = binding
        self._observe(binding, header=True)
        async with self.writer() as conn:
            await conn.execute(
                "INSERT INTO location_ingress_server_births(server_generation,incarnation) "
                "VALUES($1,$2)",
                binding.generation,
                self.incarnation,
            )
        async with self.pool.acquire() as observed:
            if (
                await observed.fetchval(
                    "SELECT incarnation FROM location_ingress_server_births "
                    "WHERE server_generation=$1",
                    binding.generation,
                )
                != self.incarnation
            ):
                raise CopyFloorUnavailable("ingress_server_commit_unknown")
        return binding

    async def reserve_queued_input(self, header: _Header, envelope: dict) -> _Input:
        from butlers.tools.switchboard.ingestion.ingest import _compute_dedupe_key
        from butlers.tools.switchboard.routing.contracts import parse_ingest_envelope

        task = asyncio.current_task()
        if (
            not self.active
            or task is None
            or self._headers.get(id(header)) is not header
            or header.task.done()
            or len(self._inputs) >= 1024
        ):
            raise CopyFloorUnavailable("ingress_server_binding_differs")
        parsed = parse_ingest_envelope(envelope)
        if parsed.source.provider != "owntracks":
            raise CopyFloorUnavailable("ingress_source_differs")
        key = _compute_dedupe_key(parsed)
        binding = _Input(
            uuid4(), header.generation, logical_digest(key), content_digest(envelope), None
        )
        self._inputs[id(binding)] = binding
        # No Task end exists while this immutable input is queued. HTTP 202
        # and POST completion do not close its pending SDK copy.
        async with self.writer() as conn:
            if await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM location_retention_source_floors "
                "WHERE dedupe_digest=$1)",
                binding.dedupe_digest,
            ):
                raise CopyFloorUnavailable("ingress_source_is_forgotten")
            await conn.execute(
                "INSERT INTO location_ingress_input_births(copy_generation,server_generation,"
                "dedupe_digest,envelope_digest,copy_kind) VALUES($1,$2,$3,$4,1)",
                binding.generation,
                binding.server,
                binding.dedupe_digest,
                binding.envelope_digest,
            )
        async with self.pool.acquire() as observed:
            row = await observed.fetchrow(
                "SELECT server_generation,dedupe_digest,envelope_digest,copy_kind "
                "FROM location_ingress_input_births WHERE copy_generation=$1",
                binding.generation,
            )
        if row is None or tuple(row.values()) != (
            binding.server,
            binding.dedupe_digest,
            binding.envelope_digest,
            binding.kind,
        ):
            raise CopyFloorUnavailable("ingress_input_commit_unknown")
        return binding

    async def claim_queued_input(self, cell: _RequestInput, envelope: dict) -> _Input:
        binding, task = cell.queued, asyncio.current_task()
        if (
            cell.runtime is not self
            or binding is None
            or self._inputs.get(id(binding)) is not binding
            or binding.server != cell.header.generation
            or binding.envelope_digest != content_digest(envelope)
            or task is None
            or (binding.task is not None and binding.task is not task)
        ):
            raise CopyFloorUnavailable("ingress_queued_input_differs")
        if binding.task is None:
            binding.task, binding.handler = task, uuid4()
            if binding.kind != 2:
                self._observe(binding, header=False)
            async with self.writer() as conn:
                await conn.execute(
                    "INSERT INTO location_ingress_input_claims "
                    "(copy_generation,handler_generation,incarnation) VALUES($1,$2,$3)",
                    binding.generation,
                    binding.handler,
                    self.incarnation,
                )
            async with self.pool.acquire() as observed:
                actual = await observed.fetchrow(
                    "SELECT handler_generation,incarnation FROM location_ingress_input_claims "
                    "WHERE copy_generation=$1",
                    binding.generation,
                )
            if actual is None or tuple(actual.values()) != (binding.handler, self.incarnation):
                raise CopyFloorUnavailable("ingress_handler_commit_unknown")
        return binding

    async def reserve_child(self, parent: _Input, body: dict, *, kind: int = 3) -> _Input:
        """Fixed owning producer forks its actual copied input before publication."""
        if (
            not self.active
            or self._inputs.get(id(parent)) is not parent
            or parent.task is not asyncio.current_task()
            or parent.kind not in (1, 2)
            or kind not in (2, 3)
            or (parent.kind == 2 and kind != 3)
            or len(self._inputs) >= 1024
        ):
            raise CopyFloorUnavailable("ingress_child_parent_differs")
        child = _Input(
            uuid4(), parent.server, parent.dedupe_digest, content_digest(body), None, kind=kind
        )
        self._inputs[id(child)] = child
        async with self.writer() as conn:
            accepted = await conn.fetchrow(
                "SELECT request_id,stored_digest FROM location_ingress_accepted_inputs "
                "WHERE copy_generation=$1",
                parent.generation,
            )
            if accepted is None or await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM location_retention_source_floors "
                "WHERE dedupe_digest=$1)",
                parent.dedupe_digest,
            ):
                raise CopyFloorUnavailable("ingress_child_source_unavailable")
            await conn.execute(
                "INSERT INTO location_ingress_input_births(copy_generation,server_generation,"
                "dedupe_digest,envelope_digest,copy_kind) VALUES($1,$2,$3,$4,$5)",
                child.generation,
                child.server,
                child.dedupe_digest,
                child.envelope_digest,
                child.kind,
            )
            await conn.execute(
                "INSERT INTO location_ingress_input_parents(copy_generation,parent_generation) "
                "VALUES($1,$2)",
                child.generation,
                parent.generation,
            )
            await conn.execute(
                "INSERT INTO location_ingress_accepted_inputs "
                "(copy_generation,request_id,stored_digest) "
                "VALUES($1,$2,$3)",
                child.generation,
                accepted["request_id"],
                accepted["stored_digest"],
            )
        async with self.pool.acquire() as observed:
            actual = await observed.fetchrow(
                "SELECT b.server_generation,b.dedupe_digest,b.envelope_digest,p.parent_generation,"
                "a.request_id,a.stored_digest FROM location_ingress_input_births b "
                "JOIN location_ingress_input_parents p USING(copy_generation) "
                "JOIN location_ingress_accepted_inputs a USING(copy_generation) "
                "WHERE b.copy_generation=$1",
                child.generation,
            )
        if actual is None or tuple(actual.values()) != (
            child.server,
            child.dedupe_digest,
            child.envelope_digest,
            parent.generation,
            accepted["request_id"],
            accepted["stored_digest"],
        ):
            raise CopyFloorUnavailable("ingress_child_commit_unknown")
        return child

    async def bind_accepted(self, binding: _Input, request_id: UUID) -> None:
        if (
            self._inputs.get(id(binding)) is not binding
            or binding.task is not asyncio.current_task()
            or binding.task.done()
        ):
            raise CopyFloorUnavailable("ingress_input_binding_differs")
        async with self.writer() as conn:
            row = await conn.fetchrow(
                "SELECT raw_payload,normalized_text,request_context FROM message_inbox "
                "WHERE id=$1 FOR SHARE",
                request_id,
            )
            key = (row["request_context"] or {}).get("dedupe_key") if row else None
            if not isinstance(key, str) or not key or logical_digest(key) != binding.dedupe_digest:
                raise CopyFloorUnavailable("ingress_accepted_source_differs")
            body = content_digest(
                {"raw_payload": row["raw_payload"], "normalized_text": row["normalized_text"]}
            )
            await conn.execute(
                "INSERT INTO location_ingress_accepted_inputs "
                "(copy_generation,request_id,stored_digest) VALUES($1,$2,$3)",
                binding.generation,
                request_id,
                body,
            )
        async with self.pool.acquire() as observed:
            actual = await observed.fetchrow(
                "SELECT request_id,stored_digest FROM location_ingress_accepted_inputs "
                "WHERE copy_generation=$1",
                binding.generation,
            )
        if actual is None or tuple(actual.values()) != (request_id, body):
            raise CopyFloorUnavailable("ingress_accepted_commit_unknown")

    async def reconcile_observed_ends(self) -> None:
        async with self._reconcile_lock:
            for table, key, bindings, ended in (
                (
                    "location_ingress_input_ends",
                    "copy_generation",
                    self._inputs,
                    self._ended_inputs,
                ),
                (
                    "location_ingress_server_ends",
                    "server_generation",
                    self._headers,
                    self._ended_headers,
                ),
            ):
                for identity in tuple(ended):
                    binding = bindings.get(identity)
                    if (
                        binding is None
                        or binding.task is None
                        or (
                            isinstance(binding, _Input)
                            and binding.kind == 2
                            and identity not in self._cleared_inputs
                        )
                        or (
                            not (isinstance(binding, _Input) and binding.kind == 2)
                            and not binding.task.done()
                        )
                    ):
                        raise CopyFloorUnavailable("ingress_observed_lifetime_differs")
                    async with self.writer() as conn:
                        birth_table = table.replace("_ends", "_births")
                        columns = (
                            "incarnation"
                            if isinstance(binding, _Header)
                            else "server_generation,dedupe_digest,envelope_digest,copy_kind"
                        )
                        born = await conn.fetchrow(
                            f"SELECT {columns} FROM {birth_table} WHERE {key}=$1",
                            binding.generation,
                        )
                        expected = (
                            (self.incarnation,)
                            if isinstance(binding, _Header)
                            else (
                                binding.server,
                                binding.dedupe_digest,
                                binding.envelope_digest,
                                binding.kind,
                            )
                        )
                        if born is not None and tuple(born.values()) != expected:
                            raise CopyFloorUnavailable("ingress_original_birth_differs")
                        if born and isinstance(binding, _Input):
                            claim = await conn.fetchrow(
                                "SELECT handler_generation,incarnation "
                                "FROM location_ingress_input_claims WHERE copy_generation=$1",
                                binding.generation,
                            )
                            if claim is None or tuple(claim.values()) != (
                                binding.handler,
                                self.incarnation,
                            ):
                                raise CopyFloorUnavailable("ingress_original_claim_unknown")
                        if born:
                            await conn.execute(
                                f"INSERT INTO {table}({key}) VALUES($1) ON CONFLICT DO NOTHING",
                                binding.generation,
                            )
                    async with self.pool.acquire() as observed:
                        terminal = not born or await observed.fetchval(
                            f"SELECT EXISTS(SELECT 1 FROM {table} WHERE {key}=$1)",
                            binding.generation,
                        )
                    if not terminal:
                        raise CopyFloorUnavailable("ingress_end_commit_unknown")
                    bindings.pop(identity)
                    ended.discard(identity)
                    self._cleared_inputs.discard(identity)

    async def stop(self) -> None:
        """No new input; retry only real observed ends before pool shutdown."""
        self.active = False
        try:
            await self.reconcile_observed_ends()
        finally:
            if _writers.get(self.pool) is self:
                _writers.pop(self.pool)


class IngressServerLifetime:
    """Reserve a fixed owning POST lifetime BEFORE giving SDK any body bytes."""

    def __init__(self, app: Any, runtime: SwitchboardInputCopies):
        self.app, self.runtime = app, runtime

    def __getattr__(self, name: str) -> Any:
        return getattr(self.app, name)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return
        if not (scope.get("path") == "/mcp" or scope.get("path", "").startswith("/messages/")):
            await self.app(scope, receive, send)
            return
        try:
            header = await self.runtime.reserve_header()
        except Exception:
            from starlette.responses import JSONResponse

            await JSONResponse({"error": "ingress_source_unavailable"}, status_code=503)(
                scope, receive, send
            )
            return
        # Only this real constructor can insert this private object into the
        # actual SDK Request.scope. It is not an HTTP header or MCP argument.
        cell = _RequestInput(self.runtime, header)
        captured = bytearray()
        overflow = False
        completed = False

        async def native_receive():
            nonlocal overflow, completed
            message = await receive()
            if message.get("type") == "http.request" and not completed:
                chunk = message.get("body", b"")
                if not overflow and len(captured) + len(chunk) <= 2 * 1024 * 1024:
                    captured.extend(chunk)
                else:
                    captured.clear()
                    overflow = True
                if not message.get("more_body", False):
                    completed = True
                    envelope = None if overflow else _queued_envelope(bytes(captured))
                    captured.clear()
                    if envelope and _owntracks(envelope):
                        # Commit and independently read back before releasing
                        # the final body frame to the SDK's message queue.
                        cell.queued = await self.runtime.reserve_queued_input(header, envelope)
            return message

        await self.app({**scope, _HTTP_CELL: cell}, native_receive, send)


async def reserve_ingest_input(pool: Any, envelope: dict) -> tuple | None:
    """Actual owning tool call binds this request, never initialization ContextVars."""
    runtime = _writers.get(pool)
    if runtime is None:
        return None  # Explicit unconfigured adapter supplies no native proof.
    if not _owntracks(envelope):
        return None  # Ordinary non-location ingest retains its existing behavior.
    cell = _current_request_input(runtime)
    captured = await runtime.claim_queued_input(cell, envelope)
    if captured.task is not asyncio.current_task():
        raise CopyFloorUnavailable("ingress_sdk_handler_differs")
    return runtime, captured


def _current_request_input(runtime: SwitchboardInputCopies) -> _RequestInput:
    from fastmcp.server.dependencies import get_http_request

    try:
        cell = get_http_request().scope.get(_HTTP_CELL)
    except RuntimeError:
        cell = None
    if not isinstance(cell, _RequestInput) or cell.runtime is not runtime:
        raise CopyFloorUnavailable("ingress_sdk_request_unavailable")
    return cell


def install_ingress_middleware(mcp: Any, runtime: SwitchboardInputCopies) -> None:
    """Constructor-owned SDK claim before FunctionTool validation/delegation."""
    from fastmcp.server.middleware import Middleware

    installed = getattr(mcp, "_location_ingress_owner", None)
    if installed is runtime:
        return
    if installed is not None:
        raise CopyFloorUnavailable("ingress_sdk_constructor_differs")

    class InputClaims(Middleware):
        async def on_call_tool(self, context, call_next):
            if context.message.name == "ingest":
                envelope = _ingest_envelope(context.message.arguments)
                if envelope and _owntracks(envelope):
                    await runtime.claim_queued_input(_current_request_input(runtime), envelope)
            return await call_next(context)

    mcp.add_middleware(InputClaims())
    mcp._location_ingress_owner = runtime


async def bind_accepted_input(binding: tuple | None, request_id: UUID) -> None:
    if binding is not None:
        runtime, captured = binding
        await runtime.bind_accepted(captured, request_id)


async def spawn_ingest_processing(captured: tuple | None, body: dict, invoke: Any) -> asyncio.Task:
    """Actual fixed ingest producer reserves copied fields before Task publication."""
    if captured is None:
        return asyncio.create_task(invoke())
    runtime, parent = captured
    child = await runtime.reserve_child(parent, body)
    cell = _RequestInput(runtime, _Header(child.server, parent.task), child)

    async def process():
        # The frozen actual copied arguments, not request_id or a caller flag,
        # bind this separate Task. A changed bundle cannot enter processing.
        await runtime.claim_queued_input(cell, body)
        return await invoke()

    return asyncio.create_task(process())


def _buffer_body(ref: Any) -> dict:
    return {
        "request_id": ref.request_id,
        "message_text": ref.message_text,
        "source": ref.source,
        "event": ref.event,
        "sender": ref.sender,
        "attachments": ref.attachments,
        "payload_type": ref.payload_type,
        "triage_decision": ref.triage_decision,
        "triage_target": ref.triage_target,
    }


async def process_buffer_input(ref: Any, invoke: Any) -> None:
    """Own queued object is disposed only after its separate processing Task ends."""
    captured = ref._native_ingress
    if captured is None:
        await invoke(ref)
        return
    runtime, queued, body = captured
    if queued.kind != 2 or content_digest(_buffer_body(ref)) != queued.envelope_digest:
        raise CopyFloorUnavailable("ingress_buffer_copy_differs")
    cell = _RequestInput(runtime, _Header(queued.server, asyncio.current_task()), queued)
    await runtime.claim_queued_input(cell, body)
    task = await spawn_ingest_processing((runtime, queued), body, lambda: invoke(ref))

    async def finish() -> None:
        # Requested cancellation is not an end. The awaited Task must have
        # finished its actual unwind before this fixed object loses payloads.
        if not task.done():
            raise CopyFloorUnavailable("ingress_processing_still_active")
        if ref._native_ingress is not captured:
            raise CopyFloorUnavailable("ingress_buffer_binding_differs")
        ref.message_text = ""
        ref.source, ref.event, ref.sender = {}, {}, {}
        ref.attachments, ref.payload_type = None, None
        ref.triage_decision, ref.triage_target = None, None
        ref._native_ingress = None
        body.clear()
        # This private cleared-object witness is separate from Task-end;
        # the long-lived worker cannot attest an untouched queued payload.
        runtime._cleared_inputs.add(id(queued))
        runtime._ended_inputs.add(id(queued))
        await runtime.reconcile_observed_ends()

    try:
        await task
    except BaseException:
        try:
            await finish()
        except Exception:
            # Preserve the primary handler error/cancellation, never its args.
            # The original binding remains pending for committed-readback retry.
            logger.warning("Location ingress buffer disposition remains unresolved")
        raise
    else:
        # A successful business result cannot escape a failed end witness.
        await finish()


async def lock_ingress_census(conn: Any) -> None:
    """Control-first lock order matches birth, binding and end producers."""
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", _MUTEX)


async def require_ingress_closed(conn: Any, request_id: UUID, key: str, stored: Any) -> None:
    """Same owning mutex, full census; never infer a legacy missing birth closed."""
    await lock_ingress_census(conn)
    if await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM location_ingress_server_births b "
        "LEFT JOIN location_ingress_server_ends e USING(server_generation) "
        "WHERE e.server_generation IS NULL)"
    ):
        raise CopyFloorUnavailable("ingress_server_cohort_pending")
    rows = await conn.fetch(
        "SELECT b.copy_generation,b.server_generation,c.handler_generation,"
        "a.request_id,a.stored_digest,b.copy_kind,p.parent_generation,"
        "pb.copy_generation AS original_parent,pa.request_id AS parent_request,"
        "pa.stored_digest AS parent_digest,e.copy_generation AS ended,"
        "s.server_generation AS server_ended "
        "FROM location_ingress_input_births b "
        "LEFT JOIN location_ingress_input_parents p USING(copy_generation) "
        "LEFT JOIN location_ingress_input_births pb ON pb.copy_generation=p.parent_generation "
        "LEFT JOIN location_ingress_accepted_inputs pa ON pa.copy_generation=pb.copy_generation "
        "LEFT JOIN location_ingress_input_claims c ON c.copy_generation=b.copy_generation "
        "LEFT JOIN location_ingress_accepted_inputs a ON a.copy_generation=b.copy_generation "
        "LEFT JOIN location_ingress_input_ends e ON e.copy_generation=b.copy_generation "
        "LEFT JOIN location_ingress_server_ends s ON s.server_generation=b.server_generation "
        "WHERE b.dedupe_digest=$1 ORDER BY b.copy_generation",
        logical_digest(key),
    )
    digest = content_digest(
        {"raw_payload": stored["raw_payload"], "normalized_text": stored["normalized_text"]}
    )
    if not rows or any(
        row["handler_generation"] is None
        or row["copy_kind"] not in (1, 2, 3)
        or (row["copy_kind"] == 1 and row["parent_generation"] is not None)
        or (
            row["copy_kind"] in (2, 3)
            and (
                row["parent_generation"] is None
                or row["original_parent"] != row["parent_generation"]
                or row["parent_request"] != request_id
                or row["parent_digest"] != digest
            )
        )
        or row["request_id"] != request_id
        or row["stored_digest"] != digest
        or row["ended"] != row["copy_generation"]
        or row["server_ended"] != row["server_generation"]
        for row in rows
    ):
        raise CopyFloorUnavailable("ingress_input_cohort_pending")
