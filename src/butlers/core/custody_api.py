"""Restricted standalone API custody startup through a one-shot parent channel.

The child owns only the existing owner-auth pool and a matching restricted
anchor. Enrollment remains in the trusted CLI parent. The local inherited
socket is a bounded startup conduit, not a generic RPC, persistent signer or
hostile same-UID boundary. Raw uvicorn factory use has no parent channel and
cannot initialize custody authority.
"""

from __future__ import annotations

import asyncio
import os
import socket
import struct

import asyncpg
from fastmcp import Client
from opentelemetry.instrumentation.utils import suppress_instrumentation

from butlers.api.owner_auth.config import OwnerAuthConfig
from butlers.api.owner_auth.service import OwnerAuthService, _initialize_auth_connection
from butlers.core.custody_admission import CustodyAdmission, CustodyProfile
from butlers.core.custody_browser import CustodyBrowserProducer
from butlers.core.custody_source import CustodyError, canonical_json, closed_object, digest
from butlers.db import database_name_from_env, db_params_from_env

_PARENT_CHANNEL: socket.socket | None = None
_FRAME_LIMIT = 8192


def dashboard_profile(config: OwnerAuthConfig) -> CustodyProfile:
    return CustodyProfile(
        "dashboard",
        "dashboard_auth_api",
        ("owner_command",),
        ("eligibility", "hold", "release", "replaced", "revoke_sessions"),
        ("switchboard",),
        digest(
            {
                "version": "custody-source.v1",
                "actor": "dashboard",
                "role": "dashboard_auth_api",
                "origin": config.origin,
                "rp_id": config.rp_id,
                "key_generation": config.key_generation,
            }
        ),
    )


def install_api_parent_channel(channel: socket.socket) -> None:
    """Called once by the fixed child entrypoint before constructing the API."""
    global _PARENT_CHANNEL
    if _PARENT_CHANNEL is not None or channel.family != socket.AF_UNIX:
        raise CustodyError("refused")
    os.set_inheritable(channel.fileno(), False)
    channel.setblocking(False)
    _PARENT_CHANNEL = channel


def discard_api_parent_channel() -> None:
    """Close an unused/failed startup channel before any runtime child starts."""
    global _PARENT_CHANNEL
    channel, _PARENT_CHANNEL = _PARENT_CHANNEL, None
    if channel is not None:
        channel.close()


async def _exact(channel: socket.socket, size: int) -> bytes:
    result = bytearray()
    while len(result) < size:
        try:
            part = await asyncio.get_running_loop().sock_recv(channel, size - len(result))
        except OSError:
            raise CustodyError("unavailable") from None
        if not part:
            raise CustodyError("unavailable")
        result.extend(part)
    return bytes(result)


async def receive_startup_frame(channel: socket.socket) -> dict:
    """Bounded UTF-8 JSON; duplicate fields are refused before enrollment."""
    import json

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise CustodyError("invalid")
            result[key] = value
        return result

    size = struct.unpack("!I", await _exact(channel, 4))[0]
    if not 0 < size <= _FRAME_LIMIT:
        raise CustodyError("invalid")
    try:
        value = json.loads((await _exact(channel, size)).decode("utf-8"), object_pairs_hook=pairs)
        if type(value) is not dict:
            raise CustodyError("invalid")
        canonical_json(value, maximum=_FRAME_LIMIT)
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise CustodyError("invalid") from None


async def send_startup_frame(channel: socket.socket, value: dict) -> None:
    raw = canonical_json(value, maximum=_FRAME_LIMIT)
    try:
        await asyncio.get_running_loop().sock_sendall(channel, struct.pack("!I", len(raw)) + raw)
    except OSError:
        raise CustodyError("unavailable") from None


class CustodyApiRuntime:
    def __init__(self, admission: CustodyAdmission, producer: CustodyBrowserProducer) -> None:
        self.admission, self.producer = admission, producer

    async def stop(self) -> None:
        await self.admission.stop()


async def start_api_custody(service: OwnerAuthService, endpoint: str) -> CustodyApiRuntime:
    """Consumes the actual one-shot parent socket and creates its own anchor.

    No connecting host credential is borrowed from the API DB manager. This
    function neither installs a schema nor enrolls a caller-supplied process.
    """
    global _PARENT_CHANNEL
    channel, _PARENT_CHANNEL = _PARENT_CHANNEL, None
    if channel is None or service.pool is None:
        if channel is not None:
            channel.close()
        raise CustodyError("unavailable")
    anchor = admission = None
    try:
        # These are the same existing restricted login settings used by the
        # owner-auth service. Values flow only into asyncpg; none are retained
        # in manifests, startup frames, logging or public request objects.
        user = os.environ.get("DASHBOARD_AUTH_DB_USER")
        password = os.environ.get("DASHBOARD_AUTH_DB_PASSWORD")
        if not user or not password:
            raise CustodyError("unavailable")
        with suppress_instrumentation():
            anchor = await asyncpg.connect(
                **(db_params_from_env() | {"user": user, "password": password}),
                database=database_name_from_env("butlers"),
                timeout=5,
                command_timeout=5,
            )
            await _initialize_auth_connection(anchor)

        async def enroll(manifest: dict) -> dict:
            async with asyncio.timeout(10):
                await send_startup_frame(channel, manifest)
                response = await receive_startup_frame(channel)
                closed_object(response, required={"receipt"})
                if type(response["receipt"]) is not dict:
                    raise CustodyError("unavailable")
                return response["receipt"]

        admission = CustodyAdmission(
            dashboard_profile(service.config), service.pool, anchor, host_enroll=enroll
        )
        await admission.start()
        producer = CustodyBrowserProducer(admission, service, Client(endpoint))
        return CustodyApiRuntime(admission, producer)
    except BaseException:
        if admission is not None:
            await admission.stop()
        elif anchor is not None:
            await anchor.close()
        raise
    finally:
        channel.close()
