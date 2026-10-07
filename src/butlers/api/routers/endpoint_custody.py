"""Protected custody command door with prepare-before-effect recovery IDs.

All authority comes from pre-body owner middleware and the installed engine.
The private producer and prepared sources are lifecycle allocations, never
request fields. A lost commit acknowledgement is read using the SAME ID;
commit is never automatically resent, including after a process restart.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from butlers.api.owner_auth.context import (
    CUSTODY_COMMAND_PREFIX,
    OwnerCustodyProof,
    owner_custody_proof,
)
from butlers.core.custody_browser import CustodyBrowserProducer
from butlers.core.custody_control import PreparedCustodyCommand
from butlers.core.custody_source import CustodyError, canonical_json, canonical_uuid, closed_object

router = APIRouter(prefix=CUSTODY_COMMAND_PREFIX, tags=["endpoint-custody"])
_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


@dataclass(repr=False)
class _Pending:
    source: PreparedCustodyCommand
    proof: OwnerCustodyProof
    deadline: float
    started: bool = False


class CustodyBrowserDoor:
    """Fixed owning producer; bounded private cache is not persisted authority."""

    def __init__(self, producer: CustodyBrowserProducer) -> None:
        self._producer = producer
        self._pending: dict[uuid.UUID, _Pending] = {}
        self._prepare_lock = asyncio.Lock()

    async def prepare(self, operation: str, selection: dict) -> str:
        proof = owner_custody_proof.get()
        if type(proof) is not OwnerCustodyProof:
            raise CustodyError("refused")
        async with self._prepare_lock:
            now = asyncio.get_running_loop().time()
            self._pending = {
                key: value for key, value in self._pending.items() if value.deadline > now
            }
            if len(self._pending) >= 128:
                raise CustodyError("unavailable")
            source = await self._producer.prepare(operation, selection)
            # Before any remote effect, return only the durable original ID
            # and retain this exact request proof in the live incarnation.
            self._pending[source.command_id] = _Pending(source, proof, now + 30)
            return str(source.command_id)

    async def commit(self, command_id: uuid.UUID) -> dict:
        pending = self._pending.get(command_id)
        if pending is None or pending.deadline <= asyncio.get_running_loop().time():
            raise CustodyError("unknown")
        # The command's frozen original request proof must be the proof at
        # this execution door too. A new cookie/CSRF can read the old result,
        # but cannot lend its pre-body verdict to another prepared command.
        if owner_custody_proof.get() != pending.proof:
            raise CustodyError("refused")
        if pending.started:
            raise CustodyError("unknown")
        pending.started = True
        try:
            async with asyncio.timeout(15):
                return await self._producer.execute(pending.source)
        except TimeoutError:
            raise CustodyError("unknown") from None

    async def result(self, command_id: uuid.UUID) -> dict:
        # A fresh CURRENT browser read ticket, not cached mutation permission.
        # This also works after API restart, expiry or an unknown commit ACK.
        source = await self._producer.prepare(
            "eligibility",
            {
                "target_set": [],
                "target_set_version": 1,
                "result_command_id": str(command_id),
            },
        )
        try:
            async with asyncio.timeout(15):
                return await self._producer.execute(source)
        except TimeoutError:
            raise CustodyError("unknown") from None

    def close(self) -> None:
        self._pending.clear()


def _door(request: Request) -> CustodyBrowserDoor:
    # The object is supplied by actual API startup. A principal string, request
    # actor, query/source locator or cached DTO cannot fill this allocation.
    if owner_custody_proof.get() is None:
        raise CustodyError("refused")
    door = getattr(request.app.state, "custody_browser_door", None)
    if type(door) is not CustodyBrowserDoor:
        raise CustodyError("unavailable")
    return door


async def _body(request: Request) -> dict:
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise CustodyError("invalid")
            value[key] = item
        return value

    if (
        request.query_params
        or request.headers.get("content-type", "").split(";", 1)[0] != "application/json"
    ):
        raise CustodyError("invalid")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 8192:
            raise CustodyError("invalid")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
        closed_object(value, required={"operation", "selection"})
        canonical_json(value, maximum=8192)
        if type(value["selection"]) is not dict or value["operation"] not in {
            "hold",
            "release",
            "replaced",
            "revoke_sessions",
        }:
            raise CustodyError("invalid")
        return value
    except (ValueError, UnicodeError, RecursionError, TypeError):
        raise CustodyError("invalid") from None


async def _empty_body(request: Request) -> None:
    if request.query_params:
        raise CustodyError("invalid")
    async for chunk in request.stream():
        if chunk:
            raise CustodyError("invalid")


def _response(data: dict, status: int = 200) -> JSONResponse:
    return JSONResponse({"data": data}, status_code=status, headers=_HEADERS)


def _error(error: CustodyError, command_id: str | None = None) -> JSONResponse:
    if error.code == "unknown" and command_id is not None:
        return _response({"command_id": command_id, "state": "unknown"}, 202)
    code = error.code if error.code in {"invalid", "refused", "conflict"} else "unavailable"
    status = {"invalid": 400, "refused": 403, "conflict": 409}.get(code, 503)
    return JSONResponse(
        {
            "error": {
                "code": "CUSTODY_" + code.upper(),
                "message": "Custody command " + code,
                "butler": None,
            }
        },
        status_code=status,
        headers=_HEADERS,
    )


@router.post("")
async def prepare_command(request: Request) -> JSONResponse:
    try:
        door = _door(request)
        body = await _body(request)
        command_id = await door.prepare(body["operation"], body["selection"])
        return _response({"command_id": command_id, "state": "prepared"}, 201)
    except CustodyError as exc:
        return _error(exc)


@router.post("/{command_id}/commit")
async def commit_command(command_id: str, request: Request) -> JSONResponse:
    try:
        door = _door(request)
        original = uuid.UUID(canonical_uuid(command_id))
        await _empty_body(request)
        return _response(await door.commit(original))
    except CustodyError as exc:
        return _error(exc, command_id)


@router.post("/{command_id}/result")
async def read_result(command_id: str, request: Request) -> JSONResponse:
    try:
        door = _door(request)
        original = uuid.UUID(canonical_uuid(command_id))
        await _empty_body(request)
        return _response(await door.result(original))
    except CustodyError as exc:
        return _error(exc, command_id)
