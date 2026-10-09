"""Switchboard-owned receiving input lifetimes, separate from connector ends.

Only the fixed owning daemon registers this writer. A native ASGI scope and
the actual SDK handler Task reserve their own original generations. Request
headers, accepted UUIDs and a remote caller's end cannot settle these copies.
Interrupted/unbound history remains unavailable until its own proof closes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from contextlib import asynccontextmanager
from contextvars import ContextVar
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
_scanner_scope: ContextVar[tuple | None] = ContextVar("location_ingress_scanner", default=None)
_processing_scope: ContextVar[tuple | None] = ContextVar(
    "location_ingress_processing", default=None
)


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
    unresolved_failure: bool = False


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
        self._unresolved_headers: set[int] = set()
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
            if header and id(binding) in getattr(self, "_unresolved_headers", ()):
                return
            # A completed Task can still own copied input in its cached
            # result, exception or traceback. Preserve the primary exception
            # and cancellation semantics; no Task-done receipt may proxy
            # disposal of those unresolved holders. Queued-object clearing
            # remains a distinct fixed producer below.
            if not header:
                if binding.unresolved_failure:
                    return
                if binding.kind == 3 and (
                    task.cancelled() or task.exception() is not None or task.result() is not None
                ):
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

    def retain_server_failure(self, binding: _Header) -> None:
        """Only the original ASGI producer can hold its failed server copy."""
        if (
            self._headers.get(id(binding)) is not binding
            or binding.task is not asyncio.current_task()
        ):
            raise CopyFloorUnavailable("ingress_server_scope_differs")
        self._unresolved_headers.add(id(binding))

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
        await self._bind_handler(binding, task)
        return binding

    async def _bind_handler(self, binding: _Input, task: asyncio.Task) -> None:
        # Repeated claim must re-establish the same committed nonce. A prior
        # failed write/readback never turns private in-memory state into proof.
        if self._inputs.get(id(binding)) is not binding or (
            binding.task is not None and binding.task is not task
        ):
            raise CopyFloorUnavailable("ingress_handler_differs")
        if binding.task is None:
            binding.task, binding.handler = task, uuid4()
            if binding.kind != 2:
                self._observe(binding, header=False)
        async with self.writer() as conn:
            await conn.execute(
                "INSERT INTO location_ingress_input_claims "
                "(copy_generation,handler_generation,incarnation) VALUES($1,$2,$3) "
                "ON CONFLICT(copy_generation) DO NOTHING",
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

    async def reserve_recovered_queue(
        self, header: _Header, row_id: UUID, body: dict
    ) -> _Input | None:
        """Own pre-read scanner binds its current copy to the recorded first writer."""
        if (
            not self.active
            or self._headers.get(id(header)) is not header
            or header.task is not asyncio.current_task()
            or header.task.done()
            or len(self._inputs) >= 1024
        ):
            raise CopyFloorUnavailable("ingress_scanner_scope_unknown")
        async with self.writer() as conn:
            row = await conn.fetchrow(
                "SELECT request_context,raw_payload,normalized_text FROM message_inbox "
                "WHERE id=$1 FOR UPDATE",
                row_id,
            )
            if row is None:
                raise CopyFloorUnavailable("ingress_scanner_row_unknown")
            context, raw = row["request_context"], row["raw_payload"]
            if not isinstance(context, dict) or not isinstance(raw, dict):
                raise CopyFloorUnavailable("ingress_scanner_source_unknown")
            expected = {
                "request_id": str(context.get("request_id", str(row_id))),
                "message_text": row["normalized_text"],
                "source": raw.get("source", {}),
                "event": raw.get("event", {}),
                "sender": raw.get("sender", {}),
                "attachments": None,
                "payload_type": context.get("payload_type"),
                "triage_decision": context.get("triage_decision"),
                "triage_target": context.get("triage_target"),
            }
            if content_digest(body) != content_digest(expected):
                raise CopyFloorUnavailable("ingress_scanner_body_changed")
            original = await conn.fetchrow(
                "SELECT s.copy_generation,s.dedupe_digest,s.stored_digest,b.copy_kind "
                "FROM location_ingress_inbox_sources s "
                "LEFT JOIN location_ingress_input_births b USING(copy_generation) "
                "WHERE s.request_id=$1",
                row_id,
            )
            source = raw.get("source")
            if (
                original is None
                and isinstance(source, dict)
                and isinstance(source.get("provider"), str)
                and source["provider"]
                and isinstance(source.get("channel"), str)
                and source["channel"]
                and source["provider"] != "owntracks"
                and source["channel"] != "owntracks"
            ):
                # A label or missing private stamp alone is not ordinary
                # authority. Compare the original committed shared admission
                # record to every canonical selector before legacy processing.
                admitted = await conn.fetchrow(
                    "SELECT source_provider,source_channel,source_endpoint_identity,"
                    "source_sender_identity,dedupe_key FROM public.ingestion_events WHERE id=$1",
                    row_id,
                )
                sender = raw.get("sender")
                expected_admission = (
                    source["provider"],
                    source["channel"],
                    source.get("endpoint_identity"),
                    sender.get("identity") if isinstance(sender, dict) else None,
                    context.get("dedupe_key"),
                )
                if (
                    admitted is None
                    or any(not isinstance(value, str) or not value for value in expected_admission)
                    or tuple(admitted.values()) != expected_admission
                ):
                    raise CopyFloorUnavailable("ingress_scanner_ordinary_admission_unknown")
                return None  # Fixed original ordinary admission; no native proof.
            key = context.get("dedupe_key")
            stored = content_digest({"raw_payload": raw, "normalized_text": row["normalized_text"]})
            if (
                original is None
                or original["copy_kind"] != 1
                or not isinstance(key, str)
                or original["dedupe_digest"] != logical_digest(key)
                or original["stored_digest"] != stored
                or await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM location_retention_source_floors "
                    "WHERE dedupe_digest=$1)",
                    original["dedupe_digest"],
                )
            ):
                raise CopyFloorUnavailable("ingress_scanner_original_unknown")
            child = _Input(
                uuid4(),
                header.generation,
                original["dedupe_digest"],
                content_digest(body),
                None,
                kind=2,
            )
            self._inputs[id(child)] = child
            await conn.execute(
                "INSERT INTO location_ingress_input_births(copy_generation,server_generation,"
                "dedupe_digest,envelope_digest,copy_kind) VALUES($1,$2,$3,$4,2)",
                child.generation,
                child.server,
                child.dedupe_digest,
                child.envelope_digest,
            )
            await conn.execute(
                "INSERT INTO location_ingress_input_parents "
                "(copy_generation,parent_generation) VALUES($1,$2)",
                child.generation,
                original["copy_generation"],
            )
            await conn.execute(
                "INSERT INTO location_ingress_accepted_inputs "
                "(copy_generation,request_id,stored_digest) VALUES($1,$2,$3)",
                child.generation,
                row_id,
                stored,
            )
        async with self.pool.acquire() as observed:
            committed = await observed.fetchrow(
                "SELECT b.server_generation,b.dedupe_digest,b.envelope_digest,b.copy_kind,"
                "p.parent_generation,a.request_id,a.stored_digest "
                "FROM location_ingress_input_births b "
                "LEFT JOIN location_ingress_input_parents p USING(copy_generation) "
                "LEFT JOIN location_ingress_accepted_inputs a USING(copy_generation) "
                "WHERE b.copy_generation=$1",
                child.generation,
            )
        if committed is None or tuple(committed.values()) != (
            child.server,
            child.dedupe_digest,
            child.envelope_digest,
            2,
            original["copy_generation"],
            row_id,
            stored,
        ):
            raise CopyFloorUnavailable("ingress_scanner_commit_unknown")
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
            original = await conn.fetchrow(
                "SELECT dedupe_digest,stored_digest FROM location_ingress_inbox_sources "
                "WHERE request_id=$1",
                request_id,
            )
            if original is None or tuple(original.values()) != (binding.dedupe_digest, body):
                raise CopyFloorUnavailable("ingress_canonical_source_unknown")
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
            if self._settlers:
                await asyncio.gather(*tuple(self._settlers), return_exceptions=True)
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

        try:
            await self.app({**scope, _HTTP_CELL: cell}, native_receive, send)
        except BaseException:
            # A traceback can retain the native receive closure/body even
            # after this Task completes. This only withholds its own end.
            try:
                self.runtime.retain_server_failure(header)
            except Exception:
                logger.warning("Location ingress server failure binding remains unresolved")
            raise


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
            captured = None
            try:
                if context.message.name == "ingest":
                    envelope = _ingest_envelope(context.message.arguments)
                    if envelope and _owntracks(envelope):
                        cell = _current_request_input(runtime)
                        captured = cell.queued
                        await runtime.claim_queued_input(cell, envelope)
                return await call_next(context)
            except BaseException:
                if isinstance(captured, _Input) and runtime._inputs.get(id(captured)) is captured:
                    # The SDK may catch this outside the middleware and finish
                    # its Task normally while retaining the error's input copy.
                    # This marker only withholds the original input end.
                    captured.unresolved_failure = True
                raise

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
        token = _processing_scope.set((runtime, child))
        try:
            return await invoke()
        finally:
            _processing_scope.reset(token)

    return asyncio.create_task(process())


def retain_ingress_processing_failure() -> None:
    """Fixed owning callbacks hold their actual copy after a caught failure.

    This private scope can only withhold disposition. It cannot close a copy
    or attest disposal of a pipeline result, log, runtime or routed descendant.
    """
    captured = _processing_scope.get()
    if captured is None:
        return
    runtime, binding = captured
    if (
        runtime._inputs.get(id(binding)) is not binding
        or binding.kind != 3
        or binding.task is not asyncio.current_task()
    ):
        raise CopyFloorUnavailable("ingress_processing_scope_differs")
    binding.unresolved_failure = True


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


def _clear_buffer_ref(ref: Any) -> tuple:
    """Fixed owning object disposal validates its current full frozen body."""
    captured = ref._native_ingress
    if captured is None:
        raise CopyFloorUnavailable("ingress_buffer_binding_unknown")
    runtime, queued, body = captured
    if (
        runtime._inputs.get(id(queued)) is not queued
        or queued.kind != 2
        or content_digest(_buffer_body(ref)) != queued.envelope_digest
        or content_digest(body) != queued.envelope_digest
    ):
        raise CopyFloorUnavailable("ingress_buffer_copy_differs")
    ref.message_text = ""
    ref.source, ref.event, ref.sender = {}, {}, {}
    ref.attachments, ref.payload_type = None, None
    ref.triage_decision, ref.triage_target = None, None
    ref._native_ingress = None
    body.clear()
    runtime._cleared_inputs.add(id(queued))
    return runtime, queued


def discard_buffer_input(ref: Any) -> None:
    """Dispose only the exact rejected or stopped owning queue object."""
    if ref._native_ingress is None:
        return  # Existing ordinary buffer behavior supplies no native proof.
    runtime, queued, _body = ref._native_ingress
    if queued.task is not None:
        raise CopyFloorUnavailable("ingress_discard_claim_already_present")
    _clear_buffer_ref(ref)

    async def finish():
        task = asyncio.current_task()
        await runtime._bind_handler(queued, task)
        runtime._ended_inputs.add(id(queued))
        await runtime.reconcile_observed_ends()

    settling = asyncio.create_task(finish())
    runtime._settlers.add(settling)
    settling.add_done_callback(runtime._settled)


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
        _clear_buffer_ref(ref)
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


async def run_buffer_scanner(pool: Any, invoke: Any) -> int:
    """A separate actual Task captures its receiving lifetime BEFORE row reads."""
    runtime = _writers.get(pool)
    if runtime is None:
        return await invoke()

    async def scan():
        header = await runtime.reserve_header()
        token = _scanner_scope.set((runtime, header))
        try:
            return await invoke()
        finally:
            _scanner_scope.reset(token)

    return await asyncio.create_task(scan())


async def reserve_scanned_buffer_input(pool: Any, ref: Any) -> None:
    """Fixed own scanner publishes only independently read-back canonical lineage."""
    runtime = _writers.get(pool)
    if runtime is None:
        return
    current = _scanner_scope.get()
    if (
        current is None
        or current[0] is not runtime
        or current[1].task is not asyncio.current_task()
    ):
        raise CopyFloorUnavailable("ingress_scanner_scope_unknown")
    body = _buffer_body(ref)
    child = await runtime.reserve_recovered_queue(current[1], ref.message_inbox_id, body)
    if child is not None:
        ref._native_ingress = (runtime, child, body)


async def lock_registered_ingress_writer(pool: Any, conn: Any) -> None:
    """Actual canonical owning transaction enters control before dedup/row locks."""
    runtime = _writers.get(pool)
    if runtime is None:
        return  # Explicit unconfigured legacy writer supplies no lineage proof.
    actual = await conn.fetchrow("SELECT current_schema(),current_user")
    if tuple(actual.values()) != ("switchboard", "butler_switchboard_rw"):
        raise CopyFloorUnavailable("ingress_owning_identity_differs")
    await lock_ingress_census(conn)


async def capture_canonical_ingress_source(
    pool: Any,
    conn: Any,
    request_id: UUID,
    request_context: dict,
    raw_payload: dict,
    normalized_text: str,
) -> None:
    """Same actual first INSERT transaction freezes its private SDK producer."""
    runtime = _writers.get(pool)
    if runtime is None:
        return
    cell = _current_request_input(runtime)
    binding = cell.queued
    task = asyncio.current_task()
    key = request_context.get("dedupe_key")
    if (
        not runtime.active
        or binding is None
        or runtime._inputs.get(id(binding)) is not binding
        or binding.kind != 1
        or binding.task is not task
        or task is None
        or task.done()
        or not isinstance(key, str)
        or logical_digest(key) != binding.dedupe_digest
    ):
        raise CopyFloorUnavailable("ingress_canonical_producer_unknown")
    actual = await conn.fetchrow(
        "SELECT b.server_generation,b.dedupe_digest,b.envelope_digest,b.copy_kind,"
        "c.handler_generation,c.incarnation FROM location_ingress_input_births b "
        "LEFT JOIN location_ingress_input_claims c USING(copy_generation) "
        "WHERE b.copy_generation=$1",
        binding.generation,
    )
    expected = (
        binding.server,
        binding.dedupe_digest,
        binding.envelope_digest,
        1,
        binding.handler,
        runtime.incarnation,
    )
    if actual is None or tuple(actual.values()) != expected:
        raise CopyFloorUnavailable("ingress_canonical_claim_unknown")
    await conn.execute(
        "INSERT INTO location_ingress_inbox_sources "
        "(request_id,copy_generation,dedupe_digest,stored_digest) VALUES($1,$2,$3,$4)",
        request_id,
        binding.generation,
        binding.dedupe_digest,
        content_digest({"raw_payload": raw_payload, "normalized_text": normalized_text}),
    )


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
        "s.server_generation AS server_ended,"
        "r.input_generation AS runtime_generation,r.request_id AS runtime_request,"
        "b.envelope_digest AS processing_digest,r.envelope_digest AS runtime_input_digest,"
        "r.stored_digest AS runtime_source_digest,r.prompt_digest AS runtime_prompt_digest,"
        "r.receiving_session AS runtime_session,"
        "rb.receiving_session AS bound_runtime_session,rb.prompt_digest AS bound_runtime_prompt,"
        "rd.receipt_id AS runtime_disposition,"
        "rs.prompt AS runtime_current_prompt,rs.result AS runtime_current_result,"
        "rs.tool_calls AS runtime_current_calls,rs.error AS runtime_current_error,"
        "rs.effective_system_prompt AS runtime_current_system,"
        "rs.prompt_provenance AS runtime_current_provenance,"
        "rd.reduced_system_digest AS runtime_reduced_system,"
        "rd.reduced_provenance_digest AS runtime_reduced_provenance "
        "FROM location_ingress_input_births b "
        "LEFT JOIN location_ingress_input_parents p USING(copy_generation) "
        "LEFT JOIN location_ingress_input_births pb ON pb.copy_generation=p.parent_generation "
        "LEFT JOIN location_ingress_accepted_inputs pa ON pa.copy_generation=pb.copy_generation "
        "LEFT JOIN location_ingress_input_claims c ON c.copy_generation=b.copy_generation "
        "LEFT JOIN location_ingress_accepted_inputs a ON a.copy_generation=b.copy_generation "
        "LEFT JOIN location_ingress_input_ends e ON e.copy_generation=b.copy_generation "
        "LEFT JOIN location_ingress_server_ends s ON s.server_generation=b.server_generation "
        "LEFT JOIN location_ingress_runtime_inputs r ON r.copy_generation=b.copy_generation "
        "LEFT JOIN location_runtime_context_bindings rb USING(input_generation) "
        "LEFT JOIN location_runtime_context_dispositions rd USING(input_generation) "
        "LEFT JOIN sessions rs ON rs.id=r.receiving_session "
        "WHERE b.dedupe_digest=$1 ORDER BY b.copy_generation",
        logical_digest(key),
    )
    digest = content_digest(
        {"raw_payload": stored["raw_payload"], "normalized_text": stored["normalized_text"]}
    )
    original = await conn.fetchrow(
        "SELECT s.dedupe_digest,s.stored_digest,b.copy_kind "
        "FROM location_ingress_inbox_sources s "
        "LEFT JOIN location_ingress_input_births b USING(copy_generation) "
        "WHERE s.request_id=$1",
        request_id,
    )
    if original is None or tuple(original.values()) != (logical_digest(key), digest, 1):
        raise CopyFloorUnavailable("ingress_canonical_source_unknown")
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
    for row in rows:
        if row.get("runtime_generation") is not None and (
            row["copy_kind"] != 3
            or row["runtime_request"] != request_id
            or row["runtime_source_digest"] != digest
            or row["runtime_input_digest"] != row["processing_digest"]
            or row["runtime_session"] != row["bound_runtime_session"]
            or row["runtime_prompt_digest"] != row["bound_runtime_prompt"]
            or row["runtime_disposition"] is None
            or row["runtime_current_prompt"] != "[Location input forgotten]"
            or row["runtime_current_result"] != "[Location output forgotten]"
            or row["runtime_current_calls"] != []
            or row["runtime_current_error"] is not None
            or not isinstance(row["runtime_current_system"], str)
            or row["runtime_reduced_system"] is None
            or row["runtime_reduced_provenance"] is None
            or hashlib.sha256(row["runtime_current_system"].encode()).digest()
            != row["runtime_reduced_system"]
            or content_digest(row["runtime_current_provenance"])
            != row["runtime_reduced_provenance"]
        ):
            raise CopyFloorUnavailable("ingress_runtime_cohort_pending")
    # Every actually captured structured attempt remains a distinct input
    # holder until its native SDK/result/routed descendants are disposed.
    # This stage captures it; no model return or parent Task end is its receipt.
    if await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM location_ingress_structured_inputs s "
        "LEFT JOIN location_ingress_input_births b USING(copy_generation) "
        "WHERE b.dedupe_digest=$1 OR b.copy_generation IS NULL)",
        logical_digest(key),
    ):
        raise CopyFloorUnavailable("ingress_structured_cohort_pending")
