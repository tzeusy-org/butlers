"""Same-database response serialization with owning, content-blind MCP reads.

No peer SQL or shared write authority: each durable marker stays in its owner's
calendar_action_log. Coordination reserves capacity separate from work pools.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import tomllib
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from butlers.db import Database

# Historical owners remain members even when response execution is disabled.
# Adding a new enabled owner requires extending this inventory on every peer.
RESPONSE_FENCE_OWNERS = ("messenger", "relationship")
_PROTOCOL = "calendar-response-fence.v1"
_INVENTORY = hashlib.sha256(json.dumps(RESPONSE_FENCE_OWNERS).encode()).hexdigest()
_COORDINATION_TIMEOUT = 10.0
_HEX_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_LOCK_SQL = """
SELECT EXISTS (
    SELECT 1 FROM pg_locks
    WHERE locktype='advisory' AND granted AND mode='ExclusiveLock'
      AND database=(SELECT oid FROM pg_database WHERE datname=current_database())
      AND pid=$1 AND objsubid=1
      AND classid::bigint=(($2::bigint >> 32) & 4294967295)
      AND objid::bigint=($2::bigint & 4294967295)
)
"""

_MALFORMED_SQL = """
SELECT EXISTS(SELECT 1 FROM calendar_action_log
    WHERE action_type='workspace_user_respond' AND (
        jsonb_typeof(action_payload) IS DISTINCT FROM 'object'
        OR COALESCE(action_payload->>'phase','') NOT IN
            ('reserved','prepared','egress_started','uncertain','terminal')
        OR (action_payload->>'phase' IN ('egress_started','uncertain')
            AND COALESCE(action_payload->>'target_key','') !~ '^[0-9a-f]{64}$')))
"""


class ResponseFenceUnavailable(RuntimeError):
    """Pre-egress evidence is unavailable; the prepared command may be retried."""


def _lock_key(target: str) -> int:
    return int.from_bytes(bytes.fromhex(target)[:8], "big", signed=True)


class CalendarResponseFence:
    def __init__(self, module: Any) -> None:
        self.module = module
        self._client: Any = None
        self._client_getter: Callable[[], Any] | None = None
        self._roster = Path(__file__).resolve().parents[3] / "roster"
        self._database: Database | None = None
        self._pool: Any = None
        self._open_lock = asyncio.Lock()
        self._claim_slot = asyncio.Semaphore(1)
        self._closed = False

    def wire(
        self,
        repo_root: Path,
        client: Any,
        *,
        client_getter: Callable[[], Any] | None = None,
    ) -> None:
        self._roster = Path(repo_root) / "roster"
        self._client = client
        self._client_getter = client_getter

    def _current_client(self) -> Any:
        """Resolve the Switchboard client at use time.

        The daemon's heartbeat loop replaces ``switchboard_client`` on reconnect and
        it is ``None`` while the Switchboard is unreachable, so a startup snapshot
        would refuse every response until restart. An unresolved client keeps the
        fail-closed, retryable path.
        """
        if self._client_getter is None:
            return self._client
        try:
            return self._client_getter()
        except Exception:
            return None

    def _check_inventory(self) -> None:
        if self.module._butler_name not in RESPONSE_FENCE_OWNERS or self._closed:
            raise ResponseFenceUnavailable("response_fence_unavailable")
        files = tuple(self._roster.glob("*/butler.toml"))
        if not files:
            raise ResponseFenceUnavailable("response_fence_unavailable")
        present = set()
        for path in files:
            config = tomllib.loads(path.read_text())
            name = config["butler"]["name"]
            calendar = config.get("modules", {}).get("calendar")
            if name in RESPONSE_FENCE_OWNERS:
                if not isinstance(calendar, dict):
                    raise ResponseFenceUnavailable("response_fence_unavailable")
                present.add(name)
            if isinstance(calendar, dict):
                enabled = calendar.get("response_enabled", False)
                if type(enabled) is not bool:
                    raise ResponseFenceUnavailable("response_fence_unavailable")
                if enabled and name not in RESPONSE_FENCE_OWNERS:
                    raise ResponseFenceUnavailable("response_fence_unavailable")
        if present != set(RESPONSE_FENCE_OWNERS):
            raise ResponseFenceUnavailable("response_fence_unavailable")

    async def _get_pool(self) -> Any:
        async with self._open_lock:
            if self._closed:
                raise ResponseFenceUnavailable("response_fence_unavailable")
            if self._pool is None:
                source = self.module._db
                if not isinstance(source, Database) or source.role_enforcement_disabled:
                    raise ResponseFenceUnavailable("response_fence_unavailable")
                if source.schema != self.module._butler_name or source.role != (
                    f"butler_{self.module._butler_name}_rw"
                ):
                    raise ResponseFenceUnavailable("response_fence_unavailable")
                self._database = Database(
                    db_name=source.db_name,
                    schema=source.schema,
                    role=source.role,
                    host=source.host,
                    port=source.port,
                    user=source.user,
                    password=source.password,
                    ssl=source.ssl,
                    min_pool_size=0,
                    max_pool_size=2,
                )
                self._pool = await self._database.connect()
            return self._pool

    @asynccontextmanager
    async def claim(self, target: str):
        """Keep the global serializer until the caller commits its owning marker."""
        try:
            async with asyncio.timeout(_COORDINATION_TIMEOUT), self._claim_slot:
                self._check_inventory()
                if self._current_client() is None or not _HEX_DIGEST.fullmatch(target):
                    raise ResponseFenceUnavailable("response_fence_unavailable")
                pool = await self._get_pool()
                async with pool.acquire() as connection:
                    async with connection.transaction(isolation="read_committed"):
                        if not await connection.fetchval(
                            "SELECT pg_try_advisory_xact_lock($1)", _lock_key(target)
                        ):
                            raise ResponseFenceUnavailable("response_fence_busy")
                        nonce = uuid.uuid4().hex[:16]
                        nonce_key = int.from_bytes(bytes.fromhex(nonce), "big", signed=True)
                        if nonce_key == _lock_key(target) or not await connection.fetchval(
                            "SELECT pg_try_advisory_xact_lock($1)", nonce_key
                        ):
                            raise ResponseFenceUnavailable("response_fence_busy")
                        request = {
                            "protocol": _PROTOCOL,
                            "inventory": _INVENTORY,
                            "request_id": str(uuid.uuid4()),
                            "target_key": target,
                            "nonce": nonce,
                            "claimant_pid": connection.get_server_pid(),
                        }
                        if await connection.fetchval(_MALFORMED_SQL):
                            raise ResponseFenceUnavailable("response_fence_unavailable")
                        yield connection, request
        except TimeoutError:
            raise ResponseFenceUnavailable("response_fence_unavailable") from None

    async def check_peers(self, request: dict[str, Any]) -> None:
        client = self._current_client()
        if client is None:
            raise ResponseFenceUnavailable("response_fence_unavailable")
        for owner in RESPONSE_FENCE_OWNERS:
            if owner == self.module._butler_name:
                continue
            response = await client.call_tool(
                "route",
                {
                    "target_butler": owner,
                    "tool_name": "calendar_response_fence_status",
                    "args": {"request": request},
                    "source_butler": self.module._butler_name,
                },
            )
            raw = response.data
            if response.is_error or not isinstance(raw, dict) or raw.get("error"):
                raise ResponseFenceUnavailable("response_fence_unavailable")
            result = raw.get("result")
            if not isinstance(result, dict) or result != {**request, "status": "clear"}:
                raise ResponseFenceUnavailable("response_fence_unavailable")

    async def status(self, request: dict[str, Any]) -> dict[str, Any]:
        """Read only this owner's evidence; never wait on a claimant or invoke peers."""
        unavailable = {"status": "unavailable"}
        try:
            async with asyncio.timeout(_COORDINATION_TIMEOUT):
                self._check_inventory()
                if set(request) != {
                    "protocol",
                    "inventory",
                    "request_id",
                    "target_key",
                    "nonce",
                    "claimant_pid",
                }:
                    return unavailable
                if request["protocol"] != _PROTOCOL or request["inventory"] != _INVENTORY:
                    return unavailable
                target, nonce, pid = (
                    request["target_key"],
                    request["nonce"],
                    request["claimant_pid"],
                )
                if (
                    not isinstance(target, str)
                    or not _HEX_DIGEST.fullmatch(target)
                    or not isinstance(nonce, str)
                    or not re.fullmatch(r"[0-9a-f]{16}", nonce)
                    or type(pid) is not int
                    or pid <= 0
                    or str(uuid.UUID(request["request_id"])) != request["request_id"]
                ):
                    return unavailable
                pool = await self._get_pool()
                async with pool.acquire() as connection:
                    async with connection.transaction(isolation="read_committed", readonly=True):
                        for key in (
                            _lock_key(target),
                            int.from_bytes(bytes.fromhex(nonce), "big", signed=True),
                        ):
                            if not await connection.fetchval(_LOCK_SQL, pid, key):
                                return unavailable
                        if await connection.fetchval(_MALFORMED_SQL):
                            return unavailable
                        unresolved = await connection.fetchval(
                            "SELECT EXISTS(SELECT 1 FROM calendar_action_log "
                            "WHERE action_type='workspace_user_respond' "
                            "AND action_payload->>'phase' IN ('egress_started','uncertain') "
                            "AND action_payload->>'target_key'=$1)",
                            target,
                        )
                        if unresolved:
                            return {**request, "status": "unresolved"}
                        return {**request, "status": "clear"}
        except Exception:
            return unavailable

    async def close(self) -> None:
        self._closed = True
        async with self._open_lock:
            database = self._database
            if database is not None and database.pool is not None:
                pool = database.pool
                try:
                    async with asyncio.timeout(_COORDINATION_TIMEOUT):
                        await database.close()
                except TimeoutError:
                    pool.terminate()
            self._pool = None
