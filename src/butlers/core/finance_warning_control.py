"""One fixed authenticated subscription-currentness read, held by real daemons.

A transport bearer is pending, not invocation authority. Only the registered
Finance closure can consume it. This is trusted internal composition, not a
fact, owner invocation, generic RPC grant or a replacement for route policy.
"""

from __future__ import annotations

import asyncio
import contextvars
import secrets
import sys
import weakref
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

import httpx

TOOL = "subscription_warning_still_relevant"
HEADER = "X-Butlers-Finance-Warning"
VERIFY_PATH = "/internal/finance-warning/verify"
DEADLINE_SECONDS = 5.0
_pending = contextvars.ContextVar("finance_warning_pending", default=None)
_owners: weakref.WeakValueDictionary[int, FinanceWarningControlOwner] = (
    weakref.WeakValueDictionary()
)


def warning_tuple(subject, selected_date, epoch):
    if type(subject) is not str or str(UUID(subject)) != subject:
        raise ValueError("invalid warning subject")
    if (
        type(selected_date) is not str
        or date.fromisoformat(selected_date).isoformat() != selected_date
    ):
        raise ValueError("invalid warning date")
    if type(epoch) is not int or not 0 <= epoch < 2**63:
        raise ValueError("invalid warning epoch")
    return UUID(subject), date.fromisoformat(selected_date), epoch


def canonical_args(args):
    if type(args) is not dict or set(args) != {"subject", "selected_date", "epoch"}:
        raise ValueError("invalid warning arguments")
    warning_tuple(**args)
    return dict(args)


def _local_identity(binding):
    """Boot and publication epochs belong to different counters."""
    boot, epoch, publisher, _publication_epoch, revision, generation, *_material = binding
    return boot, epoch, publisher, revision, generation


def _registration_binding(snapshot, domain):
    registration = snapshot.registration(domain)
    boot = snapshot.boot(domain)
    if (
        not isinstance(boot[0], UUID)
        or boot[0].version != 7
        or type(boot[1]) is not int
        or boot[1] <= 0
        or not isinstance(registration.instance_id, UUID)
        or not isinstance(registration.revision_id, UUID)
        or type(registration.epoch) is not int
        or registration.epoch <= 0
    ):
        raise PermissionError("finance_warning_identity_refused")
    return (
        str(boot[0]),
        boot[1],
        str(registration.instance_id),
        registration.epoch,
        str(registration.revision_id),
        registration.catalog.generation_digest,
        registration.body_digest,
        registration.authorization_digest,
    )


@dataclass(frozen=True)
class _CurrentRegistrations:
    catalogs: Any
    boots: dict

    def registration(self, domain):
        return self.catalogs.registration(domain)

    def boot(self, domain):
        return self.boots[domain]


def lookup_owner(pool):
    owner = _owners.get(id(pool))
    if owner is None or owner.pool is not pool or not owner.installed():
        return None
    return owner


@dataclass(frozen=True)
class _HeldCall:
    owner: Any
    token: str
    args: dict
    receiver: tuple

    def pool_operations(self, pool):
        # Keep the native pool as the authority identity. Only this already
        # admitted fixed call receives an execution view; it grants no SQL
        # surface to MCP arguments or to ordinary route callers.
        if lookup_owner(pool) is not self.owner or self.owner.call is not self:
            raise PermissionError("finance_warning_binding_refused")
        return _OwnedPoolOperations(self.owner)

    def client_context(self, client):
        return self.owner.client_context(client)

    def headers(self, pool, target, tool, args, decision):
        if (
            lookup_owner(pool) is not self.owner
            or self.owner.call is not self
            or target != "finance"
            or tool != TOOL
            or args != self.args
            or self.owner.closed
            or self.token not in self.owner.claims
            or (str(decision.boot_instance_id), decision.boot_epoch) != self.receiver[:2]
        ):
            raise PermissionError("finance_warning_binding_refused")
        return {HEADER: self.token}


class _OwnedPoolOperations:
    """Route DB phases settle native release without holding a lease over IO."""

    def __init__(self, owner):
        self._owner = owner

    def acquire(self):
        return self._owner.connection()

    async def _call(self, method, *args, **kwargs):
        async with self.acquire() as conn:
            return await self._owner._child(getattr(conn, method)(*args, **kwargs), cancel=True)

    async def execute(self, *args, **kwargs):
        return await self._call("execute", *args, **kwargs)

    async def fetch(self, *args, **kwargs):
        return await self._call("fetch", *args, **kwargs)

    async def fetchrow(self, *args, **kwargs):
        return await self._call("fetchrow", *args, **kwargs)

    async def fetchval(self, *args, **kwargs):
        return await self._call("fetchval", *args, **kwargs)


class FinanceWarningControlOwner:
    """Retain whole work, acquire, transaction, native release and MCP close.

    Caller time is bounded; incomplete cleanup stays retained and quarantined.
    Pool.close is fenced by the lifecycle joining this owner before dependents.
    The index weakrefs ordinary owners, never non-weakrefable native Pools.
    """

    def __init__(self, daemon, *, route_call=None, verifier_url=None):
        if vars(daemon).get("_finance_warning_control_owner") is not None:
            raise RuntimeError("finance_warning_daemon_owner_already_installed")
        self.daemon = daemon
        self.pool = daemon.db.pool
        if self.pool is None:
            raise ValueError("finance_warning_pool_unavailable")
        self.name = daemon.config.name
        self.route_call = route_call
        self.verifier_url = verifier_url
        self.closed = False
        self.quarantined = False
        self.tasks = set()
        self.children = set()
        self.claims = {}
        self.call = None
        self._cancelled = set()
        self._cleanup_failed = False
        previous = _owners.get(id(self.pool))
        if previous is not None:
            raise RuntimeError("finance_warning_owner_already_installed")
        _owners[id(self.pool)] = self

    def installed(self):
        return (
            vars(self.daemon).get("_finance_warning_control_owner") is self
            and self.daemon.db is not None
            and self.daemon.db.pool is self.pool
        )

    def identity(self):
        if not self.installed() or self.closed:
            raise PermissionError("finance_warning_unavailable")
        catalog = vars(self.daemon).get("_tool_catalog")
        epoch = vars(self.daemon).get("_boot_epoch")
        instance = vars(self.daemon).get("_boot_instance_id")
        publisher = vars(self.daemon).get("_catalog_publication")
        if (
            catalog is None
            or not catalog.classification_complete
            or type(epoch) is not int
            or epoch <= 0
            or not isinstance(instance, UUID)
            or instance.version != 7
            or publisher is None
            or publisher.pool is not self.pool
            or publisher.domain != self.name
            or not isinstance(publisher.instance, UUID)
            or not isinstance(publisher.revision, UUID)
            or publisher._publication_pending
            or vars(self.daemon).get("_catalog_publication_failed", False)
            or publisher.generation_digest != catalog.generation_digest
            or (callable(getattr(self.pool, "is_closing", None)) and self.pool.is_closing())
        ):
            raise PermissionError("finance_warning_unavailable")
        if self.name == "finance":
            descriptor = catalog[TOOL]
            if (
                descriptor.module_name != "finance"
                or descriptor.group_name != "subscriptions"
                or descriptor.effect_class != "read"
                or descriptor.owner_authority is not False
            ):
                raise PermissionError("finance_warning_catalog_refused")
        return (
            str(instance),
            epoch,
            str(publisher.instance),
            str(publisher.revision),
            catalog.generation_digest,
        )

    async def _child(self, awaitable, *, cancel=False, defer_cancel=False):
        async def run():
            return await awaitable

        task = asyncio.create_task(run(), name="finance-warning-owned-phase")
        self.children.add(task)
        interrupted = False
        try:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    interrupted = True
                    if cancel and task not in self._cancelled:
                        self._cancelled.add(task)
                        task.cancel()
            result = task.result()
            if interrupted and not defer_cancel:
                raise asyncio.CancelledError
            return result
        finally:
            if task.done():
                self.children.discard(task)
                self._cancelled.discard(task)

    @asynccontextmanager
    async def connection(self):
        # Acquisition itself settles before it is safe to release/close this pool.
        conn = await self._child(self.pool.acquire(), defer_cancel=True)
        try:
            if asyncio.current_task().cancelling():
                raise asyncio.CancelledError
            yield conn
        finally:
            try:
                await self._child(self.pool.release(conn))
            except asyncio.CancelledError:
                raise
            except Exception:
                self._cleanup_failed = True
                raise

    @asynccontextmanager
    async def client_context(self, client):
        # AnyIO/MCP contexts enter and exit in this SAME whole-operation task.
        # A failed close is not successful cleanup or permission to readmit.
        try:
            peer = await client.__aenter__()
        except BaseException:
            self._cleanup_failed = True
            raise
        try:
            yield peer
        finally:
            try:
                await client.__aexit__(*sys.exc_info())
            except BaseException:
                self._cleanup_failed = True
                raise

    async def registrations(self):
        from butlers.core.catalog_reader import PublishedCatalogProvider

        async with self.connection() as conn:
            tx = conn.transaction(isolation="repeatable_read")
            await self._child(tx.start())
            try:
                snapshot = await self._child(PublishedCatalogProvider().snapshot(conn), cancel=True)
            except BaseException:
                await self._child(tx.rollback())
                raise
            else:
                try:
                    rows = await self._child(
                        conn.fetch(
                            "SELECT name,boot_instance_id,boot_epoch "
                            "FROM switchboard.butler_registry_control_plane "
                            "WHERE name=ANY($1::text[])",
                            ["switchboard", "finance"],
                        ),
                        cancel=True,
                    )
                    boots = {
                        row["name"]: (row["boot_instance_id"], row["boot_epoch"]) for row in rows
                    }
                    if set(boots) != {"switchboard", "finance"}:
                        raise PermissionError("finance_warning_boot_unavailable")
                except BaseException:
                    await self._child(tx.rollback())
                    raise
                await self._child(tx.commit())
        return _CurrentRegistrations(snapshot, boots)

    def stop_admission(self):
        self.closed = True
        self.claims.clear()

    def _cancel(self, task):
        if task not in self._cancelled and not task.done():
            self._cancelled.add(task)
            task.cancel()

    async def _bounded(self, operation, deadline):
        if self.closed or self.quarantined or self.tasks:
            return None
        try:
            admitted_identity = self.identity()
        except (PermissionError, KeyError, TypeError):
            admitted_identity = None
        task = asyncio.create_task(operation(), name="finance-warning-whole-operation")
        self.tasks.add(task)

        def settled(done):
            self.tasks.discard(done)
            self._cancelled.discard(done)
            try:
                done.result()
            except BaseException:
                pass  # The caller already received UNKNOWN/cancellation; never a late boolean.
            # Release/query cleanup is fully settled before healthy readmission.
            # Failed native release requires daemon recovery, not optimistic reuse.
            try:
                same_live_owner = (
                    admitted_identity is not None
                    and self.identity() == admitted_identity
                    and not (
                        callable(getattr(self.pool, "is_closing", None)) and self.pool.is_closing()
                    )
                )
            except (PermissionError, KeyError, TypeError):
                same_live_owner = False
            if not self.children and not self._cleanup_failed and same_live_owner:
                self.quarantined = False

        task.add_done_callback(settled)
        try:
            async with asyncio.timeout_at(deadline):
                value = await asyncio.shield(task)
            return value if type(value) is bool else None
        except asyncio.CancelledError:
            self.quarantined = True
            self.claims.clear()
            self._cancel(task)
            raise
        except Exception:
            self.quarantined = True
            self.claims.clear()
            self._cancel(task)
            return None

    async def read(self, args):
        """Only actual daily/urgent assembly gets this bound method, never candidate JSON."""
        if self.name != "switchboard" or self.route_call is None:
            return None
        try:
            args = canonical_args(args)
            identity = self.identity()
        except (ValueError, KeyError, PermissionError, TypeError):
            return None
        deadline = asyncio.get_running_loop().time() + DEADLINE_SECONDS

        async def operation():
            snapshot = await self.registrations()
            receiver = snapshot.registration("finance")
            source_binding = _registration_binding(snapshot, "switchboard")
            if _local_identity(source_binding) != identity:
                raise PermissionError("finance_warning_source_changed")
            descriptor = receiver.catalog[TOOL]
            if descriptor.effect_class != "read" or descriptor.owner_authority is not False:
                raise PermissionError("finance_warning_receiver_refused")
            token = secrets.token_urlsafe(32)
            remote = _registration_binding(snapshot, "finance")
            self.claims[token] = (args, source_binding, remote, deadline)
            call = _HeldCall(self, token, args, remote)
            self.call = call
            try:
                result = await self.route_call(
                    self.pool, "finance", TOOL, args, finance_warning_call=call
                )
                raw = result.get("result") if isinstance(result, dict) else None
                return raw if type(raw) is bool else None
            finally:
                self.claims.pop(token, None)
                self.call = None

        return await self._bounded(operation, deadline)

    async def verify_consume(self, body):
        """One fixed route, not an MCP tool. No locks/acquisitions cross the callback."""
        if type(body) is not dict or set(body) != {"token", "args", "receiver", "nonce"}:
            return None
        token = body["token"]
        if type(token) is not str or type(body["nonce"]) is not str or len(body["nonce"]) != 43:
            return None
        claim = self.claims.get(token)
        if claim is None or self.closed or self.quarantined:
            return None
        args, identity, receiver, deadline = claim
        try:
            supplied_args = canonical_args(body["args"])
        except (ValueError, TypeError, KeyError):
            return None
        if supplied_args != args or body["receiver"] != list(_local_identity(receiver)):
            return None
        # The callback shares the issuer's original absolute deadline, never a
        # fresh query allowance. Native release after cancellation stays owned.
        if asyncio.get_running_loop().time() >= deadline:
            return None
        async with asyncio.timeout_at(deadline):
            snapshot = await self.registrations()
        if (
            _registration_binding(snapshot, "finance") != receiver
            or _registration_binding(snapshot, "switchboard") != identity
            or self.identity() != _local_identity(identity)
            or self.closed
            or self.quarantined
        ):
            return None
        remaining = deadline - asyncio.get_running_loop().time()
        # Atomic compare-and-delete without await after all currentness checks.
        if remaining <= 0 or self.claims.get(token) is not claim:
            return None
        del self.claims[token]
        return {
            "nonce": body["nonce"],
            "receiver": list(_local_identity(receiver)),
            "binding": list(receiver),
            "remaining": remaining,
        }

    def route(self):
        from starlette.responses import JSONResponse
        from starlette.routing import Route

        async def endpoint(request):
            if self.closed or self.name != "switchboard":
                return JSONResponse({"status": "unavailable"}, status_code=403)
            # Closed fixed small wire; never log bearer, args or dynamic exceptions.
            try:
                if int(request.headers.get("content-length", "0")) > 2048:
                    return JSONResponse({"status": "unavailable"}, status_code=403)
            except ValueError:
                return JSONResponse({"status": "unavailable"}, status_code=403)

            async def operation():
                data = bytearray()
                async for chunk in request.stream():
                    if len(data) + len(chunk) > 2048:
                        return None
                    data.extend(chunk)
                import json

                return await self.verify_consume(json.loads(data))

            task = asyncio.create_task(operation(), name="finance-warning-verifier-child")
            self.children.add(task)

            def settled(done):
                self.children.discard(done)
                self._cancelled.discard(done)
                if not done.cancelled():
                    done.exception()  # retrieve errors without logging sensitive wire data

            task.add_done_callback(settled)
            try:
                async with asyncio.timeout(DEADLINE_SECONDS):
                    result = await asyncio.shield(task)
                return JSONResponse(
                    result or {"status": "unavailable"}, status_code=200 if result else 403
                )
            except asyncio.CancelledError:
                self._cancel(task)
                raise
            except Exception:
                self._cancel(task)
                return JSONResponse({"status": "unavailable"}, status_code=403)

        return Route(VERIFY_PATH, endpoint, methods=["POST"])

    async def _receiver_publication_current(self, connection, binding):
        row = await self._child(
            connection.fetchrow(
                "SELECT c.instance_id,c.revision_id,c.epoch,"
                "public.finance_warning_boot_current($2::uuid,$3::bigint) AS boot_current,"
                "c.active AND c.expires_at>clock_timestamp() AS live,"
                "h.body_digest,h.authorization_digest "
                "FROM public.capability_catalog_current c "
                "JOIN public.capability_catalog_history h ON "
                "(h.domain,h.epoch,h.revision_id,h.instance_id)="
                "(c.domain,c.epoch,c.revision_id,c.instance_id) WHERE c.domain=$1",
                "finance",
                UUID(binding[0]),
                binding[1],
            ),
            cancel=True,
        )
        return (
            row is not None
            and row["boot_current"] is True
            and row["live"] is True
            and _local_identity(binding) == self.identity()
            and (
                str(row["instance_id"]),
                row["epoch"],
                str(row["revision_id"]),
                row["body_digest"],
                row["authorization_digest"],
            )
            == (binding[2], binding[3], binding[4], binding[6], binding[7])
        )

    async def consume_and_read(self, args, read_on_connection):
        """Called only by the actual compiled Finance closure, after argument validation."""
        token = _pending.get()
        try:
            args = canonical_args(args)
            identity = self.identity()
        except (ValueError, KeyError, TypeError, PermissionError):
            return None
        if self.name != "finance" or type(token) is not str or not self.verifier_url:
            return None
        start = asyncio.get_running_loop().time()
        deadline = start + DEADLINE_SECONDS

        async def operation():
            nonce = secrets.token_urlsafe(32)
            verification_start = asyncio.get_running_loop().time()
            async with self.client_context(
                httpx.AsyncClient(trust_env=False, timeout=DEADLINE_SECONDS)
            ) as client:
                response = await client.post(
                    self.verifier_url,
                    json={"token": token, "args": args, "receiver": list(identity), "nonce": nonce},
                )
                if response.status_code != 200 or len(response.content) > 2048:
                    return None
                proof = response.json()
            remaining = proof.get("remaining") if type(proof) is dict else None
            binding = proof.get("binding") if type(proof) is dict else None
            if (
                type(remaining) not in (int, float)
                or not 0 < remaining <= DEADLINE_SECONDS
                or proof.get("nonce") != nonce
                or proof.get("receiver") != list(identity)
                or self.identity() != identity
                or type(binding) is not list
                or len(binding) != 8
                or _local_identity(binding) != identity
                or type(binding[3]) is not int
                or binding[3] <= 0
            ):
                return None
            cutoff = min(deadline, verification_start + remaining)
            if asyncio.get_running_loop().time() >= cutoff:
                return None
            async with self.connection() as conn:
                if (
                    self.identity() != identity
                    or asyncio.get_running_loop().time() >= cutoff
                    or not await self._receiver_publication_current(conn, binding)
                ):
                    return None
                # The awaited admission query can consume the shorter issuer
                # lifetime. Expired authority permits cleanup, never a new read.
                if asyncio.get_running_loop().time() >= cutoff or self.identity() != identity:
                    return None
                result = await self._child(
                    read_on_connection(conn, *warning_tuple(**args)), cancel=True
                )
                if not await self._receiver_publication_current(conn, binding):
                    return None
            if asyncio.get_running_loop().time() >= cutoff or self.identity() != identity:
                return None
            return result if type(result) is bool else None

        return await self._bounded(operation, deadline)

    async def settle(self):
        while self.tasks or self.children:
            tasks = tuple(self.tasks | self.children)
            await asyncio.gather(*(asyncio.shield(t) for t in tasks), return_exceptions=True)
        if self.closed and _owners.get(id(self.pool)) is self:
            del _owners[id(self.pool)]

    async def close(self, remaining):
        self.stop_admission()
        for task in tuple(self.tasks):
            self._cancel(task)
        if not self.tasks and not self.children:
            await self.settle()
            return True
        try:
            async with asyncio.timeout(max(0, remaining)):
                await self.settle()
            return True
        except TimeoutError:
            return False
