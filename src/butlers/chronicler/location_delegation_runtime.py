"""Core-only owning question runtime, independent of Memory configuration.

The daemon enrolls its actual domain pool before serving. This constructor
owns only question/session lifetimes. It never installs a Memory receiver,
borrows a peer pool, acquires a credential or declares catalog authority.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from butlers.chronicler.location_policy import PolicyUnavailableError


class NativeDelegationRuntime:
    @classmethod
    async def create(cls, *, domain: Any, name: str, schema: str, registry: Any):
        import asyncpg

        if not isinstance(domain, asyncpg.Pool):
            raise PolicyUnavailableError("Native question constructor is unavailable")
        async with domain.acquire() as conn:
            identity = (
                await conn.fetchval("SELECT current_schema()"),
                await conn.fetchval("SELECT current_user"),
            )
            if identity[0] != schema or not identity[1]:
                raise PolicyUnavailableError("Native question owning namespace differs")
        runtime = cls(domain=domain, name=name, registry=registry, identity=identity)
        # The exact identity is rechecked inside every domain transaction.
        from butlers.chronicler.location_memory_context import register_context_writer
        from butlers.core.delegation_source import register_writer

        register_writer(domain, runtime.delegation_writer)
        register_context_writer(runtime)
        return runtime

    def __init__(self, *, domain: Any, name: str, registry: Any, identity: tuple[str, str]):
        from butlers.chronicler.location_delegation_copies import NativeDelegationWriter

        self.domain, self.name, self.registry, self.identity = domain, name, registry, identity
        self.incarnation = uuid4()
        self.active = True
        self.delegation_writer = NativeDelegationWriter(self)

    async def lock_domain(self, conn: Any) -> None:
        if (
            not self.active
            or (
                await conn.fetchval("SELECT current_schema()"),
                await conn.fetchval("SELECT current_user"),
            )
            != self.identity
        ):
            raise PolicyUnavailableError("Native question owning writer differs")
        if self.name == "chronicler":
            from butlers.chronicler.storage import _lock_location_writes

            await _lock_location_writes(conn)
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
            "location:catalog-copy:" + self.name,
        )

    async def endpoint(self, name: str, *, control: bool = True) -> str:
        from butlers.chronicler.location_copy_transport import registered_endpoint

        return await registered_endpoint(self, name, control=control)

    async def exchange(self, endpoint: str, token: str, body: dict) -> dict:
        from butlers.chronicler.location_copy_transport import exchange_metadata

        return await exchange_metadata(self, endpoint, token, body)

    async def routed_tool(self, target: str, tool: str, args: dict) -> dict:
        from butlers.chronicler.location_copy_transport import routed_owning_tool

        return await routed_owning_tool(self, target, tool, args)

    async def control(self, request: Any) -> Any:
        from starlette.responses import JSONResponse

        from butlers.chronicler.location_catalog_copies import _HEADER, _request_json
        from butlers.chronicler.location_delegation_receivers import (
            prepare_question_source,
            question_challenge,
            verify_question_delivery,
        )

        handlers = {
            "question_challenge": (
                {"op", "ledger_id", "source", "body_digest"},
                question_challenge,
            ),
            "question_source": ({"op", "ledger_id", "receiver"}, prepare_question_source),
            "question_delivery": ({"op", "loan_id", "receiver"}, verify_question_delivery),
        }
        try:
            token = request.headers.get(_HEADER, "")
            if not self.active or not isinstance(token, str) or not 32 <= len(token) <= 128:
                raise ValueError
            body = await _request_json(request)
            if body.get("op") in {"answer_challenge", "answer_source", "answer_delivery"}:
                from butlers.chronicler.location_delegation_returns import answer_control

                return JSONResponse(await answer_control(self.delegation_writer, token, body))
            declared = handlers.get(body.get("op"))
            if declared is None or set(body) != declared[0]:
                raise ValueError
            return JSONResponse(await declared[1](self.delegation_writer, token, body))
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)

    def route(self):
        from starlette.routing import Route

        from butlers.chronicler.location_catalog_copies import _PATH

        return Route(_PATH, self.control, methods=["POST"])

    def close(self) -> None:
        from butlers.chronicler.location_memory_context import _context_writers
        from butlers.core.delegation_source import clear_writer

        self.active = False
        clear_writer(self.domain, self.delegation_writer)
        if _context_writers.get(self.domain) is self:
            del _context_writers[self.domain]
        self.delegation_writer.pending.clear()
        self.delegation_writer.answer_pending.clear()
        self.delegation_writer.receiving.clear()
        self.delegation_writer.receiving_answers.clear()
