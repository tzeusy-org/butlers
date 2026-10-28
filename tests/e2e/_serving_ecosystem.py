"""Explicit E2E serving fixture; shared ownership is confined to the pytest session.

Module-local fixture imports create distinct FixtureDefs. The session owns one
setup/close task; importing this module installs no hooks, plugin or autouse state.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING, Any

import pytest
from testcontainers.postgres import PostgresContainer

from tests.e2e import conftest as public

if TYPE_CHECKING:
    from butlers.config import ButlerConfig
    from butlers.db import Database


async def _settle(task: asyncio.Task[Any]) -> asyncio.CancelledError | None:
    """Join owned work through repeated waiter cancellation without cancelling it."""
    cancelled = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            if cancelled is None:
                cancelled = exc
        except BaseException:
            break
    return cancelled


class _SessionOwner:
    """One source-bound setup and teardown, with no retry after failure or close."""

    def __init__(
        self,
        container: PostgresContainer,
        loop: asyncio.AbstractEventLoop,
        bootstrap: Callable[
            [PostgresContainer], AbstractAsyncContextManager[public.ButlerEcosystem]
        ],
    ) -> None:
        self.container = container
        self.loop = loop
        self.bootstrap = bootstrap
        self.state = "STARTING"
        self.context = bootstrap(container)
        self.close_task: asyncio.Task[None] | None = None
        self.borrowers = 0
        self.borrowers_done = asyncio.Event()
        self.borrowers_done.set()
        self.setup_task = loop.create_task(self._setup())

    def require_identity(self, container, loop, bootstrap) -> None:
        if (
            container is not self.container
            or loop is not self.loop
            or bootstrap is not self.bootstrap
        ):
            raise RuntimeError("E2E session owner input identity changed")
        if self.close_task is not None or self.state not in {"STARTING", "READY"}:
            raise RuntimeError("E2E session owner is unavailable; retry is forbidden")

    def begin_borrow(self) -> None:
        self.borrowers += 1
        self.borrowers_done.clear()

    def end_borrow(self) -> None:
        self.borrowers -= 1
        if self.borrowers == 0:
            self.borrowers_done.set()

    async def _setup(self) -> public.ButlerEcosystem:
        try:
            ecosystem = await self.context.__aenter__()
        except BaseException:
            self.state = "FAILED"
            raise
        self.state = "READY"
        return ecosystem

    async def _close(self, primary: BaseException | None) -> None:
        if not self.setup_task.done():
            # Only the first setup owner reaches this path. Borrowers never
            # cancel shared setup or gain cleanup authority.
            self.setup_task.cancel()
        await _settle(self.setup_task)
        await self.borrowers_done.wait()
        try:
            self.setup_task.result()
        except BaseException:
            # Failed __aenter__ already ran the context's cleanup. Preserve its
            # failed state and exception; do not reenter or call __aexit__ twice.
            self.state = "FAILED"
            return
        self.state = "CLOSING"
        args = (
            (None, None, None)
            if primary is None
            else (type(primary), primary, primary.__traceback__)
        )
        try:
            await self.context.__aexit__(*args)
        finally:
            self.state = "CLOSED"

    async def close(self, primary: BaseException | None = None) -> None:
        if self.close_task is None:
            self.close_task = self.loop.create_task(self._close(primary))
        cancelled = await _settle(self.close_task)
        failure = None
        try:
            self.close_task.result()
        except BaseException as exc:
            failure = exc
        if primary is not None:
            if failure is not None:
                primary.add_note("E2E session cleanup also failed")
                raise primary from failure
            return
        if cancelled is not None:
            if failure is not None:
                cancelled.add_note("E2E session cleanup also failed")
            raise cancelled from failure
        if failure is not None:
            raise failure


_SESSION_OWNER: pytest.StashKey[_SessionOwner] = pytest.StashKey()


@asynccontextmanager
async def _session_ecosystem(
    session: pytest.Session,
    postgres_container: PostgresContainer,
    *,
    bootstrap=None,
) -> AsyncIterator[public.ButlerEcosystem]:
    """Borrow the same session owner; only the first real fixture owns teardown.

    The private bootstrap argument permits source-owned provider-free lifetime
    proofs. The actual E2E fixture always selects _boot_ecosystem. Factory object,
    container and actual running loop are part of the same-session identity.
    """
    selected = _boot_ecosystem if bootstrap is None else bootstrap
    loop = asyncio.get_running_loop()
    owner = session.stash.get(_SESSION_OWNER, None)
    first = owner is None
    if first:
        owner = _SessionOwner(postgres_container, loop, selected)
        session.stash[_SESSION_OWNER] = owner
    else:
        owner.require_identity(postgres_container, loop, selected)
        owner.begin_borrow()
    primary = None
    try:
        ecosystem = await asyncio.shield(owner.setup_task)
        yield ecosystem
    except BaseException as exc:
        primary = exc
        raise
    finally:
        if first:
            await owner.close(primary)
        else:
            owner.end_borrow()


@pytest.fixture(scope="session")
async def butler_ecosystem(
    request: pytest.FixtureRequest,
    postgres_container: PostgresContainer,
) -> AsyncIterator[public.ButlerEcosystem]:
    """Opt in explicitly from each actual consumer module; never mutate process env."""
    async with _session_ecosystem(request.session, postgres_container) as ecosystem:
        yield ecosystem


@asynccontextmanager
async def _boot_ecosystem(
    postgres_container: PostgresContainer,
) -> AsyncIterator[public.ButlerEcosystem]:
    """Provision and boot all roster butlers using a five-phase structured bootstrap.

    Phase 1 — Provision:
        Start testcontainer PostgreSQL, run all Alembic migrations (core +
        butler-specific + module chains), create per-butler schemas, and ensure
        the ``message_inbox`` partition for the current month exists (switchboard
        schema).

    Phase 2 — Configure:
        Load all roster configs, apply ``public.E2E_PORT_OFFSET`` to every butler port,
        and patch non-switchboard butlers to point at the E2E switchboard URL.

    Phase 3 — Authenticate:
        Probe each CLI auth provider (e.g. ``claude``) for token validity using
        the actual status command — not just file existence.  Butlers that have
        OAuth-dependent modules (calendar, email) are checked; missing or expired
        tokens trigger an interactive re-auth prompt.

    Phase 4 — Boot:
        Start all ``ButlerDaemon`` instances, then wait for every SSE port to
        respond with HTTP 200 (health check).

    Phase 5 — Validate:
        Smoke-test each running butler with a no-op MCP ``status`` call.

    Teardown (try/finally):
        All daemons are stopped, DB pools are closed, and the PostgreSQL
        container is removed — on normal exit, test failure, or
        KeyboardInterrupt/SIGTERM.

    Yields:
        public.ButlerEcosystem with all daemons running and pools connected.

    Raises:
        RuntimeError: If any phase fails, reporting which phase and why.
    """
    from butlers.config import list_butlers, load_config
    from butlers.credential_store import CredentialPurposeConfig
    from butlers.daemon import ButlerDaemon
    from butlers.testing.migration import async_serving_test_deployment

    ecosystem = public.ButlerEcosystem(postgres_container=postgres_container)
    owned = AsyncExitStack()
    butler_names: list[str] = []

    # Install SIGTERM → KeyboardInterrupt so finally blocks run on container stop.
    public._install_sigterm_handler()

    try:
        # ------------------------------------------------------------------
        # Phase 1 — Provision
        # ------------------------------------------------------------------
        public.logger.info("[Phase 1/5] Provision — starting PostgreSQL and running migrations")
        public._report_phase("Provision")

        host = postgres_container.get_container_host_ip()
        port = int(postgres_container.get_exposed_port(5432))
        user = postgres_container.username
        password = postgres_container.password

        # Discover roster butlers
        butler_configs_raw = list_butlers()
        butler_names = [b.name for b in butler_configs_raw]
        public.logger.info("Discovered %d butlers: %s", len(butler_names), butler_names)

        deployment = await owned.enter_async_context(
            async_serving_test_deployment(postgres_container)
        )
        credential_purpose = CredentialPurposeConfig(
            database_url=deployment.purpose_url("credential"), schema="public"
        )

        # Ensure the message_inbox partition for the current month exists.
        # This lives in the switchboard schema; the switchboard migration chain
        # creates the ensure_partition function and the initial partitions.
        # We trigger it explicitly here to guarantee the current-month partition
        # is present at test time (the migration only runs at session start, not
        # mid-month).
        await public._ensure_message_inbox_partition(host, port, user, password)

        public.logger.info("[Phase 1/5] Provision complete")

        # ------------------------------------------------------------------
        # Phase 2 — Configure
        # ------------------------------------------------------------------
        public.logger.info(
            "[Phase 2/5] Configure — applying port offsets and switchboard URL patches"
        )
        public._report_phase("Configure")

        # Compute switchboard E2E port from its base config
        switchboard_base_config = load_config(
            public._THIS_FILE.parent.parent.parent / "roster" / "switchboard"
        )
        switchboard_e2e_port = switchboard_base_config.port + public.E2E_PORT_OFFSET

        # Build configs and DB instances per butler
        butler_configs: dict[str, tuple[ButlerConfig, Database]] = {}
        for butler_config in butler_configs_raw:
            butler_name = butler_config.name

            # Create per-butler Database instance (schema-scoped)
            db = deployment.runtime_database(butler_config.db_schema, min_size=2, max_size=10)
            pool = await db.connect()
            ecosystem.pools[butler_name] = pool

            # Apply port offset
            butler_config.port += public.E2E_PORT_OFFSET

            # Patch switchboard_url for non-switchboard butlers
            if butler_config.name != "switchboard":
                butler_config.switchboard_url = f"http://localhost:{switchboard_e2e_port}/sse"

            butler_configs[butler_name] = (butler_config, db)

        public.logger.info(
            "[Phase 2/5] Configure complete — %d butlers configured", len(butler_names)
        )

        # ------------------------------------------------------------------
        # Phase 3 — Authenticate
        # ------------------------------------------------------------------
        public.logger.info("[Phase 3/5] Authenticate — validating CLI auth token validity")
        public._report_phase("Authenticate")

        await public._validate_auth_tokens()

        public.logger.info("[Phase 3/5] Authenticate complete")

        # ------------------------------------------------------------------
        # Phase 4 — Boot
        # ------------------------------------------------------------------
        public.logger.info("[Phase 4/5] Boot — starting butler daemons")
        public._report_phase("Boot")

        for butler_name in butler_names:
            butler_config, db = butler_configs[butler_name]
            public.logger.info("Starting butler: %s (port %s)", butler_name, butler_config.port)

            daemon = ButlerDaemon(
                butler_name=butler_name, db=db, credential_purpose=credential_purpose
            )
            daemon.config = butler_config
            deployment.retain(daemon, "shutdown")
            await daemon.start()
            ecosystem.butlers[butler_name] = daemon

            public.logger.info("Butler %s started on port %s", butler_name, butler_config.port)

        # Wait for all SSE ports to respond
        public.logger.info("Waiting for all butler SSE ports to be healthy...")
        await public._wait_for_ecosystem_health(ecosystem)
        public.logger.info("[Phase 4/5] Boot complete — all butlers responding")

        # ------------------------------------------------------------------
        # Phase 5 — Validate
        # ------------------------------------------------------------------
        public.logger.info("[Phase 5/5] Validate — smoke-testing each butler via MCP")
        public._report_phase("Validate")

        await public._smoke_test_butlers(ecosystem)

        public.logger.info("[Phase 5/5] Validate complete — ecosystem ready")

        yield ecosystem

    finally:
        # ------------------------------------------------------------------
        # Teardown: stop daemons, close pools, container auto-stops via fixture
        # ------------------------------------------------------------------
        public.logger.info("Tearing down ecosystem (daemons + DB pools)...")
        await owned.aclose()
        public.logger.info("Ecosystem teardown complete")
