"""Private custody admission under the existing trusted daemon/host premise.

Enrollment is a trusted startup action. Neither a model DTO nor a SQL role name
is an enrolled principal. Every acquired domain writer is bound, checked and
explicitly unbound; asyncpg's normal reset is not evidence of custody cleanup.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import asyncpg
from opentelemetry.instrumentation.utils import suppress_instrumentation

from butlers.core.custody_source import (
    CustodyError,
    VerifiedCustodyCall,
    _verified_call,
    canonical_json,
    canonical_targets,
    canonical_uuid,
    closed_object,
    digest,
    parse_wire,
    sha256_hex,
    validate_minted_binding,
)

_FAMILIES = frozenset(
    {
        "accepted_ingress",
        "provider_callback",
        "owner_command",
        "host_command",
        "scheduled_task",
        "deferred_notice",
        "domain_evidence",
        "provider_inventory",
    }
)
_OPERATIONS = frozenset(
    {
        "hold",
        "release",
        "replaced",
        "yes",
        "no",
        "revoke_sessions",
        "write",
        "provider_start",
        "eligibility",
        "question",
    }
)
_ACTOR = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z", re.ASCII)
_TRANSACTION_LIMIT_SECONDS = 30


@dataclass(frozen=True)
class CustodyProfile:
    """Constructor-fixed scopes loaded by trusted startup, never a request model."""

    actor: str
    role: str
    source_kinds: tuple[str, ...]
    operations: tuple[str, ...]
    audiences: tuple[str, ...]
    config_digest: str

    def __post_init__(self) -> None:
        if not _ACTOR.fullmatch(self.actor):
            raise CustodyError("invalid")
        permitted_role = (
            self.actor not in {"dashboard", "host-switchboard"}
            and not self.actor.startswith("connector-")
            and re.fullmatch(r"butler_[A-Za-z_][A-Za-z0-9_]*_rw", self.role) is not None
            or self.actor == "host-switchboard"
            and re.fullmatch(r"butler_[A-Za-z_][A-Za-z0-9_]*_rw", self.role) is not None
            or self.actor == "dashboard"
            and self.role == "dashboard_auth_api"
            or self.actor.startswith("connector-")
            and self.role == "connector_writer"
        )
        if not permitted_role or not set(self.source_kinds) <= _FAMILIES:
            raise CustodyError("invalid")
        if self.actor == "host-switchboard" and (
            self.source_kinds != ("host_command",)
            or self.audiences != ("switchboard",)
            or not set(self.operations)
            <= {"hold", "release", "replaced", "revoke_sessions", "eligibility"}
        ):
            raise CustodyError("invalid")
        if not set(self.operations) <= _OPERATIONS:
            raise CustodyError("invalid")
        if not self.operations and (
            self.actor != "relationship"
            or self.role != "butler_relationship_rw"
            or self.source_kinds != ("domain_evidence",)
            or self.audiences
        ):
            raise CustodyError("invalid")
        if not all(_ACTOR.fullmatch(actor) for actor in self.audiences):
            raise CustodyError("invalid")
        sha256_hex(self.config_digest)
        for values, limit in ((self.source_kinds, 16), (self.operations, 16), (self.audiences, 32)):
            if len(values) > limit or tuple(sorted(set(values))) != values:
                raise CustodyError("invalid")


async def _sql(connection: asyncpg.Connection, statement: str, *values: Any) -> dict:
    """Transport no driver errors or private SQL values into model/log surfaces."""
    try:
        with suppress_instrumentation():
            result = await connection.fetchval(statement, *values)
        if type(result) is not dict:
            raise CustodyError("unavailable")
        return result
    except asyncpg.PostgresError as exc:
        if exc.sqlstate and exc.sqlstate.startswith("08"):
            raise CustodyError("unknown") from None
        if exc.sqlstate in {"42501", "22023", "40001"}:
            raise CustodyError("refused") from None
        raise CustodyError("unavailable") from None
    except (OSError, asyncpg.InterfaceError):
        raise CustodyError("unknown") from None


class CustodyWriter:
    """One actual acquired connection; access ends before pool return."""

    def __init__(self, connection: asyncpg.Connection, generation: int) -> None:
        self._connection = connection
        self.generation = generation
        self._active = True

    async def _call(self, statement: str, *values: Any) -> dict:
        if not self._active:
            raise CustodyError("refused")
        return await _sql(self._connection, statement, *values)

    async def register_source(self, family: str, projection: dict) -> dict:
        # This is a private adapter method, not a model tool or HTTP payload.
        canonical_json(projection)
        if family not in _FAMILIES:
            raise CustodyError("invalid")
        if projection.get("target_set") != canonical_targets(projection.get("target_set")):
            raise CustodyError("invalid")
        if "issuer_binding" in projection:
            canonical_targets([projection["issuer_binding"]])
        result = await self._call(
            "SELECT public.custody_source_register($1,$2::jsonb)", family, projection
        )
        closed_object(result, required={"source_ref", "source_digest", "projection"})
        canonical_uuid(result["source_ref"])
        if result["source_digest"] != digest(result["projection"]) or (
            family not in {"accepted_ingress", "owner_command", "host_command"}
            and result["projection"] != projection
        ):
            raise CustodyError("refused")
        return result

    async def admit_write(self, source_ref: uuid.UUID, operation: str, target_set: list) -> dict:
        return await self._call(
            "SELECT public.custody_admit_write($1,$2,$3::jsonb)", source_ref, operation, target_set
        )

    async def commit_command(self, command_id: uuid.UUID, call_ref: uuid.UUID) -> dict:
        return await self._call("SELECT public.custody_commit_command($1,$2)", command_id, call_ref)

    async def result_read(self, command_id: uuid.UUID, call_ref: uuid.UUID) -> dict:
        return await self._call("SELECT public.custody_result_read($1,$2)", command_id, call_ref)

    async def provider_start(
        self, source_ref: uuid.UUID, call_ref: uuid.UUID, operation_digest: str
    ) -> dict:
        return await self._call(
            "SELECT public.custody_mark_provider_start($1,$2,$3)",
            source_ref,
            call_ref,
            operation_digest,
        )


_verified_writer: ContextVar[tuple[CustodyAdmission, CustodyWriter] | None] = ContextVar(
    "custody_registered_writer", default=None
)


class CustodyAdmission:
    """Dedicated live DB anchor and constructor-owned pool/role lifecycle.

    The host enrollment callable is supplied only by fixed administrative
    startup. A separate restricted API process receives its bounded receipt
    through the owning parent's private startup channel; it receives no admin
    password or host enrollment operation. This class does not implement that
    deployment wiring or claim isolation from a compromised same-UID host.
    """

    def __init__(
        self,
        profile: CustodyProfile,
        pool: asyncpg.Pool,
        anchor: asyncpg.Connection,
        *,
        host_enroll: Callable[[dict], Awaitable[dict]],
    ) -> None:
        self.profile = profile
        self._pool = pool
        self._anchor = anchor
        self._host_enroll = host_enroll
        self._anchor_lock = asyncio.Lock()
        self._renewal: asyncio.Task | None = None
        self._ready = False
        self._receipt: dict | None = None
        self._incarnation = uuid.uuid4()
        self._active_writers: set[int] = set()
        self._writer_objects: dict[int, CustodyWriter] = {}

    async def start(self) -> dict:
        if self._ready or self._renewal is not None:
            raise CustodyError("conflict")
        # SQL identifiers are derived solely from the validated constructor role.
        quoted_role = '"' + self.profile.role.replace('"', '""') + '"'
        await self._anchor.execute(f"SET ROLE {quoted_role}")
        proposal = await self._anchor_call("SELECT public.custody_anchor_begin()")
        manifest = {
            "nonce": str(proposal["nonce"]),
            "logical_actor": self.profile.actor,
            "role": self.profile.role,
            "adapter_incarnation": str(self._incarnation),
            "config_digest": self.profile.config_digest,
            "source_kinds": list(self.profile.source_kinds),
            "operations": list(self.profile.operations),
            "audiences": list(self.profile.audiences),
        }
        self._receipt = await self._host_enroll(manifest)
        canonical_uuid(str(self._receipt["process_id"]))
        await self._anchor_call("SELECT public.custody_anchor_renew()")
        self._ready = True
        self._renewal = asyncio.create_task(
            self._renew(), name=f"custody-anchor-{self.profile.actor}"
        )
        return dict(self._receipt)

    async def _anchor_call(self, statement: str, *values: Any) -> dict:
        async with self._anchor_lock:
            return await _sql(self._anchor, statement, *values)

    async def _renew(self) -> None:
        try:
            while self._ready:
                await asyncio.sleep(10)
                if self._ready:
                    await self._anchor_call("SELECT public.custody_anchor_renew()")
        except (Exception, asyncio.CancelledError):
            # Loss of renewal is fail-closed. The server lease is the bounded
            # witness; this is not an instantaneous OS-death attestation.
            self._ready = False

    async def stop(self) -> None:
        self._ready = False
        if self._renewal is not None:
            self._renewal.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._renewal
            self._renewal = None
        await self._anchor.close()

    @asynccontextmanager
    async def writer(self) -> AsyncIterator[CustodyWriter]:
        """Acquire an owning pool connection and commit its guarded transaction."""
        if not self._ready:
            raise CustodyError("unavailable")
        async with self._pool.acquire() as connection:
            async with self.bound_writer(connection) as writer:
                yield writer

    def _require_acquired_connection(self, connection: asyncpg.Connection) -> None:
        """Check asyncpg's actual checkout identity before any SQL or role change.

        These private asyncpg fields describe its acquisition lifecycle. They
        are not an authority token: the fixed trusted constructor owns the
        pool, and SQL independently proves the backend/role/nonce/incarnation.
        A future asyncpg lifecycle change must fail closed here.
        """
        if not isinstance(connection, asyncpg.pool.PoolConnectionProxy):
            raise CustodyError("refused")
        holder = connection._holder
        if (
            not isinstance(holder, asyncpg.pool.PoolConnectionHolder)
            or holder._pool is not self._pool
            or holder not in self._pool._holders
            or holder._proxy is not connection
            or holder._con is not connection._con
            or connection._con is None
            or holder._in_use is None
            or holder._in_use.done()
            or connection.is_closed()
        ):
            raise CustodyError("refused")
        # An existing transaction may already hold domain locks. A savepoint
        # cannot establish the mandated first-lock order or acknowledge COMMIT.
        if connection.is_in_transaction() or id(connection) in self._active_writers:
            raise CustodyError("conflict")

    @asynccontextmanager
    async def bound_writer(self, connection: asyncpg.Connection) -> AsyncIterator[CustodyWriter]:
        """Own the transaction on an already acquired SAME owning connection.

        Call before any domain transaction/locks. Pass writer._connection to
        existing conn-aware writers; their nested transactions are savepoints.
        This context owns the outer COMMIT and cleanup, but never releases the
        caller's pool acquisition. Reuse the yielded writer for nested work.
        """
        if not self._ready:
            raise CustodyError("unavailable")
        self._require_acquired_connection(connection)
        self._active_writers.add(id(connection))
        try:
            writer = None
            generation = None
            try:
                role = '"' + self.profile.role.replace('"', '""') + '"'
                # asyncpg reset clears SET ROLE on release. Re-assert it for
                # EVERY checkout before the inert begin, not only pool init.
                await connection.execute(f"SET ROLE {role}")
                begin = await _sql(connection, "SELECT public.custody_connection_begin()")
                generation = begin["acquisition_generation"]
                nonce = str(begin["writer_nonce"])
                await self._anchor_call("SELECT public.custody_bind_connection($1)", nonce)
                body_completed = False
                try:
                    async with (
                        asyncio.timeout(_TRANSACTION_LIMIT_SECONDS),
                        connection.transaction(),
                    ):
                        # finish takes the auth singleton/control locks inside
                        # THIS business transaction before any domain handler.
                        await _sql(connection, "SELECT public.custody_connection_finish($1)", nonce)
                        writer = CustodyWriter(connection, generation)
                        self._writer_objects[id(connection)] = writer
                        yield writer
                        body_completed = True
                except asyncio.CancelledError:
                    # Cancellation can interrupt COMMIT or its acknowledgement.
                    # Preserve cancellation while discarding this physical
                    # backend; no cleanup verdict can turn it into success.
                    if body_completed or connection.is_in_transaction():
                        connection.terminate()
                    # Before COMMIT, an acknowledged transaction rollback can
                    # still unbind safely. Interrupted COMMIT/rollback cannot.
                    raise
                except (OSError, asyncpg.InterfaceError, TimeoutError):
                    # This includes an unknown COMMIT acknowledgement. The
                    # caller must read the same durable identity, never remint.
                    connection.terminate()
                    raise CustodyError("unknown") from None
                except asyncpg.PostgresError as exc:
                    if exc.sqlstate and exc.sqlstate.startswith("08"):
                        connection.terminate()
                        raise CustodyError("unknown") from None
                    raise CustodyError("refused") from None
            finally:
                self._writer_objects.pop(id(connection), None)
                if writer is not None:
                    writer._active = False
                # A terminated physical backend cannot be reused. Do not return
                # from this finally: that would suppress the unknown verdict.
                if generation is not None and not connection.is_closed():
                    cleanup = asyncio.create_task(
                        _sql(connection, "SELECT public.custody_connection_unbind($1)", generation)
                    )
                    try:
                        async with asyncio.timeout(10):
                            await asyncio.shield(cleanup)
                            if not self.profile.operations:
                                # API readers keep their existing login policy.
                                # RESET ALL is not our proof of RESET ROLE:
                                # explicitly restore it after acknowledged
                                # UNBIND and discard on an unknown reset ACK.
                                await connection.execute("RESET ROLE")
                    except BaseException as exc:
                        # Never return a connection with unknown custody state.
                        # terminate() makes asyncpg discard this physical backend.
                        connection.terminate()
                        cleanup.cancel()
                        with contextlib.suppress(BaseException):
                            await cleanup
                        if isinstance(exc, asyncio.CancelledError):
                            raise
                        raise CustodyError("unknown") from None
                elif generation is None and not connection.is_closed():
                    # An ambiguous begin may have committed a generation whose
                    # reply was lost. It cannot safely enter another checkout.
                    connection.terminate()
        finally:
            self._active_writers.discard(id(connection))

    def owns_writer(self, writer: CustodyWriter) -> bool:
        """Private live object identity, never a public authority assertion."""
        return (
            type(writer) is CustodyWriter
            and writer._active
            and self._writer_objects.get(id(writer._connection)) is writer
            and id(writer._connection) in self._active_writers
            and not writer._connection.is_closed()
            and writer._connection.is_in_transaction()
        )

    def current_verified_writer(self) -> CustodyWriter:
        """Return only this receiving guard's still-active SAME domain writer.

        Never resolve by actor/session/connection strings. This private object
        has no transferable authority: every final domain mutation still calls
        its SQL admission/currentness check within this transaction.
        """
        bound = _verified_writer.get()
        if bound is None or bound[0] is not self or not self.owns_writer(bound[1]):
            raise CustodyError("refused")
        return bound[1]

    async def mint(
        self, source_ref: uuid.UUID, operation: dict, destination_actor: str, *, source_digest: str
    ) -> dict:
        if not self._ready:
            raise CustodyError("unavailable")
        canonical_json(operation)
        if operation.get("target_set") != canonical_targets(operation.get("target_set")):
            raise CustodyError("invalid")
        sha256_hex(source_digest)
        minted = await self._anchor_call(
            "SELECT public.custody_mint($1,$2::jsonb,$3)", source_ref, operation, destination_actor
        )
        return validate_minted_binding(
            minted,
            source_ref=source_ref,
            source_digest=source_digest,
            operation=operation,
            destination_actor=destination_actor,
            receipt=self._receipt,
        )

    async def challenge(self, call_ref: uuid.UUID, operation_digest: str) -> dict:
        sha256_hex(operation_digest)
        return await self._anchor_call(
            "SELECT public.custody_challenge($1,$2)", call_ref, operation_digest
        )

    async def respond(self, call_ref: uuid.UUID, challenge_ref: str) -> dict:
        canonical_uuid(challenge_ref)
        return await self._anchor_call(
            "SELECT public.custody_respond($1,$2)", call_ref, challenge_ref
        )

    async def start_effect(self, raw: bytes) -> dict:
        """Commit an ordinary effect's marker before returning any start grant.

        Provider I/O belongs to the caller AFTER this method returns. A network
        or COMMIT error raises unknown; retry/readback uses the same stable
        owning effect and can never create a second possible start.
        """
        async with self.verified(raw) as (writer, call):
            result = await writer.provider_start(
                call.source_ref, call.call_ref, call.operation_digest
            )
        return result

    @asynccontextmanager
    async def verified(
        self, raw: bytes
    ) -> AsyncIterator[tuple[CustodyWriter, VerifiedCustodyCall]]:
        """Receiving outer guard, before privileged instrumentation/domain code."""
        wire = parse_wire(raw)
        async with self.writer() as writer:
            proof = await writer._call(
                "SELECT public.custody_verify($1,$2,$3)",
                uuid.UUID(wire["call_ref"]),
                wire["challenge_ref"],
                wire["operation_digest"],
            )
            operation = proof["operation"]
            if wire["method"] != operation["method"] or digest(wire["arguments"]) != digest(
                operation["arguments"]
            ):
                raise CustodyError("refused")
            call = VerifiedCustodyCall(
                uuid.UUID(wire["call_ref"]),
                wire["challenge_ref"],
                wire["operation_digest"],
                uuid.UUID(proof["source_ref"]),
                wire["method"],
                wire["arguments"],
                writer.generation,
            )
            token = _verified_call.set(call)
            writer_token = _verified_writer.set((self, writer))
            original_arguments = canonical_json(call.arguments)
            original_operation = canonical_json(operation)
            try:
                yield writer, call
                # A frozen dataclass does not freeze a nested argument dict.
                # Refuse a handler that changes the admitted operation before
                # the outer transaction can acknowledge its domain COMMIT.
                if canonical_json(call.arguments) != original_arguments:
                    raise CustodyError("refused")
                # Clock/lease/challenge expiry can advance while the real tool
                # waits. Recheck on THIS committing writer, after the handler;
                # neither initial admission nor a second pool verdict suffices.
                final = await writer._call(
                    "SELECT public.custody_verify($1,$2,$3)",
                    call.call_ref,
                    call.challenge_ref,
                    call.operation_digest,
                )
                if (
                    final["source_ref"] != str(call.source_ref)
                    or canonical_json(final["operation"]) != original_operation
                ):
                    raise CustodyError("refused")
            finally:
                _verified_writer.reset(writer_token)
                _verified_call.reset(token)


class CustodyMcpService:
    """Two registered protected MCP operations with constructor-fixed handlers.

    The owning ASGI integration must position the verification guard before its
    existing tool instrumentation. Merely registering these handlers does not
    prove that daemon/run/up/separate-process deployment integration has landed.
    """

    def __init__(
        self,
        admission: CustodyAdmission,
        handlers: Mapping[str, Callable[[CustodyWriter, VerifiedCustodyCall], Awaitable[dict]]],
    ) -> None:
        self._admission = admission
        self._handlers = dict(handlers)

    def register(self, mcp: Any) -> None:
        self.install_guard(mcp)
        mcp.tool(name="custody.challenge", task=False)(self.challenge)
        mcp.tool(name="custody.apply", task=False)(self.apply)

    def install_guard(self, mcp: Any) -> None:
        from butlers.core.custody_mcp import CustodyToolAdmission

        # Install first: every FunctionTool wrapper executes inside this guard,
        # including the existing daemon capture/logging/span wrappers. The
        # trusted startup owns this server; modules cannot supply an admission.
        if any(isinstance(item, CustodyToolAdmission) for item in mcp.middleware):
            raise CustodyError("conflict")
        # FastMCP's SDK handler has a DEBUG line with the complete argument
        # payload before its middleware chain. Keep that raw-argument logger
        # pinned like httpx/httpcore; custody wire is never diagnostic output.
        logging.getLogger("fastmcp.server.mixins.mcp_operations").setLevel(logging.WARNING)
        mcp.middleware.insert(0, CustodyToolAdmission(self._admission))

    async def challenge(self, call_ref: str, operation_digest: str) -> dict:
        from butlers.core.custody_mcp import admitted_challenge

        return admitted_challenge(
            self._admission, canonical_uuid(call_ref), sha256_hex(operation_digest)
        )

    async def apply(self, wire: str) -> dict:
        from butlers.core.custody_mcp import admitted_apply

        # Raw JSON text retains duplicate keys for the registered parser. A
        # dict-valued DTO would have lost them before this handler was reached.
        call = admitted_apply(self._admission, wire)
        writer = self._admission.current_verified_writer()
        handler = self._handlers.get(call.method)
        if handler is None:
            raise CustodyError("refused")
        try:
            return await handler(writer, call)
        except CustodyError:
            raise
        except Exception:
            # Domain/driver/provider exception strings can contain source or
            # credential values. Existing FunctionTool wrappers log exception
            # text, so keep the public failure content-free before that layer.
            raise CustodyError("unavailable") from None
