"""Fixed owning catalog loan protocol, with online receiver/body readback.

Registry addresses select a daemon; they do not attest a copy. A source loan
requires a live private receiver challenge and its own committed generation.
The source re-reads its canonical body after that admission. No public tool
argument contains a capability, verifier URL, row body or authority verdict.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from butlers.chronicler.location_retention import PolicyUnavailableError
from butlers.location_retention import content_digest

_HEADER = "X-Butlers-Location-Copy"
_PATH = "/internal/location-retention/catalog"
_MAX_PENDING = 256
_BODY_COLUMNS = (
    "id",
    "source_schema",
    "source_table",
    "source_id",
    "source_butler",
    "tenant_id",
    "entity_id",
    "summary",
    "memory_type",
    "title",
    "predicate",
    "scope",
    "valid_at",
    "confidence",
    "importance",
    "retention_class",
    "sensitivity",
    "object_entity_id",
    "invalid_at",
    "updated_at",
)
_runtimes: dict[Any, CatalogCopyRuntime] = {}


@dataclass
class _ServerCopyScope:
    request: UUID
    loans: list[tuple[CatalogCopyRuntime, UUID, bytes, bool]]
    active: bool = True
    contexts: list[tuple[Any, UUID]] = field(default_factory=list)
    target: str | None = None
    questions: list[tuple[Any, UUID, bytes]] = field(default_factory=list)
    answers: list[tuple[Any, UUID]] = field(default_factory=list)


_server_copy_scope: ContextVar[_ServerCopyScope | None] = ContextVar(
    "native_catalog_server_copy", default=None
)

_server_copy_scopes: dict[UUID, _ServerCopyScope] = {}


class CatalogServerCopyLifetime:
    """Native ASGI completion settles only the actual server response copy."""

    def __init__(self, app: Any, *, butler_name: str | None = None) -> None:
        self.app = app
        self.butler_name = butler_name

    def __getattr__(self, name: str) -> Any:
        return getattr(self.app, name)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        copies = _ServerCopyScope(uuid4(), [], target=self.butler_name)
        _server_copy_scopes[copies.request] = copies
        token = _server_copy_scope.set(copies)
        final_body = False

        async def forward(message: dict) -> None:
            nonlocal final_body
            await send(message)
            if message.get("type") == "http.response.body" and not message.get("more_body", False):
                final_body = True

        try:
            await self.app(scope, receive, forward)
            if final_body:
                for runtime, loan, digest, source in copies.loans:
                    if source:
                        await runtime.finish_source_response(loan, digest, copies.request)
                    else:
                        await runtime.finish_server_copy(loan, digest, copies.request)
                from butlers.chronicler.location_delegation_receivers import finish_received_server

                for runtime, generation, digest in copies.questions:
                    await finish_received_server(runtime, generation, digest, copies.request)
                from butlers.chronicler.location_delegation_returns import finish_answer_server

                for runtime, generation in copies.answers:
                    await finish_answer_server(runtime, generation, copies.request)
                from butlers.chronicler.location_memory_context import finish_context_server

                for runtime, generation in copies.contexts:
                    await finish_context_server(runtime, generation, copies.request)
        finally:
            copies.active = False
            _server_copy_scopes.pop(copies.request, None)
            _server_copy_scope.reset(token)


def _body(row: Any) -> dict:
    from butlers.chronicler.location_projection import _digest_value

    return _digest_value({column: row[column] for column in _BODY_COLUMNS})


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate member")
        result[key] = value
    return result


async def _request_json(request: Any) -> dict:
    body = bytearray()
    frames = 0
    async with asyncio.timeout(5):
        async for chunk in request.stream():
            frames += 1
            if frames > 128 or len(chunk) > 8192 - len(body):
                raise ValueError("Oversized control body")
            body.extend(chunk)
    value = json.loads(body, object_pairs_hook=_unique_object)
    if not isinstance(value, dict):
        raise ValueError("Control object required")
    return value


@dataclass
class _Pending:
    catalog: UUID
    source: str
    deadline: float
    loan: UUID | None = None
    generation: UUID | None = None
    digest: bytes | None = None
    claimed: bool = False


@dataclass
class _AdmittedLoan:
    runtime: CatalogCopyRuntime
    token: str
    loan: UUID
    witness: dict
    active: bool = True


@dataclass
class _AdmittedRoute:
    token: str
    loan: UUID
    active: bool = True


_admitted_route: ContextVar[_AdmittedRoute | None] = ContextVar(
    "native_catalog_route", default=None
)


def catalog_transport_headers(target: str, tool: str, arguments: dict) -> dict[str, str]:
    """Switchboard infrastructure reads its own outer admission, never public args."""
    bound = _admitted_route.get()
    if bound is None:
        return {}
    if (
        not bound.active
        or target != "chronicler"
        or tool != "location_catalog_loan_body"
        or arguments.get("loan_id") != str(bound.loan)
        or set(arguments) - {"loan_id", "trace_context"}
    ):
        raise PolicyUnavailableError("Native catalog routed operation differs")
    return {_HEADER: bound.token}


_admitted_loan: ContextVar[_AdmittedLoan | None] = ContextVar("native_catalog_loan", default=None)


class CatalogLoanAdmission:
    """Strip the private capability and verify before MCP instrumentation.

    Generic requests are streamed untouched. Only the reserved private-header
    native transport is buffered, bounded and rejects duplicate JSON members.
    The registered tool itself refuses an absent private admission cell.
    """

    def __init__(self, app: Any, runtime: Any) -> None:
        self.app, self.runtime = app, runtime

    def __getattr__(self, name: str) -> Any:
        return getattr(self.app, name)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("path") == _PATH:
            # Fixed metadata controls have their own strict resource/parser
            # bounds. Only the actual MCP request supplies tool admission.
            await self.app(scope, receive, send)
            return
        headers = list(scope.get("headers", ()))
        tokens = [value for name, value in headers if name.lower() == _HEADER.lower().encode()]
        if not tokens:
            await self.app(scope, receive, send)
            return
        clean = {
            **scope,
            "headers": [
                (name, value) for name, value in headers if name.lower() != _HEADER.lower().encode()
            ],
        }
        # SSE's connection/initialize carries the transport header but no body
        # operation. It grants no context or enrollment.
        if scope.get("method") != "POST":
            await self.app(clean, receive, send)
            return
        admission = None
        routed = None
        try:
            if len(tokens) != 1 or not 32 <= len(tokens[0]) <= 128:
                raise ValueError
            capability = tokens[0].decode("ascii", "strict")
            body = bytearray()
            async with asyncio.timeout(5):
                for _ in range(128):
                    message = await receive()
                    if message.get("type") != "http.request":
                        raise ValueError
                    chunk = message.get("body", b"")
                    # Bound the owned buffer before copying the received
                    # chunk; checking afterwards transiently allocates the
                    # entire oversized private request. Generic requests keep
                    # their existing untouched streaming contract above.
                    if not isinstance(chunk, bytes) or len(chunk) > 262144 - len(body):
                        raise ValueError
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
                else:
                    raise ValueError
            payload = json.loads(body, object_pairs_hook=_unique_object)
            params = payload.get("params", {})
            if payload.get("method") == "tools/call":
                arguments = params.get("arguments", {})
                runtime = self.runtime()
                if runtime is None:
                    raise ValueError
                if params.get("name") == "location_catalog_loan_body":
                    if set(arguments) - {"loan_id", "trace_context"} or "loan_id" not in arguments:
                        raise ValueError
                    admission = await runtime.admit_loan(UUID(arguments["loan_id"]), capability)
                elif params.get("name") == "route":
                    if (
                        set(arguments) != {"target_butler", "tool_name", "args"}
                        or arguments["target_butler"] != "chronicler"
                        or arguments["tool_name"] != "location_catalog_loan_body"
                        or set(arguments["args"]) != {"loan_id"}
                    ):
                        raise ValueError
                    routed = await runtime.admit_route(
                        UUID(arguments["args"]["loan_id"]), capability
                    )
                else:
                    raise ValueError
            consumed = False

            async def replay() -> dict:
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

        except Exception:
            from starlette.responses import JSONResponse

            await JSONResponse({"status": "unavailable"}, status_code=503)(clean, receive, send)
            return
        token = _admitted_loan.set(admission)
        route_token = _admitted_route.set(routed)
        try:
            await self.app(clean, replay, send)
        finally:
            if admission is not None:
                admission.active = False
            _admitted_loan.reset(token)
            if routed is not None:
                routed.active = False
            _admitted_route.reset(route_token)


class CatalogCopyRuntime:
    """Constructor-owned Memory domain/writer and configured registry client."""

    def __init__(
        self,
        *,
        domain: Any,
        memory: Any,
        name: str,
        registry: Any,
        identity: tuple[str, str],
        memory_identity: tuple[str, str],
    ) -> None:
        import asyncpg

        if not isinstance(domain, asyncpg.Pool) or not isinstance(memory, asyncpg.Pool):
            raise PolicyUnavailableError("Catalog runtime pools are unavailable")
        if registry is None or not isinstance(name, str) or not name:
            raise PolicyUnavailableError("Catalog registry is unavailable")
        self.domain, self.memory, self.name, self.registry = domain, memory, name, registry
        self.identity = identity
        self.memory_identity = memory_identity
        self.incarnation = uuid4()
        self.pending: dict[str, _Pending] = {}
        self.active = True
        from butlers.chronicler.location_delegation_copies import NativeDelegationWriter
        from butlers.chronicler.location_memory_context import register_context_writer
        from butlers.core.delegation_source import register_writer

        self.delegation_writer = NativeDelegationWriter(self)
        register_writer(self.domain, self.delegation_writer)
        _runtimes[memory] = self
        register_context_writer(self)

    def close(self) -> None:
        from butlers.core.delegation_source import clear_writer

        clear_writer(self.domain, self.delegation_writer)
        self.delegation_writer.pending.clear()
        self.delegation_writer.answer_pending.clear()
        self.delegation_writer.receiving.clear()
        self.delegation_writer.receiving_answers.clear()
        self.active = False
        self.pending.clear()
        if _runtimes.get(self.memory) is self:
            del _runtimes[self.memory]

    async def lock_domain(self, conn: Any) -> None:
        if (
            await conn.fetchval("SELECT current_schema()"),
            await conn.fetchval("SELECT current_user"),
        ) != self.identity:
            raise PolicyUnavailableError("Native catalog domain writer differs")
        if self.name == "chronicler":
            from butlers.chronicler.storage import _lock_location_writes

            await _lock_location_writes(conn)
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
            "location:catalog-copy:" + self.name,
        )

    async def finish_server_copy(self, loan: UUID, digest: bytes, request: UUID) -> None:
        receipt = uuid4()
        async with self.domain.acquire() as conn:
            async with conn.transaction():
                await self.lock_domain(conn)
                matched = await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM location_catalog_copy_lifetimes "
                    "WHERE loan_id=$1 AND holder_kind='server_response' "
                    "AND holder_id=$2 AND body_digest=$3)",
                    loan,
                    request,
                    digest,
                )
                if matched is not True:
                    raise PolicyUnavailableError("Native server copy lifetime differs")
                await conn.execute(
                    "INSERT INTO location_catalog_copy_finished "
                    "(loan_id,body_digest,receipt_id) VALUES($1,$2,$3)",
                    loan,
                    digest,
                    receipt,
                )
        if (
            await self.domain.fetchval(
                "SELECT receipt_id FROM location_catalog_copy_finished "
                "WHERE loan_id=$1 AND body_digest=$2",
                loan,
                digest,
            )
            != receipt
        ):
            raise PolicyUnavailableError("Committed server disposition is unknown")

    async def finish_source_response(self, loan: UUID, digest: bytes, request: UUID) -> None:
        from butlers.chronicler.location_memory_copies import _lock, _receivers

        memory, schema, role = _receivers[self.domain]
        receipt = uuid4()
        async with memory.acquire() as conn:
            async with conn.transaction():
                await _lock(conn, schema, role)
                if not await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM chronicler.location_native_catalog_loans "
                    "WHERE loan_id=$1 AND body_digest=$2)",
                    loan,
                    digest,
                ):
                    raise PolicyUnavailableError("Native source response differs")
                await conn.execute(
                    "INSERT INTO chronicler.location_native_catalog_server_dispositions "
                    "(loan_id,body_digest,server_request,receipt_id) VALUES($1,$2,$3,$4)",
                    loan,
                    digest,
                    request,
                    receipt,
                )
        if (
            await self.domain.fetchval(
                "SELECT receipt_id FROM location_native_catalog_server_dispositions "
                "WHERE loan_id=$1 AND body_digest=$2",
                loan,
                digest,
            )
            != receipt
        ):
            raise PolicyUnavailableError("Committed source response is unknown")

    async def endpoint(self, name: str, *, control: bool = True) -> str:
        from butlers.chronicler.location_copy_transport import registered_endpoint

        return await registered_endpoint(self, name, control=control)

    async def exchange(self, endpoint: str, token: str, body: dict) -> dict:
        from butlers.chronicler.location_copy_transport import exchange_metadata

        return await exchange_metadata(self, endpoint, token, body)

    async def _challenge(self, token: str, body: dict) -> dict:
        pending = self.pending.get(token)
        if (
            pending is None
            or pending.deadline <= time.monotonic()
            or not self.active
            or body.get("catalog_id") != str(pending.catalog)
            or body.get("source") != pending.source
        ):
            raise PolicyUnavailableError("Native receiver challenge differs")
        # Even the initial challenge is served by the actual fixed owning
        # pool identity. A stored endpoint/claimed incarnation is insufficient.
        async with self.domain.acquire() as conn:
            async with conn.transaction():
                await self.lock_domain(conn)
        if body.get("phase") in {"committed", "forward"}:
            if pending.loan is None or pending.generation is None or pending.digest is None:
                raise PolicyUnavailableError("Native receiver is not committed")
            current = await self.domain.fetchval(
                "SELECT EXISTS(SELECT 1 FROM location_catalog_copy_loans "
                "WHERE loan_id=$1 AND source_generation=$2 AND body_digest=$3 "
                "AND receiving_incarnation=$4)",
                pending.loan,
                pending.generation,
                pending.digest,
                self.incarnation,
            )
            if current is not True:
                raise PolicyUnavailableError("Committed receiver binding is unknown")
            if pending.claimed:
                raise PolicyUnavailableError("Native receiver challenge was already claimed")
            if body["phase"] == "committed":
                pending.claimed = True
        elif body.get("phase") != "prepare":
            raise PolicyUnavailableError("Native receiver phase differs")
        return {
            "incarnation": str(self.incarnation),
            "catalog_id": str(pending.catalog),
            "source": pending.source,
            "phase": body["phase"],
            "loan_id": str(pending.loan) if pending.loan else None,
            "source_generation": str(pending.generation) if pending.generation else None,
            "body_digest": pending.digest.hex() if pending.digest else None,
        }

    async def routed_tool(self, target: str, tool: str, args: dict) -> dict:
        from butlers.chronicler.location_copy_transport import routed_owning_tool

        return await routed_owning_tool(self, target, tool, args)

    async def authorize_route(self, loan: UUID, token: str) -> dict:
        if self.name != "chronicler":
            raise PolicyUnavailableError("Native route source differs")
        async with self.domain.acquire() as conn:
            async with conn.transaction():
                await self.lock_domain(conn)
                stored = await conn.fetchrow(
                    "SELECT l.*,g.catalog_id FROM location_native_catalog_loans l "
                    "JOIN location_native_catalog_generations g USING(source_generation) "
                    "WHERE loan_id=$1",
                    loan,
                )
        if stored is None:
            raise PolicyUnavailableError("Native source loan is unavailable")
        witness = await self.exchange(
            await self.endpoint(stored["receiver_name"]),
            token,
            {
                "op": "challenge",
                "phase": "forward",
                "catalog_id": str(stored["catalog_id"]),
                "source": self.name,
            },
        )
        if (
            witness.get("loan_id") != str(loan)
            or witness.get("source_generation") != str(stored["source_generation"])
            or witness.get("body_digest") != stored["body_digest"].hex()
            or witness.get("incarnation") != str(stored["receiving_incarnation"])
            or witness.get("phase") != "forward"
        ):
            raise PolicyUnavailableError("Native forwarded receiver binding differs")
        return {"loan_id": str(loan), "source": self.name, "witness": witness}

    async def admit_route(self, loan: UUID, token: str) -> _AdmittedRoute:
        if self.name != "switchboard" or not self.active:
            raise PolicyUnavailableError("Native routing constructor differs")
        async with self.domain.acquire() as conn:
            async with conn.transaction():
                await self.lock_domain(conn)
        confirmed = await self.exchange(
            await self.endpoint("chronicler"),
            token,
            {
                "op": "authorize_route",
                "loan_id": str(loan),
            },
        )
        if confirmed.get("loan_id") != str(loan) or confirmed.get("source") != "chronicler":
            raise PolicyUnavailableError("Native routing source binding differs")
        return _AdmittedRoute(token, loan)

    async def admit_loan(self, loan: UUID, token: str) -> _AdmittedLoan:
        async with self.domain.acquire() as conn:
            async with conn.transaction():
                await self.lock_domain(conn)
                stored = await conn.fetchrow(
                    "SELECT l.*,g.catalog_id FROM location_native_catalog_loans l "
                    "JOIN location_native_catalog_generations g USING(source_generation) "
                    "WHERE loan_id=$1",
                    loan,
                )
        if self.name != "chronicler" or stored is None:
            raise PolicyUnavailableError("Native source loan is unavailable")
        witness = await self.exchange(
            await self.endpoint(stored["receiver_name"]),
            token,
            {
                "op": "challenge",
                "phase": "committed",
                "catalog_id": str(stored["catalog_id"]),
                "source": self.name,
            },
        )
        if (
            witness.get("loan_id") != str(loan)
            or witness.get("source_generation") != str(stored["source_generation"])
            or witness.get("body_digest") != stored["body_digest"].hex()
            or witness.get("incarnation") != str(stored["receiving_incarnation"])
            or witness.get("catalog_id") != str(stored["catalog_id"])
            or witness.get("source") != self.name
            or witness.get("phase") != "committed"
        ):
            raise PolicyUnavailableError("Native online receiver binding differs")
        return _AdmittedLoan(self, token, loan, witness)

    async def loan_body(self, loan: UUID) -> dict:
        admission = _admitted_loan.get()
        if (
            admission is None
            or not admission.active
            or admission.runtime is not self
            or admission.loan != loan
        ):
            raise PolicyUnavailableError("Native loan admission is unavailable")
        stored = await self.domain.fetchrow(
            "SELECT l.receiver_name,g.catalog_id FROM location_native_catalog_loans l "
            "JOIN location_native_catalog_generations g USING(source_generation) WHERE loan_id=$1",
            loan,
        )
        if stored is None:
            raise PolicyUnavailableError("Native loan source is unavailable")
        return await self._source(
            admission.token,
            {
                "catalog_id": str(stored["catalog_id"]),
                "receiver": stored["receiver_name"],
                "phase": "body",
            },
            admission=admission,
        )

    async def _source(
        self, token: str, body: dict, *, admission: _AdmittedLoan | None = None
    ) -> dict:
        from butlers.chronicler.location_memory_copies import _lock, _receivers

        if self.name != "chronicler" or self.domain not in _receivers:
            raise PolicyUnavailableError("Native catalog source is unavailable")
        memory, schema, role = _receivers[self.domain]
        if memory is not self.memory:
            raise PolicyUnavailableError("Native catalog source writer differs")
        catalog = UUID(body["catalog_id"])
        receiver = body["receiver"]
        endpoint = await self.endpoint(receiver)
        phase = body["phase"]
        if phase != "prepare" and (
            phase != "body"
            or admission is None
            or admission.runtime is not self
            or not admission.active
        ):
            raise PolicyUnavailableError("Native source phase differs")
        witness = (
            admission.witness
            if admission is not None
            else await self.exchange(
                endpoint,
                token,
                {
                    "op": "challenge",
                    "phase": "prepare" if phase == "prepare" else "committed",
                    "catalog_id": str(catalog),
                    "source": self.name,
                },
            )
        )
        if (
            witness.get("catalog_id") != str(catalog)
            or witness.get("source") != self.name
            or witness.get("phase") != ("prepare" if phase == "prepare" else "committed")
        ):
            raise PolicyUnavailableError("Native receiver witness differs")
        incarnation = UUID(witness["incarnation"])
        async with memory.acquire() as conn:
            async with conn.transaction():
                await _lock(conn, schema, role)
                catalog_row = await conn.fetchrow(
                    "SELECT * FROM public.memory_catalog WHERE id=$1 FOR UPDATE", catalog
                )
                head = await conn.fetchrow(
                    "SELECT g.* FROM chronicler.location_native_catalog_heads h "
                    "JOIN chronicler.location_native_catalog_generations g "
                    "USING(source_generation) "
                    "WHERE h.catalog_id=$1 FOR UPDATE OF h",
                    catalog,
                )
                if (
                    catalog_row is None
                    or head is None
                    or catalog_row["invalid_at"] is not None
                    or catalog_row["source_schema"] != schema
                    or content_digest({"catalog_body": _body(catalog_row)}) != head["body_digest"]
                ):
                    raise PolicyUnavailableError("Native catalog generation differs")
                artifact = await conn.fetchrow(
                    "SELECT a.*,b.exclusive_input "
                    "FROM chronicler.location_native_memory_artifacts a "
                    "JOIN chronicler.location_native_memory_bundles b USING(input_generation) "
                    "WHERE a.artifact_generation=$1",
                    head["artifact_generation"],
                )
                if artifact is None or await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM chronicler.location_native_copy_births b "
                    "JOIN chronicler.location_native_dispatch_parents i "
                    "USING(copy_generation,input_digest) "
                    "JOIN chronicler.location_retention_plan_outputs p "
                    "USING(output_kind,output_id) WHERE i.input_generation=$1)",
                    artifact["input_generation"],
                ):
                    raise PolicyUnavailableError("Native catalog input has been fenced")
                from butlers.chronicler.location_memory_mutations import (
                    current_artifact_body_matches,
                )

                # The catalog is a discovery projection, never proof that its
                # canonical source is unchanged. Use the fixed owning writer.
                table = artifact["memory_table"]
                if table not in {"facts", "rules"}:
                    raise PolicyUnavailableError("Native catalog source profile differs")
                canonical = await conn.fetchrow(
                    f"SELECT * FROM {table} WHERE id=$1 FOR UPDATE", artifact["artifact_id"]
                )
                if not await current_artifact_body_matches(conn, canonical, artifact):
                    raise PolicyUnavailableError("Native catalog canonical body changed")
                if phase == "prepare":
                    loan = uuid4()
                    await conn.execute(
                        "INSERT INTO chronicler.location_native_catalog_loans "
                        "(loan_id,source_generation,receiver_name,receiving_incarnation,"
                        "body_digest) "
                        "VALUES($1,$2,$3,$4,$5)",
                        loan,
                        head["source_generation"],
                        receiver,
                        incarnation,
                        head["body_digest"],
                    )
                else:
                    loan = UUID(witness["loan_id"])
                    stored = await conn.fetchrow(
                        "SELECT * FROM chronicler.location_native_catalog_loans WHERE loan_id=$1",
                        loan,
                    )
                    if (
                        stored is None
                        or stored["receiver_name"] != receiver
                        or stored["receiving_incarnation"] != incarnation
                        or str(stored["source_generation"]) != witness.get("source_generation")
                        or stored["body_digest"].hex() != witness.get("body_digest")
                        or stored["source_generation"] != head["source_generation"]
                    ):
                        raise PolicyUnavailableError("Native loan generation differs")
                result = {
                    "loan_id": str(loan),
                    "source_generation": str(head["source_generation"]),
                    "body_digest": head["body_digest"].hex(),
                    "receiver": receiver,
                    "incarnation": str(incarnation),
                }
                if phase == "body":
                    result["body"] = _body(catalog_row)
                    if len(json.dumps(result["body"], ensure_ascii=False).encode("utf-8")) > 262144:
                        raise PolicyUnavailableError("Native catalog body exceeds its profile")
        # A returned prepare ACK cannot substitute for this source readback.
        confirmed = await self.domain.fetchval(
            "SELECT EXISTS(SELECT 1 FROM location_native_catalog_loans WHERE loan_id=$1 "
            "AND source_generation=$2 AND body_digest=$3 AND receiving_incarnation=$4)",
            loan,
            head["source_generation"],
            head["body_digest"],
            incarnation,
        )
        if confirmed is not True:
            raise PolicyUnavailableError("Committed source loan is unknown")
        if phase == "body":
            scope = _server_copy_scope.get()
            if scope is None or not scope.active or len(scope.loans) >= _MAX_PENDING:
                raise PolicyUnavailableError("Native source response lifetime is unavailable")
            scope.loans.append((self, loan, head["body_digest"], True))
        return result

    async def control(self, request: Any) -> Any:
        from starlette.responses import JSONResponse

        try:
            token = request.headers.get(_HEADER, "")
            if not isinstance(token, str) or not 32 <= len(token) <= 128:
                raise ValueError
            body = await _request_json(request)
            if body.get("op") == "challenge" and set(body) == {
                "op",
                "phase",
                "catalog_id",
                "source",
            }:
                result = await self._challenge(token, body)
            elif body.get("op") == "source" and set(body) == {
                "op",
                "phase",
                "catalog_id",
                "receiver",
            }:
                if body.get("phase") != "prepare":
                    raise ValueError
                result = await self._source(token, body)
            elif body.get("op") == "authorize_route" and set(body) == {"op", "loan_id"}:
                result = await self.authorize_route(UUID(body["loan_id"]), token)
            elif body.get("op") == "question_challenge" and set(body) == {
                "op",
                "ledger_id",
                "source",
                "body_digest",
            }:
                from butlers.chronicler.location_delegation_receivers import question_challenge

                result = await question_challenge(self.delegation_writer, token, body)
            elif body.get("op") == "question_source" and set(body) == {
                "op",
                "ledger_id",
                "receiver",
            }:
                from butlers.chronicler.location_delegation_receivers import prepare_question_source

                result = await prepare_question_source(self.delegation_writer, token, body)
            elif body.get("op") == "question_delivery" and set(body) == {
                "op",
                "loan_id",
                "receiver",
            }:
                from butlers.chronicler.location_delegation_receivers import (
                    verify_question_delivery,
                )

                result = await verify_question_delivery(self.delegation_writer, token, body)
            elif body.get("op") in {"answer_challenge", "answer_source", "answer_delivery"}:
                from butlers.chronicler.location_delegation_returns import answer_control

                result = await answer_control(self.delegation_writer, token, body)
            else:
                raise ValueError
            return JSONResponse(result)
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)

    async def receive(self, catalog: UUID) -> dict:
        self.pending = {
            key: value for key, value in self.pending.items() if value.deadline > time.monotonic()
        }
        if not self.active or len(self.pending) >= _MAX_PENDING:
            raise PolicyUnavailableError("Native receiver capacity is unavailable")
        endpoint = await self.endpoint("chronicler")
        token = secrets.token_urlsafe(32)
        pending = _Pending(catalog, "chronicler", time.monotonic() + 30)
        self.pending[token] = pending
        try:
            prepared = await self.exchange(
                endpoint,
                token,
                {
                    "op": "source",
                    "phase": "prepare",
                    "catalog_id": str(catalog),
                    "receiver": self.name,
                },
            )
            loan, generation = UUID(prepared["loan_id"]), UUID(prepared["source_generation"])
            digest = bytes.fromhex(prepared["body_digest"])
            if (
                len(digest) != 32
                or prepared["receiver"] != self.name
                or prepared["incarnation"] != str(self.incarnation)
            ):
                raise PolicyUnavailableError("Native loan response differs")
            async with self.domain.acquire() as conn:
                async with conn.transaction():
                    await self.lock_domain(conn)
                    # This is the configured owning domain writer, not a
                    # receiving name/role supplied by a tool invocation.
                    await conn.execute(
                        "INSERT INTO location_catalog_copy_loans "
                        "(loan_id,source_generation,catalog_id,body_digest,receiving_incarnation) "
                        "VALUES($1,$2,$3,$4,$5)",
                        loan,
                        generation,
                        catalog,
                        digest,
                        self.incarnation,
                    )
            pending.loan, pending.generation, pending.digest = loan, generation, digest
            # Canonical body transfer uses the adopted registered MCP plane.
            # Only the outer source admission consumes this private header.
            from fastmcp import Client
            from fastmcp.client.transports import StreamableHttpTransport

            from butlers.connectors.mcp_client import CachedMCPClient

            mcp_endpoint = await self.endpoint("switchboard", control=False)
            registered = urlsplit(mcp_endpoint)
            # The registered owning daemon exposes both transports. Its fixed
            # native loan plane uses /mcp so admission stays on the actual HTTP
            # request task; no SSE query/session string supplies authority.
            native_endpoint = f"{registered.scheme}://{registered.netloc}/mcp"
            async with Client(
                StreamableHttpTransport(native_endpoint, headers={_HEADER: token}),
                name="native-location-catalog-holder",
            ) as client:
                delivered = CachedMCPClient._parse_result(
                    await client.call_tool(
                        "route",
                        {
                            "target_butler": "chronicler",
                            "tool_name": "location_catalog_loan_body",
                            "args": {"loan_id": str(loan)},
                        },
                    ),
                    "route",
                )
            if not isinstance(delivered, dict) or delivered.get("error"):
                raise PolicyUnavailableError("Native routed body is unavailable")
            delivered = delivered.get("result")
            if not isinstance(delivered, dict):
                raise PolicyUnavailableError("Native routed body differs")
            body = delivered.get("body")
            if (
                not isinstance(body, dict)
                or delivered.get("loan_id") != str(loan)
                or delivered.get("source_generation") != str(generation)
                or delivered.get("body_digest") != digest.hex()
                or content_digest({"catalog_body": body}) != digest
            ):
                raise PolicyUnavailableError("Native catalog body changed")
            # Bind a processing holder before the admitted bytes leave this
            # producer. Runtime session identity comes from the registered
            # guard cell, never a query/session/actor string.
            from butlers.chronicler.location_memory_context import current_runtime_context
            from butlers.chronicler.location_tool_copies import bind_tool_loan, current_tool_copy
            from butlers.core.copy_lifetime import _current_copy_invocation

            tool = current_tool_copy(self)
            context = current_runtime_context()
            invocation = _current_copy_invocation.get()
            scope = _server_copy_scope.get()
            holder_kind, holder_id = "unbound_processing", uuid4()
            if tool is not None:
                holder_id = tool.generation
            elif context is not None and context.runtime is self:
                holder_id = context.generation
            elif invocation is not None and invocation.target == self.name:
                holder_kind, holder_id = "runtime_session", UUID(invocation.runtime_session)
            elif scope is not None and scope.active:
                holder_kind, holder_id = "server_response", scope.request
            async with self.domain.acquire() as conn:
                async with conn.transaction():
                    await self.lock_domain(conn)
                    if holder_kind == "runtime_session" and not await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM sessions WHERE id=$1)", holder_id
                    ):
                        raise PolicyUnavailableError("Native receiving session is unavailable")
                    await conn.execute(
                        "INSERT INTO location_catalog_copy_lifetimes "
                        "(loan_id,holder_kind,holder_id,body_digest) VALUES($1,$2,$3,$4)",
                        loan,
                        holder_kind,
                        holder_id,
                        digest,
                    )
                    if tool is not None:
                        await bind_tool_loan(conn, tool, loan, digest)
            if (
                await self.domain.fetchval(
                    "SELECT holder_id FROM location_catalog_copy_lifetimes "
                    "WHERE loan_id=$1 AND body_digest=$2",
                    loan,
                    digest,
                )
                != holder_id
            ):
                raise PolicyUnavailableError("Committed receiving lifetime is unknown")
            if (
                tool is not None
                and await self.domain.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM location_runtime_tool_inputs "
                    "WHERE tool_generation=$1 AND loan_id=$2 AND body_digest=$3)",
                    tool.generation,
                    loan,
                    digest,
                )
                is not True
            ):
                raise PolicyUnavailableError("Committed native tool loan is unknown")
            if tool is None and context is not None and context.runtime is self:
                context.loans.append((loan, digest))
                context.local_rows.add(("catalog", catalog))
            if holder_kind == "server_response":
                if scope is None or not scope.active or len(scope.loans) >= _MAX_PENDING:
                    raise PolicyUnavailableError("Native server copy capacity is unavailable")
                scope.loans.append((self, loan, digest, False))
            return body
        finally:
            self.pending.pop(token, None)

    async def prepare_copy(self, decision: UUID) -> dict:
        plan = await self.routed_tool(
            "chronicler", "chronicler_location_retention_status", {"decision_id": str(decision)}
        )
        if UUID(plan["decision_id"]) != decision:
            raise PolicyUnavailableError("Stored source plan differs")
        manifest = bytes.fromhex(plan["manifest_digest"])
        if len(manifest) != 32:
            raise PolicyUnavailableError("Stored source manifest differs")
        loans = [
            loan for loan in plan.get("catalog_loans", ()) if loan["receiver_name"] == self.name
        ]
        if not loans:
            raise PolicyUnavailableError("Stored source loan cohort is unavailable")
        confirmed = []
        for source in loans:
            loan = UUID(source["loan_id"])
            input_generation = await self.domain.fetchval(
                "SELECT holder_id FROM location_catalog_copy_lifetimes "
                "WHERE loan_id=$1 AND holder_kind='unbound_processing'",
                loan,
            )
            if input_generation is not None:
                context_generation = await self.domain.fetchval(
                    "SELECT b.input_generation FROM location_runtime_tool_intents t "
                    "JOIN location_runtime_context_bindings b USING(receiving_session) "
                    "WHERE t.tool_generation=$1",
                    input_generation,
                )
                if context_generation is not None:
                    input_generation = context_generation
                from butlers.chronicler.location_memory_context import dispose_runtime_context

                await dispose_runtime_context(self, input_generation, plan)
            if source.get("complete_input") is not True:
                continue
            async with self.domain.acquire() as conn:
                async with conn.transaction():
                    await self.lock_domain(conn)
                    current = await conn.fetchrow(
                        "SELECT l.*,f.receipt_id AS finished_receipt "
                        "FROM location_catalog_copy_loans l "
                        "LEFT JOIN location_catalog_copy_finished f USING(loan_id,body_digest) "
                        "WHERE l.loan_id=$1 FOR UPDATE OF l",
                        loan,
                    )
                    if (
                        current is None
                        or current["finished_receipt"] is None
                        or str(current["source_generation"]) != source["source_generation"]
                        or current["body_digest"].hex() != source["body_digest"]
                        or str(current["receiving_incarnation"]) != source["receiving_incarnation"]
                    ):
                        continue
                    from butlers.chronicler.location_delegation_answers import loan_answers_closed

                    if not await loan_answers_closed(
                        conn, loan, current["body_digest"], decision, manifest
                    ):
                        continue
                    if await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM location_native_delegation_inputs q "
                        "JOIN location_native_delegation_parents p USING(question_generation) "
                        "WHERE p.parent_kind='catalog_loan' AND p.parent_generation=$1 "
                        "AND p.parent_digest=$2 AND NOT EXISTS("
                        "SELECT 1 FROM location_native_delegation_dispositions d "
                        "WHERE d.question_generation=q.question_generation AND d.decision_id=$3 "
                        "AND d.manifest_digest=$4 AND d.body_digest=q.body_digest))",
                        loan,
                        current["body_digest"],
                        decision,
                        manifest,
                    ):
                        continue
                    prior = await conn.fetchrow(
                        "SELECT * FROM location_catalog_copy_dispositions WHERE loan_id=$1", loan
                    )
                    if prior is None:
                        await conn.execute(
                            "INSERT INTO location_catalog_copy_dispositions "
                            "(loan_id,decision_id,manifest_digest,receipt_id) VALUES($1,$2,$3,$4)",
                            loan,
                            decision,
                            manifest,
                            uuid4(),
                        )
                    elif prior["decision_id"] != decision or prior["manifest_digest"] != manifest:
                        raise PolicyUnavailableError("Stored owning copy disposition differs")
            # An ACK or transaction context exit is not the durable receipt.
            readback = await self.domain.fetchrow(
                "SELECT * FROM location_catalog_copy_dispositions "
                "WHERE loan_id=$1 AND decision_id=$2 AND manifest_digest=$3",
                loan,
                decision,
                manifest,
            )
            if readback is None:
                raise PolicyUnavailableError("Committed owning copy disposition is unknown")
            confirmed.append(str(readback["receipt_id"]))
        return {
            "decision_id": str(decision),
            "confirmed": len(confirmed),
            "expected": len(loans),
            "receipt_ids": confirmed,
            "status": "complete" if len(confirmed) == len(loans) else "pending",
        }

    async def copy_status(self, decision: UUID, receipt: UUID) -> dict:
        async with self.domain.acquire() as conn:
            async with conn.transaction():
                await self.lock_domain(conn)
                row = await conn.fetchrow(
                    "SELECT d.*,l.source_generation,l.body_digest,l.receiving_incarnation "
                    "FROM location_catalog_copy_dispositions d JOIN location_catalog_copy_loans l "
                    "USING(loan_id) JOIN location_catalog_copy_finished f "
                    "USING(loan_id,body_digest) "
                    "WHERE d.decision_id=$1 AND d.receipt_id=$2",
                    decision,
                    receipt,
                )
        if row is None:
            raise PolicyUnavailableError("Owning catalog receipt is unavailable")
        return {
            key: row[key].hex() if isinstance(row[key], bytes) else str(row[key])
            for key in (
                "loan_id",
                "source_generation",
                "body_digest",
                "receiving_incarnation",
                "decision_id",
                "manifest_digest",
                "receipt_id",
            )
        }


async def native_catalog_rows(pool: Any, sql: str, *params: Any) -> list[dict]:
    """Source-free ranking first; full Chronicler body only after native loan."""
    import asyncpg

    runtime = _runtimes.get(pool)
    if runtime is None and not isinstance(pool, asyncpg.Pool):
        return [dict(row) for row in await pool.fetch(sql, *params)]
    # Metadata-only SELECT retains the actual SQL filters/ranking/limit.
    metadata_sql = sql.replace("SELECT *,", "SELECT id,source_schema,source_butler,")
    metadata = await pool.fetch(metadata_sql, *params)
    from butlers.chronicler.location_tool_copies import current_tool_copy

    tool = current_tool_copy(runtime) if runtime is not None else None
    if tool is not None:
        tool.read_observed = True
        if any(row["source_schema"] != "chronicler_mem" for row in metadata):
            tool.mixed_inputs = True
    results: list[dict] = []
    for row in metadata:
        values = dict(row)
        if values["source_schema"] == "chronicler_mem":
            if runtime is None:
                raise PolicyUnavailableError("Native catalog receiver is unavailable")
            body = await runtime.receive(values["id"])
            results.append({**body, **values})
        else:
            current = await pool.fetchrow(
                "SELECT * FROM public.memory_catalog WHERE id=$1", row["id"]
            )
            if current is not None:
                results.append({**dict(current), **values})
    return results


@asynccontextmanager
async def catalog_writer(pool: Any):
    from butlers.chronicler.location_memory_copies import _lock, _receivers

    runtime = _runtimes.get(pool)
    async with pool.acquire() as conn:
        async with conn.transaction():
            if runtime is not None:
                from butlers.chronicler.location_memory_context import (
                    _lock_memory_context,
                    _RuntimeContext,
                )

                await _lock_memory_context(
                    conn, _RuntimeContext(runtime, UUID(int=0), UUID(int=0), False)
                )
            if runtime is not None and runtime.name == "chronicler":
                memory, schema, role = _receivers[runtime.domain]
                if memory is not pool:
                    raise PolicyUnavailableError("Native catalog writer differs")
                await _lock(conn, schema, role)
            yield conn


async def bind_catalog(conn: Any, pool: Any, schema: str, table: str, artifact: UUID) -> None:
    runtime = _runtimes.get(pool)
    if runtime is None:
        return
    if schema != runtime.memory_identity[0]:
        raise PolicyUnavailableError("Native catalog configured source differs")
    own_schema = runtime.identity[0]
    context = await conn.fetchrow(
        f'SELECT a.artifact_generation FROM "{own_schema}".location_runtime_context_artifacts a '
        "WHERE memory_table=$1 AND artifact_id=$2",
        table,
        artifact,
    )
    if context is not None:
        if runtime.name != "chronicler" or not await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM chronicler.location_native_memory_artifacts "
            "WHERE artifact_generation=$1 AND memory_table=$2 AND artifact_id=$3)",
            context["artifact_generation"],
            table,
            artifact,
        ):
            raise PolicyUnavailableError("Native context catalog source is unavailable")
    if runtime.name != "chronicler":
        return
    row = await conn.fetchrow(
        "SELECT * FROM public.memory_catalog WHERE source_schema=$1 AND source_table=$2 "
        "AND source_id=$3 FOR UPDATE",
        schema,
        table,
        artifact,
    )
    binding = await conn.fetchrow(
        "SELECT * FROM chronicler.location_native_memory_artifacts "
        "WHERE memory_table=$1 AND artifact_id=$2",
        table,
        artifact,
    )
    if binding is None:
        # An ordinary independent fact has no fabricated location parent. A
        # legacy catalog row stays unclassified and cannot complete frontier.
        return
    if row is None:
        raise PolicyUnavailableError("Native catalog persisted body is unavailable")
    if await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM chronicler.location_native_copy_births b "
        "JOIN chronicler.location_native_dispatch_parents i "
        "USING(copy_generation,input_digest) "
        "JOIN chronicler.location_retention_plan_outputs p USING(output_kind,output_id) "
        "WHERE i.input_generation=$1)",
        binding["input_generation"],
    ):
        raise PolicyUnavailableError("Native catalog source has been fenced")
    source_generation, digest = uuid4(), content_digest({"catalog_body": _body(row)})
    await conn.execute(
        "INSERT INTO chronicler.location_native_catalog_generations "
        "(source_generation,catalog_id,artifact_generation,body_digest) VALUES($1,$2,$3,$4)",
        source_generation,
        row["id"],
        binding["artifact_generation"],
        digest,
    )
    await conn.execute(
        "INSERT INTO chronicler.location_native_catalog_heads(catalog_id,source_generation) "
        "VALUES($1,$2) ON CONFLICT(catalog_id) DO UPDATE SET "
        "source_generation=EXCLUDED.source_generation",
        row["id"],
        source_generation,
    )


async def reconcile_catalog_loans(domain: Any, decision: UUID) -> None:
    """Source-owned registry selection; actual owning MCP receipts, no peer SQL."""
    from butlers.chronicler.location_memory_copies import _lock, _receivers
    from butlers.chronicler.location_retention import plan_status

    configured = _receivers.get(domain)
    runtime = _runtimes.get(configured[0]) if configured else None
    if runtime is None or runtime.name != "chronicler":
        return  # Existing loan rows hold the frontier when registration failed.
    plan = await plan_status(domain, decision)
    expected = {loan["loan_id"]: loan for loan in plan["catalog_loans"]}
    for name in sorted({loan["receiver_name"] for loan in expected.values()}):
        prepared = await runtime.routed_tool(
            name, "location_retention_prepare_copy", {"decision_id": str(decision)}
        )
        for receipt in prepared.get("receipt_ids", ()):
            result = await runtime.routed_tool(
                name,
                "location_retention_copy_status",
                {
                    "decision_id": str(decision),
                    "receipt_id": receipt,
                },
            )
            loan = expected.get(result.get("loan_id"))
            if (
                loan is None
                or loan["receiver_name"] != name
                or loan["complete_input"] is not True
                or result.get("decision_id") != str(decision)
                or result.get("manifest_digest") != plan["manifest_digest"]
                or any(
                    result.get(key) != loan[key]
                    for key in ("source_generation", "body_digest", "receiving_incarnation")
                )
            ):
                raise PolicyUnavailableError("Owning catalog receipt binding differs")
            memory, schema, role = configured
            async with memory.acquire() as conn:
                async with conn.transaction():
                    await _lock(conn, schema, role)
                    current = await conn.fetchrow(
                        "SELECT * FROM chronicler.location_native_catalog_loans "
                        "WHERE loan_id=$1 FOR UPDATE",
                        UUID(loan["loan_id"]),
                    )
                    source_finished = await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM "
                        "chronicler.location_native_catalog_server_dispositions "
                        "WHERE loan_id=$1 AND body_digest=$2)",
                        UUID(loan["loan_id"]),
                        bytes.fromhex(loan["body_digest"]),
                    )
                    if (
                        current is None
                        or source_finished is not True
                        or str(current["source_generation"]) != loan["source_generation"]
                        or current["body_digest"].hex() != loan["body_digest"]
                    ):
                        raise PolicyUnavailableError("Source loan readback differs")
                    await conn.execute(
                        "INSERT INTO chronicler.location_retention_holder_receipts "
                        "(decision_id,owning_butler,holder_kind,holder_generation,"
                        "source_digest,receipt_id) VALUES($1,$2,'catalog_consumer',$3,$4,$5) "
                        "ON CONFLICT DO NOTHING",
                        decision,
                        name,
                        UUID(loan["loan_id"]),
                        bytes.fromhex(loan["body_digest"]),
                        UUID(result["receipt_id"]),
                    )
            confirmed = await domain.fetchval(
                "SELECT receipt_id FROM location_retention_holder_receipts "
                "WHERE decision_id=$1 AND owning_butler=$2 "
                "AND holder_kind='catalog_consumer' AND holder_generation=$3 "
                "AND source_digest=$4",
                decision,
                name,
                UUID(loan["loan_id"]),
                bytes.fromhex(loan["body_digest"]),
            )
            if confirmed != UUID(result["receipt_id"]):
                raise PolicyUnavailableError("Committed catalog receipt is unknown")


async def catalog_holder_inventory(conn: Any, decision: UUID) -> list[Any]:
    """Census actual immutable generations, with their own terminal readbacks.

    Include historic loans too: replacing a catalog head cannot dispose a
    previous receiver. A NULL receipt is an unfinished holder, never absence.
    """
    return await conn.fetch(
        "WITH artifacts AS (SELECT DISTINCT a.* FROM location_native_memory_artifacts a "
        "JOIN location_native_dispatch_parents i USING(input_generation) "
        "JOIN location_native_copy_births b USING(copy_generation,input_digest) "
        "JOIN location_retention_plan_outputs p USING(output_kind,output_id) "
        "WHERE p.decision_id=$1), generations AS ("
        "SELECT g.* FROM location_native_catalog_generations g JOIN artifacts a "
        "USING(artifact_generation)) "
        "SELECT 'chronicler'::text AS owning_butler,'memory_artifact'::text AS holder_kind,"
        "a.artifact_generation AS holder_generation,a.body_digest AS source_digest,d.receipt_id "
        "FROM artifacts a LEFT JOIN location_native_memory_artifact_dispositions d "
        "ON d.artifact_generation=a.artifact_generation AND d.decision_id=$1 "
        "AND d.body_digest=a.body_digest UNION ALL "
        "SELECT 'chronicler','catalog_source',g.source_generation,g.body_digest,d.receipt_id "
        "FROM generations g LEFT JOIN location_native_catalog_dispositions d "
        "ON d.source_generation=g.source_generation AND d.decision_id=$1 UNION ALL "
        "SELECT l.receiver_name,'catalog_consumer',l.loan_id,l.body_digest,r.receipt_id "
        "FROM location_native_catalog_loans l JOIN generations g USING(source_generation) "
        "LEFT JOIN location_retention_holder_receipts r "
        "ON r.decision_id=$1 AND r.owning_butler=l.receiver_name "
        "AND r.holder_kind='catalog_consumer' AND r.holder_generation=l.loan_id "
        "AND r.source_digest=l.body_digest UNION ALL "
        "SELECT DISTINCT 'chronicler','native_processing',c.claim_id,c.bundle_digest,f.receipt_id "
        "FROM location_native_processing_claims c "
        "JOIN location_native_processing_parents n USING(claim_id) "
        "JOIN location_native_copy_births b USING(copy_generation,input_digest) "
        "JOIN location_retention_plan_outputs p USING(output_kind,output_id) "
        "LEFT JOIN location_native_processing_finished f "
        "ON f.claim_id=c.claim_id AND f.bundle_digest=c.bundle_digest "
        "WHERE p.decision_id=$1 UNION "
        "SELECT DISTINCT 'chronicler','native_runtime_context',i.input_generation,"
        "COALESCE(c.bundle_digest,b.input_digest),x.receipt_id "
        "FROM location_runtime_context_intents i "
        "JOIN location_native_copy_births b USING(receiving_session) "
        "JOIN location_retention_plan_outputs p USING(output_kind,output_id) "
        "LEFT JOIN location_runtime_context_bindings c USING(input_generation) "
        "LEFT JOIN location_runtime_context_dispositions x "
        "ON x.input_generation=i.input_generation AND x.decision_id=p.decision_id "
        "WHERE p.decision_id=$1 ORDER BY owning_butler,holder_kind,holder_generation",
        decision,
    )


async def catalog_frontier_closed(conn: Any, decision: UUID) -> bool:
    """Actual source catalog/loan census; no absence-only terminal witness."""
    if await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM public.memory_catalog c "
        "WHERE c.source_schema='chronicler_mem' AND (c.summary<>'' OR c.title IS NOT NULL "
        "OR c.embedding IS NOT NULL OR c.search_vector IS NOT NULL) AND NOT EXISTS("
        "SELECT 1 FROM location_native_catalog_heads h WHERE h.catalog_id=c.id))"
    ):
        return False  # Opaque old catalog bodies have no fabricated lineage.
    if await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM location_native_catalog_generations g "
        "JOIN location_native_memory_artifacts a USING(artifact_generation) "
        "JOIN location_native_dispatch_parents i USING(input_generation) "
        "JOIN location_native_copy_births b USING(copy_generation,input_digest) "
        "JOIN location_retention_plan_outputs p USING(output_kind,output_id) "
        "WHERE p.decision_id=$1 AND NOT EXISTS("
        "SELECT 1 FROM location_native_catalog_dispositions d "
        "WHERE d.source_generation=g.source_generation AND d.decision_id=$1))",
        decision,
    ):
        return False
    return not await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM location_native_memory_artifacts a "
        "JOIN location_native_dispatch_parents i USING(input_generation) "
        "JOIN location_native_copy_births b USING(copy_generation,input_digest) "
        "JOIN location_retention_plan_outputs p USING(output_kind,output_id) "
        "WHERE p.decision_id=$1 AND NOT EXISTS("
        "SELECT 1 FROM location_native_memory_artifact_dispositions d "
        "WHERE d.artifact_generation=a.artifact_generation AND d.decision_id=$1))",
        decision,
    )


async def dispose_catalog_artifacts(domain: Any, decision: UUID) -> None:
    """Fixed actual Memory writer disposes unchanged exclusive native artifacts.

    Public catalog UPDATE is the existing owning permission. It clears every
    stored discovery body/vector/ref; it does not need a new DELETE grant.
    Mixed inputs, unrelated canonical versions and unclosed loan holders stay.
    """
    from butlers.chronicler.location_memory_copies import _lock, _receivers
    from butlers.chronicler.location_memory_mutations import current_artifact_body_matches

    configured = _receivers.get(domain)
    if configured is None:
        return
    memory, schema, role = configured
    candidates = await domain.fetch(
        "SELECT DISTINCT a.* FROM location_native_memory_artifacts a "
        "JOIN location_native_memory_bundles m USING(input_generation) "
        "JOIN location_native_dispatch_parents i USING(input_generation) "
        "JOIN location_native_copy_births b USING(copy_generation,input_digest) "
        "JOIN location_retention_plan_outputs p USING(output_kind,output_id) "
        "WHERE p.decision_id=$1 AND m.exclusive_input AND NOT EXISTS("
        "SELECT 1 FROM location_native_memory_artifact_dispositions d "
        "WHERE d.artifact_generation=a.artifact_generation) "
        "ORDER BY a.artifact_generation LIMIT 32",
        decision,
    )
    for artifact in candidates:
        receipt = uuid4()
        table = artifact["memory_table"]
        if table not in {"facts", "rules"}:
            raise PolicyUnavailableError("Native artifact profile differs")
        async with memory.acquire() as conn:
            async with conn.transaction():
                await _lock(conn, schema, role)
                if await conn.fetchval(
                    "SELECT NOT EXISTS(SELECT 1 FROM chronicler.location_native_dispatch_parents "
                    "WHERE input_generation=$1) OR EXISTS("
                    "SELECT 1 FROM chronicler.location_native_dispatch_parents i "
                    "LEFT JOIN chronicler.location_native_copy_births b "
                    "USING(copy_generation,input_digest) WHERE i.input_generation=$1 AND "
                    "(b.copy_generation IS NULL OR NOT b.lineage_known OR NOT b.exclusive_input "
                    "OR NOT EXISTS(SELECT 1 FROM chronicler.location_retention_plan_outputs p "
                    "WHERE p.decision_id=$2 AND p.output_kind=b.output_kind "
                    "AND p.output_id=b.output_id)))",
                    artifact["input_generation"],
                    decision,
                ):
                    continue
                context = await conn.fetchrow(
                    "SELECT a.input_generation FROM chronicler.location_runtime_context_artifacts "
                    "a "
                    "WHERE a.artifact_generation=$1",
                    artifact["artifact_generation"],
                )
                if context is not None and not await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM chronicler.location_runtime_context_ended "
                    "WHERE input_generation=$1)",
                    context["input_generation"],
                ):
                    continue
                if await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM chronicler.location_native_processing_parents p "
                    "JOIN chronicler.location_native_processing_claims c USING(claim_id) "
                    "JOIN chronicler.location_native_dispatch_parents i "
                    "USING(copy_generation,input_digest) WHERE i.input_generation=$1 "
                    "AND NOT EXISTS(SELECT 1 FROM chronicler.location_native_processing_finished f "
                    "WHERE f.claim_id=c.claim_id))",
                    artifact["input_generation"],
                ):
                    continue
                generations = await conn.fetch(
                    "SELECT * FROM chronicler.location_native_catalog_generations "
                    "WHERE artifact_generation=$1 ORDER BY source_generation",
                    artifact["artifact_generation"],
                )
                if await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM chronicler.location_native_catalog_loans l "
                    "JOIN chronicler.location_native_catalog_generations g "
                    "USING(source_generation) "
                    "WHERE g.artifact_generation=$1 AND (NOT EXISTS("
                    "SELECT 1 FROM chronicler.location_native_catalog_server_dispositions s "
                    "WHERE s.loan_id=l.loan_id AND s.body_digest=l.body_digest) OR NOT EXISTS("
                    "SELECT 1 FROM chronicler.location_retention_holder_receipts r "
                    "WHERE r.decision_id=$2 AND r.owning_butler=l.receiver_name "
                    "AND r.holder_kind='catalog_consumer' AND r.holder_generation=l.loan_id "
                    "AND r.source_digest=l.body_digest)))",
                    artifact["artifact_generation"],
                    decision,
                ):
                    continue
                canonical = await conn.fetchrow(
                    f"SELECT * FROM {table} WHERE id=$1 FOR UPDATE", artifact["artifact_id"]
                )
                if not await current_artifact_body_matches(conn, canonical, artifact):
                    continue
                if await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM memory_links WHERE source_id=$1 OR target_id=$1) "
                    + (
                        "OR EXISTS(SELECT 1 FROM facts WHERE supersedes_id=$1)"
                        if table == "facts"
                        else ""
                    ),
                    artifact["artifact_id"],
                ):
                    continue  # Independently born/linked descendants retain their actual source.
                catalog = await conn.fetchrow(
                    "SELECT c.*,g.body_digest FROM public.memory_catalog c "
                    "JOIN chronicler.location_native_catalog_heads h ON h.catalog_id=c.id "
                    "JOIN chronicler.location_native_catalog_generations g "
                    "USING(source_generation) "
                    "WHERE c.source_schema=$1 AND c.source_table=$2 AND c.source_id=$3 "
                    "FOR UPDATE OF c,h",
                    schema,
                    table,
                    artifact["artifact_id"],
                )
                if generations and (
                    catalog is None
                    or content_digest({"catalog_body": _body(catalog)}) != catalog["body_digest"]
                ):
                    continue
                if catalog is not None:
                    await conn.execute(
                        "UPDATE public.memory_catalog SET summary='',title=NULL,predicate=NULL,"
                        "scope=NULL,valid_at=NULL,embedding=NULL,search_vector=NULL,entity_id=NULL,"
                        "object_entity_id=NULL,confidence=0,importance=NULL,invalid_at=clock_timestamp(),"
                        "updated_at=clock_timestamp() WHERE id=$1",
                        catalog["id"],
                    )
                if table == "facts":
                    from butlers.core import entity_graph_edges

                    await entity_graph_edges.delete_entity_graph_edge(
                        conn,
                        source_schema=schema,
                        source_table="facts",
                        source_id=artifact["artifact_id"],
                    )
                removed = await conn.fetchval(
                    f"DELETE FROM {table} WHERE id=$1 RETURNING id", artifact["artifact_id"]
                )
                if removed != artifact["artifact_id"]:
                    raise PolicyUnavailableError("Native artifact disposition differs")
                await conn.execute(
                    "INSERT INTO chronicler.location_native_memory_artifact_dispositions "
                    "(artifact_generation,decision_id,body_digest,receipt_id) VALUES($1,$2,$3,$4)",
                    artifact["artifact_generation"],
                    decision,
                    artifact["body_digest"],
                    receipt,
                )
                for generation in generations:
                    await conn.execute(
                        "INSERT INTO chronicler.location_native_catalog_dispositions "
                        "(source_generation,decision_id,receipt_id) VALUES($1,$2,$3)",
                        generation["source_generation"],
                        decision,
                        uuid4(),
                    )
        if await domain.fetchval(
            "SELECT receipt_id FROM location_native_memory_artifact_dispositions "
            "WHERE artifact_generation=$1 AND decision_id=$2 AND body_digest=$3",
            artifact["artifact_generation"],
            decision,
            artifact["body_digest"],
        ) != receipt or await memory.fetchval(
            f"SELECT EXISTS(SELECT 1 FROM {table} WHERE id=$1)", artifact["artifact_id"]
        ):
            raise PolicyUnavailableError("Committed artifact disposition is unknown")


async def backfill_catalog_rows(
    pool: Any, sql: str, schema: str, limit: int, excluded: list
) -> int:
    """Backfill's actual emitted IDs bind on the same registered owning writer.

    Ordinary unconfigured maintenance keeps its old count query. Registered
    writers never publish context-derived bodies via the wholesale SQL bypass;
    their canonical constructor selects schema and every inserted row must
    have the same native generation/body binding as a live catalog write.
    """
    runtime = _runtimes.get(pool)
    if runtime is None:
        return int(await pool.fetchval(sql, schema, limit, excluded) or 0)
    if schema != runtime.memory_identity[0]:
        raise PolicyUnavailableError("Native backfill configured source differs")
    shaped = sql.replace("RETURNING 1", "RETURNING source_id").replace(
        "SELECT COUNT(*) FROM inserted", "SELECT source_id FROM inserted"
    )
    if shaped == sql or "SELECT COUNT(*) FROM inserted" in shaped:
        raise PolicyUnavailableError("Native backfill producer shape differs")
    table = "facts" if "FROM facts f" in sql else "rules" if "FROM rules r" in sql else None
    if table is None:
        raise PolicyUnavailableError("Native backfill producer profile differs")
    async with catalog_writer(pool) as conn:
        rows = await conn.fetch(shaped, schema, limit, excluded)
        for row in rows:
            await bind_catalog(conn, pool, schema, table, row["source_id"])
    return len(rows)
