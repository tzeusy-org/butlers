"""Constructor-owned custody lifecycle for the existing trusted startup path.

The host connection uses the already configured database identity. It is never
returned to a model, MCP caller or restricted API pool. This adds no credential,
role membership, OS privilege or claim of hostile same-UID isolation.
"""

from __future__ import annotations

import contextlib
import uuid
from typing import Any

import asyncpg
from opentelemetry.instrumentation.utils import suppress_instrumentation

from butlers.core.custody_admission import CustodyAdmission, CustodyProfile, _sql
from butlers.core.custody_commands import CustodyCommandReceiver
from butlers.core.custody_installed import verify_installed_functions
from butlers.core.custody_source import CustodyError
from butlers.db import Database, register_jsonb_codec, should_retry_with_ssl_disable


async def _connect(database: Database) -> asyncpg.Connection:
    values: dict[str, Any] = {
        "host": database.host,
        "port": database.port,
        "user": database.user,
        "password": database.password,
        "database": database.db_name,
    }
    if database.ssl is not None:
        values["ssl"] = database.ssl
    with suppress_instrumentation():
        try:
            connection = await asyncpg.connect(**values)
        except Exception as exc:
            if not should_retry_with_ssl_disable(exc, database.ssl):
                raise CustodyError("unavailable") from None
            values["ssl"] = "disable"
            try:
                connection = await asyncpg.connect(**values)
            except Exception:
                raise CustodyError("unavailable") from None
        try:
            await register_jsonb_codec(connection)
        except BaseException:
            await connection.close()
            raise CustodyError("unavailable") from None
    return connection


def release_closed_pool_allocations(pool: asyncpg.Pool) -> None:
    """Actual pool-close lifecycle releases only that now unusable allocation."""
    if type(pool) is not asyncpg.Pool or pool._closed is not True:
        raise CustodyError("refused")
    from butlers.core.custody_bindings import owning_binding_publisher, remove_binding_publisher
    from butlers.core.custody_producer import _installed_producers, remove_accepted_producer

    publisher = owning_binding_publisher(pool)
    if publisher is not None:
        remove_binding_publisher(pool, publisher)
    producer = _installed_producers.get(pool)
    if producer is not None:
        remove_accepted_producer(pool, producer)


class CustodyRuntime:
    """Owns one dedicated host connection and one restricted live DB anchor.

    Start is called by actual daemon/connector administrative startup, with a
    constructor-fixed profile. The pool remains its existing owning pool.
    Enrollment is not offered as an HTTP/MCP tool. Database existence, schema
    installation and restore-history readiness must already be established.
    The restricted API requires one-shot parent startup-pipe integration; it
    cannot use this factory to obtain an administrative connection.
    """

    def __init__(
        self,
        database: Database,
        profile: CustodyProfile,
        *,
        _api_writer_pool: asyncpg.Pool | None = None,
    ) -> None:
        if profile.actor == "dashboard":
            raise CustodyError("refused")
        if database.role != profile.role:
            raise CustodyError("unavailable")
        if _api_writer_pool is not None:
            # Fixed API startup owns this actual pool. This private allocation
            # is solely a Relationship canonical writer, never an MCP receiver
            # or an ingress/owner-command source. Every checkout still proves
            # its actual login/role/backend and enters SET ROLE before binding.
            if (
                not isinstance(_api_writer_pool, asyncpg.Pool)
                or profile.actor != "relationship"
                or profile.role != "butler_relationship_rw"
                or profile.source_kinds != ("domain_evidence",)
                or profile.operations
                or profile.audiences
            ):
                raise CustodyError("refused")
            self._pool = _api_writer_pool
        elif database.pool is None or database.role_enforcement_disabled:
            raise CustodyError("refused")
        else:
            self._pool = database.pool
        self._database = database
        self._profile = profile
        self._host: asyncpg.Connection | None = None
        self.admission: CustodyAdmission | None = None
        self._receipt: dict | None = None
        self._mcp: Any | None = None
        self.channel_bindings: Any | None = None
        self.accepted_ingress: Any | None = None
        self.accepted_producer: Any | None = None
        self.accepted_worker: Any | None = None

    async def start(self) -> CustodyAdmission:
        if self.admission is not None or self._host is not None:
            raise CustodyError("conflict")
        host = await _connect(self._database)
        anchor = None
        admission = None
        try:
            if not await self._database._verify_role_exists(host):
                raise CustodyError("unavailable")
            # This connection never passed through a SET ROLE runtime pool.
            # The existing trusted bootstrap/migration identity checks itself;
            # a restricted LOGIN that merely RESET ROLEs cannot enroll.
            # Core replay can precede the owning Switchboard migration. Replay
            # the EXISTING fixed trusted installer after those migrations so
            # its INSERT trigger is present before proof/enrollment/serving.
            # This refuses private schema or trigger drift; it does not adopt
            # arbitrary relations, source bytes, roles or peer interfaces.
            await _sql(
                host,
                "SELECT pg_catalog.jsonb_build_object('installed',true) "
                "FROM (SELECT custody_admission.install_interface()) installed",
            )
            proof = await _sql(host, "SELECT custody_admission.prove_interface()")
            verify_installed_functions(proof)
            anchor = await _connect(self._database)

            async def enroll(manifest: dict) -> dict:
                return await _sql(host, "SELECT custody_admission.host_enroll($1::jsonb)", manifest)

            admission = CustodyAdmission(self._profile, self._pool, anchor, host_enroll=enroll)
            receipt = await admission.start()
        except BaseException:
            try:
                if admission is not None:
                    await admission.stop()
                elif anchor is not None:
                    await anchor.close()
            finally:
                await host.close()
            raise
        self._host, self.admission, self._receipt = host, admission, receipt
        if self._profile.actor == "relationship":
            from butlers.core.custody_bindings import (
                CustodyChannelBindings,
                install_binding_publisher,
            )

            self.channel_bindings = CustodyChannelBindings(admission)
            try:
                install_binding_publisher(self._pool, self.channel_bindings)
            except BaseException:
                await self.stop()
                raise
        elif self._profile.actor == "switchboard":
            from butlers.core.custody_ingress import CustodyAcceptedIngress
            from butlers.core.custody_producer import (
                CustodyAcceptedProducer,
                install_accepted_producer,
            )

            self.accepted_ingress = CustodyAcceptedIngress(admission)
            self.accepted_producer = CustodyAcceptedProducer(admission, self._pool)
            try:
                install_accepted_producer(self._pool, self.accepted_producer)
            except BaseException:
                await self.stop()
                raise
        return admission

    async def prepare_host_command(self, operation: str, selection: dict) -> dict:
        """Private trusted-host producer, absent from every model/API tool.

        The existing host connection checks itself and current control in SQL.
        The returned locator is not authority; the acquired source writer must
        recheck this prepared command when registering its immutable source.
        """
        if self._profile.actor != "host-switchboard" or self._host is None:
            raise CustodyError("refused")
        return await _sql(
            self._host,
            "SELECT dashboard_auth.custody_host_prepare($1,$2::jsonb)",
            operation,
            selection,
        )

    def attach_mcp(self, mcp: Any) -> Any:
        """Trusted daemon constructor installs its fixed current admission.

        Call after start and before the canonical core dispatcher registers its
        definitions. This installs real middleware, not an enrollment endpoint.
        The daemon retains the runtime through shutdown; separate API and
        connector lifecycle integrations remain distinct.
        """
        from butlers.core.custody_admission import CustodyMcpService

        if not self._profile.operations:
            raise CustodyError("refused")

        if self.admission is None:
            raise CustodyError("refused")
        if self._mcp is not None:
            raise CustodyError("conflict")
        handlers = (
            CustodyCommandReceiver(self.admission).handlers()
            if self._profile.actor == "switchboard"
            else {}
        )
        service = CustodyMcpService(self.admission, handlers)
        service.install_guard(mcp)
        self._mcp = mcp
        return service

    def start_accepted_worker(self) -> None:
        """Only actual Switchboard starts after its MCP server is listening."""
        if self._profile.actor != "switchboard":
            return
        if (
            self.accepted_worker is not None
            or self.accepted_producer is None
            or self._host is None
            or self._receipt is None
            or self._mcp is None
        ):
            raise CustodyError("refused")
        from butlers.core.custody_producer import CustodyAcceptedWorker

        async def host_call(action: str, payload: dict) -> dict:
            if self._host is None:
                raise CustodyError("unavailable")
            return await _sql(
                self._host, "SELECT custody_admission.accepted_work($1,$2::jsonb)", action, payload
            )

        self.accepted_worker = CustodyAcceptedWorker(
            self.accepted_producer, host_call, self._receipt["process_id"]
        )
        self.accepted_worker.start()

    async def stop(self) -> None:
        admission, host, receipt = self.admission, self._host, self._receipt
        publisher = self.channel_bindings
        producer = self.accepted_producer
        # Refuse new local writers immediately. Retain the fixed callback until
        # revocation/anchor shutdown completes, so absence cannot mean legacy
        # unguarded fallback during an awaited revoke operation.
        if admission is not None:
            admission._ready = False
        worker, self.accepted_worker = self.accepted_worker, None
        self.admission, self._host, self._receipt = None, None, None
        self._mcp = None
        self.channel_bindings = None
        self.accepted_ingress = None
        self.accepted_producer = None
        try:
            try:
                if worker is not None:
                    await worker.stop()
            finally:
                if host is not None and receipt is not None:
                    with contextlib.suppress(CustodyError):
                        await _sql(
                            host,
                            "SELECT custody_admission.host_revoke($1,$2)",
                            uuid.UUID(receipt["process_id"]),
                            receipt["control_epoch"],
                        )
        finally:
            try:
                if admission is not None:
                    await admission.stop()
            finally:
                try:
                    if host is not None:
                        await host.close()
                finally:
                    if publisher is not None:
                        from butlers.core.custody_bindings import remove_binding_publisher

                        remove_binding_publisher(self._pool, publisher)
                    if producer is not None:
                        from butlers.core.custody_producer import remove_accepted_producer

                        remove_accepted_producer(self._pool, producer)
