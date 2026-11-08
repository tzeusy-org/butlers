"""Isolated genuine PG17 topology for public-MCP database contracts.

No shared test cluster authentication is changed. The dedicated container owns
its roles, peer map, databases, slots and lifetime. Subscriber creation is always
explicit, so primary-only tests cannot accidentally gain a subscriber companion.
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import tarfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import OperationalError

from butlers.migrations import run_migrations
from butlers.testing.migration import (
    _bootstrap_migration_prerequisites,
    create_migration_db,
    migration_bootstrap_db_url,
)

if TYPE_CHECKING:
    from testcontainers.postgres import PostgresContainer

_HBA = """local all all peer map=local
host all butlers_projection_publisher all reject
host butlers_public mcp_public_ro,mcp_proposals_reader all scram-sha-256
host all mcp_public_ro,mcp_proposals_reader all reject
host all all all scram-sha-256
"""
_IDENT = """local postgres postgres
local postgres butlers_projection_publisher
"""


def _put(container, name: str, content: str) -> None:
    data = content.encode()
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        entry = tarfile.TarInfo(name)
        entry.size = len(data)
        entry.mode = 0o644
        archive.addfile(entry, io.BytesIO(data))
    assert container.get_wrapped_container().put_archive("/tmp", stream.getvalue())


@contextmanager
def public_mcp_cluster():
    """One cluster with the mandated socket and peer rules; no managed roles yet."""
    from testcontainers.postgres import PostgresContainer

    # Synthetic disposable credential, passed only between owning processes.
    with (
        PostgresContainer("pgvector/pgvector:pg17", username="postgres", password=uuid.uuid4().hex)
        .with_kwargs(tmpfs={"/controller/run": "rw,mode=0777"}, shm_size="256m")
        .with_command(
            "postgres -c wal_level=logical -c max_replication_slots=64 "
            "-c max_wal_senders=32 -c max_slot_wal_keep_size=256MB"
        ) as postgres
    ):
        _put(postgres, "public_mcp_hba.conf", _HBA)
        _put(postgres, "public_mcp_ident.conf", _IDENT)
        engine = create_engine(
            postgres.get_connection_url(),
            isolation_level="AUTOCOMMIT",
            connect_args={"connect_timeout": 5},
        )
        try:
            # The image initializes its database using a temporary server and a
            # client with PGHOST cleared. Leave that first server's default
            # socket intact; configure the final peer transport only afterwards.
            with engine.connect() as conn:
                conn.exec_driver_sql("ALTER SYSTEM SET hba_file='/tmp/public_mcp_hba.conf'")
                conn.exec_driver_sql("ALTER SYSTEM SET ident_file='/tmp/public_mcp_ident.conf'")
                conn.exec_driver_sql("ALTER SYSTEM SET unix_socket_directories='/controller/run'")
            engine.dispose()
            _restart_peer_cluster(postgres, engine.url)
            yield postgres
        finally:
            engine.dispose()


def _restart_peer_cluster(postgres: PostgresContainer, old_url) -> None:
    """Restart once, then inspect current endpoints without emitting credentials."""
    from docker.errors import DockerException

    def endpoint(url):
        parsed = make_url(url)
        return {"host": parsed.host, "port": parsed.port}

    def error_facts(exc):
        original = getattr(exc, "orig", None)
        code = getattr(original, "pgcode", None)
        return {
            "error_class": type(exc).__name__,
            "driver_class": type(original).__name__ if original is not None else None,
            "sqlstate": code
            if isinstance(code, str) and re.fullmatch(r"[0-9A-Z]{5}", code)
            else None,
        }

    wrapped = postgres.get_wrapped_container()
    facts = {
        "stage": "restart",
        "old_endpoint": endpoint(old_url),
        "fresh_endpoint": None,
        "status": "unknown",
        "running": None,
        "exit_code": None,
        "socket_exit_code": None,
    }
    restart_failed = False
    try:
        # PID1 belongs to Docker. Restart the exact owned container, preserving
        # its initialized volume and auth files while recreating the socket tmpfs.
        wrapped.restart(timeout=10)
    except DockerException as exc:
        facts["restart_error"] = error_facts(exc)
        restart_failed = True

    deadline = time.monotonic() + 30
    while True:
        try:
            facts["stage"] = "inspect"
            wrapped.reload()
            state = wrapped.attrs.get("State", {})
            status = state.get("Status")
            facts["status"] = (
                status
                if status
                in {"created", "restarting", "running", "removing", "paused", "exited", "dead"}
                else "unknown"
            )
            facts["running"] = state.get("Running") if type(state.get("Running")) is bool else None
            exit_code = state.get("ExitCode")
            facts["exit_code"] = exit_code if type(exit_code) is int else None
            if restart_failed or facts["status"] in {"exited", "dead"}:
                break
            # Match Testcontainers' connection-mode/first-port semantics, using
            # this fresh inspect. get_connection_url/get_exposed_port would enter
            # an independent status waiter (with its own timeout and raw logs).
            # Keep endpoint readiness inside this loop instead.
            facts["stage"] = "endpoint-pending"
            facts["fresh_endpoint"] = None
            port = postgres.port
            if postgres.get_docker_client().get_connection_mode().use_mapped_port:
                bindings = (
                    wrapped.attrs.get("NetworkSettings", {}).get("Ports", {}).get(f"{port}/tcp")
                )
                candidate = bindings[0].get("HostPort") if bindings else None
                if not isinstance(candidate, str) or not candidate.isdecimal():
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.1)
                    continue
                port = int(candidate)
                if not 1 <= port <= 65535:
                    break
            fresh_url = make_url(old_url).set(host=postgres.get_container_host_ip(), port=port)
            facts["fresh_endpoint"] = endpoint(fresh_url)
            facts["stage"] = "peer-socket"
            peer = wrapped.exec_run(
                [
                    "psql",
                    "-X",
                    "-w",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-h",
                    "/controller/run",
                    "-U",
                    "postgres",
                    "-d",
                    "postgres",
                    "-Atqc",
                    "SELECT 1",
                ],
                user="postgres",
                environment={"PGCONNECT_TIMEOUT": "2"},
            )
            facts["socket_exit_code"] = peer.exit_code
            if (
                peer.exit_code == 0
                and isinstance(peer.output, bytes)
                and peer.output.strip() == b"1"
            ):
                facts["stage"] = "fresh-tcp"
                fresh_engine = create_engine(
                    fresh_url,
                    isolation_level="AUTOCOMMIT",
                    connect_args={"connect_timeout": 5},
                )
                try:
                    with fresh_engine.connect() as conn:
                        actual = conn.exec_driver_sql(
                            "SELECT current_setting('unix_socket_directories'), "
                            "current_setting('hba_file'), current_setting('ident_file')"
                        ).one()
                    if actual != (
                        "/controller/run",
                        "/tmp/public_mcp_hba.conf",
                        "/tmp/public_mcp_ident.conf",
                    ):
                        facts["stage"] = "configuration-mismatch"
                        break
                    return
                finally:
                    fresh_engine.dispose()
        except (OperationalError, DockerException) as exc:
            facts.update(error_facts(exc))
        if time.monotonic() >= deadline:
            break
        time.sleep(0.1)
    # Only the explicitly selected closed fields above are serialized. Never
    # emit Docker attrs/Env, connection URLs, raw driver messages or SQL params.
    raise RuntimeError(
        "dedicated public MCP restart refused: " + json.dumps(facts, sort_keys=True)
    ) from None


def create_public_mcp_roles(conn, *, include_subscription_owner: bool = True) -> dict[str, str]:
    """Actual externally-managed role shape, never production identity repair."""
    conn.exec_driver_sql(
        "CREATE ROLE butlers_projection_publisher LOGIN REPLICATION NOINHERIT "
        "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD NULL"
    )
    conn.exec_driver_sql("CREATE ROLE mcp_projection_owner NOLOGIN NOREPLICATION")
    conn.exec_driver_sql(
        "CREATE ROLE mcp_public_ro LOGIN NOINHERIT NOREPLICATION CONNECTION LIMIT 10"
    )
    conn.exec_driver_sql("CREATE ROLE mcp_proposals_reader LOGIN NOREPLICATION")
    if include_subscription_owner:
        conn.exec_driver_sql("CREATE ROLE mcp_subscription_owner LOGIN NOREPLICATION")
        conn.exec_driver_sql(
            "GRANT pg_create_subscription,mcp_projection_owner TO mcp_subscription_owner"
        )
    # These are disposable positive TCP principals, not the credentialless publisher.
    passwords = {role: uuid.uuid4().hex for role in ("mcp_public_ro", "mcp_proposals_reader")}
    for role, password in passwords.items():
        conn.execute(text(f'ALTER ROLE "{role}" PASSWORD :password'), {"password": password})
    return passwords


@dataclass
class PublicMcpTopology:
    container: PostgresContainer
    engine: Engine
    admin: Engine
    migration_role: str
    role_passwords: dict[str, str] = field(repr=False)

    def reader_engine(self, role: str, database: str = "butlers_public") -> Engine:
        url = make_url(self.container.get_connection_url()).set(
            username=role,
            password=self.role_passwords.get(role, "synthetic-refusal"),
            database=database,
        )
        return create_engine(url, connect_args={"connect_timeout": 5})

    def execute(self, program: str):
        """Run the actual emitted psql program as OS postgres on its fixed socket."""
        name = "public_mcp_program_" + uuid.uuid4().hex + ".sql"
        _put(self.container, name, program)
        try:
            return self.container.get_wrapped_container().exec_run(
                [
                    "psql",
                    "-X",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-h",
                    "/controller/run",
                    "-U",
                    "postgres",
                    "-d",
                    "butlers",
                    "-f",
                    "/tmp/" + name,
                ],
                user="postgres",
                environment={"PGCONNECT_TIMEOUT": "5"},
            )
        finally:
            self.container.get_wrapped_container().exec_run(["rm", "/tmp/" + name])

    @contextmanager
    def authentication(self, *, hba: str = _HBA, ident: str = _IDENT):
        """Scoped real authentication fault; restore only this owned cluster."""
        engine = create_engine(self.container.get_connection_url(), isolation_level="AUTOCOMMIT")
        try:
            _put(self.container, "public_mcp_hba.conf", hba)
            _put(self.container, "public_mcp_ident.conf", ident)
            with engine.connect() as conn:
                conn.exec_driver_sql("SELECT pg_reload_conf()")
            yield
        finally:
            _put(self.container, "public_mcp_hba.conf", _HBA)
            _put(self.container, "public_mcp_ident.conf", _IDENT)
            with engine.connect() as conn:
                conn.exec_driver_sql("SELECT pg_reload_conf()")
            engine.dispose()

    def projection_engine(self) -> Engine:
        return create_engine(migration_bootstrap_db_url(self.container, "butlers_public"))


@contextmanager
def public_mcp_topology():
    """Real migrations in all nine schemas, with no publication/subscription yet."""
    with public_mcp_cluster() as postgres:
        url = create_migration_db(postgres, "butlers")
        engine = create_engine(url)
        admin_url = migration_bootstrap_db_url(postgres, "butlers")
        admin = create_engine(admin_url)
        cluster = create_engine(postgres.get_connection_url(), isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as conn:
                principal = conn.execute(text("SELECT current_user")).scalar_one()
                assert (
                    conn.execute(text("SELECT to_regclass('mcp_surface.controls')")).scalar_one()
                    is None
                )
                assert (
                    conn.execute(
                        text(
                            "SELECT rolsuper OR rolreplication FROM pg_roles "
                            "WHERE rolname=current_user"
                        )
                    ).scalar_one()
                    is False
                )
            with cluster.connect() as conn:
                passwords = create_public_mcp_roles(conn)
                conn.exec_driver_sql("CREATE DATABASE butlers_public OWNER mcp_projection_owner")
            _bootstrap_migration_prerequisites(admin_url, principal)

            async def install():
                for domain in (
                    "general",
                    "lifestyle",
                    "education",
                    "travel",
                    "relationship",
                    "finance",
                    "health",
                ):
                    await run_migrations(url, chain="core", schema=domain)
                    if domain in ("travel", "relationship", "finance", "health"):
                        await run_migrations(url, chain=domain, schema=domain)
                    await run_migrations(url, chain="memory", schema=domain)
                await run_migrations(url, chain="core", schema="chronicler")
                await run_migrations(url, chain="core", schema="chronicler_mem")
                await run_migrations(url, chain="memory", schema="chronicler_mem")
                await run_migrations(url, chain="chronicler", schema="chronicler")

            asyncio.run(install())
            yield PublicMcpTopology(postgres, engine, admin, principal, passwords)
        finally:
            engine.dispose()
            admin.dispose()
            cluster.dispose()
