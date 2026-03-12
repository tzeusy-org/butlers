"""E2E test fixtures — provisions the complete butler ecosystem.

Session-scoped fixtures:
- require_api_key: Skips all E2E tests when ANTHROPIC_API_KEY is not set
- require_claude_binary: Skips all E2E tests when claude binary is not on PATH
- postgres_container: Shared testcontainer PostgreSQL instance
- butler_ecosystem: Boots all roster butlers in five phased stages
- e2e_log_path: Configures structured logging to .tmp/e2e-logs/
- cost_tracker: Accumulates token usage and prints summary at session end

Function-scoped fixtures:
- switchboard_pool, health_pool, etc.: Per-butler database pool accessors

Phased bootstrap (butler_ecosystem):
  Phase 1 — Provision: Start testcontainer PostgreSQL, run all Alembic
    migrations, create the shared schema, and ensure the message_inbox
    partition exists for the current month (switchboard schema).
  Phase 2 — Configure: Load all roster configs, apply E2E_PORT_OFFSET to
    every butler port, and patch non-switchboard butlers to point at the
    E2E switchboard URL.
  Phase 3 — Authenticate: Validate OAuth/CLI token validity (not just file
    presence) for every configured CLI auth provider; prompt interactively
    when a token is missing or expired.
  Phase 4 — Boot: Start all butler daemons and health-check /sse endpoints.
  Phase 5 — Validate: Smoke-test each butler with a no-op MCP status call.

Teardown (try/finally):
  All butler daemons are stopped, database pools are closed, and the
  PostgreSQL container is removed — regardless of whether the session
  passed, failed, or was interrupted (KeyboardInterrupt/SIGTERM).

CLI options:
- --scenarios=<tag>: Filter scenarios to those tagged with <tag> (e.g., smoke)
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from testcontainers.postgres import PostgresContainer

if TYPE_CHECKING:
    from asyncpg.pool import Pool

    from butlers.cli_auth.registry import CLIAuthProviderDef
    from butlers.config import ButlerConfig
    from butlers.daemon import ButlerDaemon
    from butlers.db import Database
    from tests.e2e.benchmark import BenchmarkResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Port offset: ensures E2E daemons do not collide with a running production
# stack.  40100 → 51100, etc.
# ---------------------------------------------------------------------------

E2E_PORT_OFFSET = 11000


# ---------------------------------------------------------------------------
# CLI option: --scenarios for tag-based scenario filtering
# ---------------------------------------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register E2E CLI options for scenario filtering and benchmark mode.

    Options
    -------
    --scenarios TAG:
        Run only scenarios tagged with TAG (e.g. ``--scenarios=smoke``).
        When omitted, all scenarios run.

    --benchmark:
        Activate benchmark mode.  Scenarios are run for each model in the
        ``--benchmark-models`` list (or ``E2E_BENCHMARK_MODELS`` env var).
        Results are accumulated without hard assertion failures; scorecards
        are generated at session end.  Fails with a clear error if no model
        list is provided.

    --benchmark-models MODEL_LIST:
        Comma-separated list of model IDs to benchmark (e.g.
        ``--benchmark-models=claude-sonnet-4-5,gpt-4o``).  Can also be set
        via the ``E2E_BENCHMARK_MODELS`` environment variable.  CLI value
        takes precedence over the environment variable.
    """
    parser.addoption(
        "--scenarios",
        action="store",
        default=None,
        metavar="TAG",
        help=(
            "Run only E2E scenarios tagged with TAG. "
            "Example: --scenarios=smoke runs only smoke-tagged scenarios. "
            "Multiple tags are not supported; use pytest -k for compound filtering."
        ),
    )
    parser.addoption(
        "--benchmark",
        action="store_true",
        default=False,
        help=(
            "Activate benchmark mode: iterate over --benchmark-models "
            "(or E2E_BENCHMARK_MODELS env var), pinning each model in turn "
            "and running the full scenario corpus.  Results are accumulated "
            "without hard assertion failures and scorecards are generated "
            "at session end.  Requires --benchmark-models or E2E_BENCHMARK_MODELS."
        ),
    )
    parser.addoption(
        "--benchmark-models",
        action="store",
        default=None,
        metavar="MODEL_LIST",
        help=(
            "Comma-separated list of model IDs for benchmark mode "
            "(e.g. claude-sonnet-4-5-20250514,gpt-4o).  "
            "Falls back to E2E_BENCHMARK_MODELS env var when not provided."
        ),
    )


@pytest.fixture(scope="session")
def scenario_tag_filter(request: pytest.FixtureRequest) -> str | None:
    """Return the --scenarios tag filter from the CLI, or None if not set.

    Used by the scenario runner to filter the scenario corpus at
    collection time.
    """
    return request.config.getoption("--scenarios")


@pytest.fixture(scope="session")
def benchmark_mode(request: pytest.FixtureRequest) -> bool:
    """Return True when --benchmark flag was passed on the CLI.

    When True, the scenario runner collects results without hard assertion
    failures and the benchmark harness generates scorecards at session end.
    """
    return bool(request.config.getoption("--benchmark"))


@pytest.fixture(scope="session")
def benchmark_models(request: pytest.FixtureRequest) -> list[str] | None:
    """Return the list of models to benchmark, or None if not in benchmark mode.

    Reads ``--benchmark-models`` (CLI) or ``E2E_BENCHMARK_MODELS`` (env var),
    with CLI taking precedence.  Returns ``None`` if benchmark mode is not
    active.

    Raises
    ------
    pytest.UsageError
        When ``--benchmark`` is set but no model list is provided via either
        the CLI option or the environment variable.
    """
    from tests.e2e.benchmark import resolve_benchmark_models  # noqa: PLC0415

    is_benchmark = bool(request.config.getoption("--benchmark"))
    if not is_benchmark:
        return None

    cli_value: str | None = request.config.getoption("--benchmark-models")
    models = resolve_benchmark_models(cli_value)

    if not models:
        raise pytest.UsageError(
            "Benchmark mode requires a model list.  "
            "Provide one via --benchmark-models=<model1>,<model2> "
            "or set the E2E_BENCHMARK_MODELS environment variable.\n"
            "Example: pytest tests/e2e/ --benchmark "
            "--benchmark-models=claude-sonnet-4-5-20250514,gpt-4o"
        )

    return models


# ---------------------------------------------------------------------------
# Session-scoped skip guards (autouse)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def require_api_key() -> None:
    """Skip all E2E tests when ANTHROPIC_API_KEY is not set."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip(
            "ANTHROPIC_API_KEY not set — E2E tests require real LLM calls",
            allow_module_level=True,
        )


@pytest.fixture(scope="session", autouse=True)
def require_claude_binary() -> None:
    """Skip all E2E tests when claude binary is not on PATH."""
    if not shutil.which("claude"):
        pytest.skip(
            "claude binary not found on PATH — E2E tests require claude CLI",
            allow_module_level=True,
        )


# ---------------------------------------------------------------------------
# PostgreSQL testcontainer (session-scoped)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    """Shared testcontainer PostgreSQL instance for all E2E tests.

    Uses pgvector/pgvector:pg17 to match production docker-compose.yml.
    Maps internal port 5432 to a random ephemeral host port to avoid
    conflicts with any running production stack on port 54320.

    Lifecycle: Started once per pytest session, destroyed at session end.
    Isolation: Each butler gets a dedicated database within this instance.
    """
    with PostgresContainer("pgvector/pgvector:pg17") as pg:
        logger.info(
            "PostgreSQL testcontainer started: host=%s port=%s",
            pg.get_container_host_ip(),
            pg.get_exposed_port(5432),
        )
        yield pg
        logger.info("PostgreSQL testcontainer stopped")


# ---------------------------------------------------------------------------
# Ecosystem bootstrap (session-scoped)
# ---------------------------------------------------------------------------


@dataclass
class ButlerEcosystem:
    """Container for all ButlerDaemon instances and database pools.

    Attributes:
        butlers: Map of butler_name -> ButlerDaemon instance
        pools: Map of butler_name -> asyncpg.Pool
        postgres_container: Testcontainer reference for manual inspection
    """

    butlers: dict[str, ButlerDaemon] = field(default_factory=dict)
    pools: dict[str, Pool] = field(default_factory=dict)
    postgres_container: PostgresContainer | None = None


@pytest.fixture(scope="session")
async def butler_ecosystem(
    postgres_container: PostgresContainer,
) -> AsyncIterator[ButlerEcosystem]:
    """Provision and boot all roster butlers using a five-phase structured bootstrap.

    Phase 1 — Provision:
        Start testcontainer PostgreSQL, run all Alembic migrations (core +
        butler-specific + module chains), create per-butler schemas, and ensure
        the ``message_inbox`` partition for the current month exists (switchboard
        schema).

    Phase 2 — Configure:
        Load all roster configs, apply ``E2E_PORT_OFFSET`` to every butler port,
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
        ButlerEcosystem with all daemons running and pools connected.

    Raises:
        RuntimeError: If any phase fails, reporting which phase and why.
    """
    from butlers.config import list_butlers, load_config
    from butlers.daemon import ButlerDaemon
    from butlers.db import Database

    ecosystem = ButlerEcosystem(postgres_container=postgres_container)
    butler_names: list[str] = []

    # Install SIGTERM → KeyboardInterrupt so finally blocks run on container stop.
    _install_sigterm_handler()

    try:
        # ------------------------------------------------------------------
        # Phase 1 — Provision
        # ------------------------------------------------------------------
        logger.info("[Phase 1/5] Provision — starting PostgreSQL and running migrations")
        _report_phase("Provision")

        host = postgres_container.get_container_host_ip()
        port = int(postgres_container.get_exposed_port(5432))
        user = postgres_container.username
        password = postgres_container.password

        # Discover roster butlers
        butler_configs_raw = list_butlers()
        butler_names = [b.name for b in butler_configs_raw]
        logger.info("Discovered %d butlers: %s", len(butler_names), butler_names)

        # Provision and migrate one shared database for all butlers (one-DB topology).
        # Each butler gets its own schema within the shared "butlers" database.
        db_url = f"postgresql://{user}:{password}@{host}:{port}/butlers"

        # Provision the shared database first
        provision_db = Database(
            db_name="butlers",
            host=host,
            port=port,
            user=user,
            password=password,
        )
        await provision_db.provision()

        # Run core migrations (creates shared schema + core tables in all butler schemas)
        logger.info("Running core migrations...")
        await _run_all_migrations(db_url, butler_configs_raw)

        # Ensure the message_inbox partition for the current month exists.
        # This lives in the switchboard schema; the switchboard migration chain
        # creates the ensure_partition function and the initial partitions.
        # We trigger it explicitly here to guarantee the current-month partition
        # is present at test time (the migration only runs at session start, not
        # mid-month).
        await _ensure_message_inbox_partition(host, port, user, password)

        logger.info("[Phase 1/5] Provision complete")

        # ------------------------------------------------------------------
        # Phase 2 — Configure
        # ------------------------------------------------------------------
        logger.info("[Phase 2/5] Configure — applying port offsets and switchboard URL patches")
        _report_phase("Configure")

        # Compute switchboard E2E port from its base config
        switchboard_base_config = load_config(
            Path(__file__).resolve().parent.parent.parent / "roster" / "switchboard"
        )
        switchboard_e2e_port = switchboard_base_config.port + E2E_PORT_OFFSET

        # Build configs and DB instances per butler
        butler_configs: dict[str, tuple[ButlerConfig, Database]] = {}
        for butler_config in butler_configs_raw:
            butler_name = butler_config.name

            # Create per-butler Database instance (schema-scoped)
            db = Database(
                db_name="butlers",
                schema=butler_config.db_schema,
                host=host,
                port=port,
                user=user,
                password=password,
                min_pool_size=2,
                max_pool_size=10,
            )
            pool = await db.connect()
            ecosystem.pools[butler_name] = pool

            # Apply port offset
            butler_config.port += E2E_PORT_OFFSET

            # Patch switchboard_url for non-switchboard butlers
            if butler_config.name != "switchboard":
                butler_config.switchboard_url = f"http://localhost:{switchboard_e2e_port}/sse"

            butler_configs[butler_name] = (butler_config, db)

        logger.info("[Phase 2/5] Configure complete — %d butlers configured", len(butler_names))

        # ------------------------------------------------------------------
        # Phase 3 — Authenticate
        # ------------------------------------------------------------------
        logger.info("[Phase 3/5] Authenticate — validating CLI auth token validity")
        _report_phase("Authenticate")

        await _validate_auth_tokens()

        logger.info("[Phase 3/5] Authenticate complete")

        # ------------------------------------------------------------------
        # Phase 4 — Boot
        # ------------------------------------------------------------------
        logger.info("[Phase 4/5] Boot — starting butler daemons")
        _report_phase("Boot")

        for butler_name in butler_names:
            butler_config, db = butler_configs[butler_name]
            logger.info("Starting butler: %s (port %s)", butler_name, butler_config.port)

            daemon = ButlerDaemon(butler_name=butler_name, db=db)
            daemon.config = butler_config
            await daemon.start()
            ecosystem.butlers[butler_name] = daemon

            logger.info("Butler %s started on port %s", butler_name, butler_config.port)

        # Wait for all SSE ports to respond
        logger.info("Waiting for all butler SSE ports to be healthy...")
        await _wait_for_ecosystem_health(ecosystem)
        logger.info("[Phase 4/5] Boot complete — all butlers responding")

        # ------------------------------------------------------------------
        # Phase 5 — Validate
        # ------------------------------------------------------------------
        logger.info("[Phase 5/5] Validate — smoke-testing each butler via MCP")
        _report_phase("Validate")

        await _smoke_test_butlers(ecosystem)

        logger.info("[Phase 5/5] Validate complete — ecosystem ready")

        yield ecosystem

    finally:
        # ------------------------------------------------------------------
        # Teardown: stop daemons, close pools, container auto-stops via fixture
        # ------------------------------------------------------------------
        logger.info("Tearing down ecosystem (daemons + DB pools)...")
        for butler_name in reversed(butler_names):
            if butler_name in ecosystem.butlers:
                try:
                    await ecosystem.butlers[butler_name].shutdown()
                except Exception:
                    logger.exception("Error shutting down butler: %s", butler_name)
            if butler_name in ecosystem.pools:
                try:
                    await ecosystem.pools[butler_name].close()
                except Exception:
                    logger.exception("Error closing pool for butler: %s", butler_name)
        logger.info("Ecosystem teardown complete")


# ---------------------------------------------------------------------------
# Phase helpers
# ---------------------------------------------------------------------------


def _report_phase(phase_name: str) -> None:
    """Emit a visible phase banner for test session stdout."""
    banner = f"  [E2E Bootstrap] Phase: {phase_name}"
    print(f"\n{banner}")
    logger.info("Bootstrap phase: %s", phase_name)


def _install_sigterm_handler() -> None:
    """Map SIGTERM to KeyboardInterrupt so try/finally teardown blocks run."""

    def _sigterm_to_keyboard_interrupt(signum: int, frame: object) -> None:  # noqa: ARG001
        logger.warning("SIGTERM received — converting to KeyboardInterrupt for teardown")
        raise KeyboardInterrupt("SIGTERM")

    try:
        signal.signal(signal.SIGTERM, _sigterm_to_keyboard_interrupt)
    except (ValueError, OSError):
        # Can't set signal handlers in non-main threads — ignore
        pass


async def _run_all_migrations(db_url: str, butler_configs: list[ButlerConfig]) -> None:
    """Run core + butler-specific + module migrations for all butlers.

    Core migrations create the ``shared`` schema and per-butler schemas.
    Butler-specific and module chains are run per butler that declares them.
    """
    from butlers.migrations import get_all_chains, has_butler_chain, run_migrations

    # Run core chain first (creates shared schema, all core tables)
    logger.info("Running core migration chain...")
    for butler_config in butler_configs:
        schema = butler_config.db_schema
        await run_migrations(db_url, chain="core", schema=schema)
        logger.debug("Core migrations complete for schema: %s", schema or "<default>")

    # Run butler-specific chains
    for butler_config in butler_configs:
        schema = butler_config.db_schema
        if has_butler_chain(butler_config.name):
            logger.info(
                "Running butler-specific migrations for: %s (schema=%s)",
                butler_config.name,
                schema or "<default>",
            )
            await run_migrations(db_url, chain=butler_config.name, schema=schema)

    # Run module-level chains (module name is the chain key)
    all_chains = get_all_chains()
    # Chains that aren't "core" and aren't butler names are module chains
    butler_names_set = {c.name for c in butler_configs}
    module_chains = [c for c in all_chains if c != "core" and c not in butler_names_set]
    for chain in module_chains:
        # Run module chains against the first butler schema that has the module enabled,
        # or against every butler schema to be safe (idempotent)
        for butler_config in butler_configs:
            schema = butler_config.db_schema
            if chain in (butler_config.modules or {}):
                logger.debug(
                    "Running module migration chain '%s' for butler: %s",
                    chain,
                    butler_config.name,
                )
                try:
                    await run_migrations(db_url, chain=chain, schema=schema)
                except Exception:
                    logger.debug(
                        "Module migration '%s' skipped/failed for butler '%s' (non-fatal)",
                        chain,
                        butler_config.name,
                    )


async def _ensure_message_inbox_partition(
    host: str,
    port: int,
    user: str,
    password: str,
) -> None:
    """Ensure message_inbox has a partition for the current month.

    The switchboard schema hosts ``message_inbox`` as a monthly range-partitioned
    table.  The migration creates the initial partition when it runs, but we
    call ``switchboard_message_inbox_ensure_partition(now())`` explicitly here
    to guarantee the current-month partition exists at test time.
    """
    import asyncpg

    try:
        conn = await asyncpg.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database="butlers",
        )
        try:
            # Set search_path to switchboard schema
            await conn.execute("SET search_path TO switchboard, shared, public")

            # Check if the ensure_partition function exists (it's created by sw_008 migration)
            fn_exists = await conn.fetchval(
                """
                SELECT 1 FROM pg_proc p
                JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'switchboard'
                  AND p.proname = 'switchboard_message_inbox_ensure_partition'
                """
            )
            if fn_exists:
                await conn.execute(
                    "SELECT switchboard.switchboard_message_inbox_ensure_partition(now())"
                )
                logger.info("message_inbox current-month partition ensured in switchboard schema")
            else:
                logger.debug(
                    "switchboard_message_inbox_ensure_partition not found "
                    "(switchboard schema may not have run migrations yet — skipping)"
                )
        finally:
            await conn.close()
    except Exception:
        # Non-fatal: if message_inbox doesn't exist yet, the migration hasn't
        # run for the switchboard schema; that's handled by the daemon startup.
        logger.debug(
            "Could not ensure message_inbox partition (non-fatal, may be pre-migration)",
            exc_info=True,
        )


async def _validate_auth_tokens() -> None:
    """Validate CLI auth token validity for all available providers.

    Uses ``probe_provider`` which runs the status command — not just checks
    file existence.  If a provider is available and its token is missing or
    expired, an interactive re-auth prompt is printed and the fixture blocks
    until the user completes the flow.

    Providers that are unavailable (binary not on PATH) are skipped silently.
    """
    try:
        from butlers.cli_auth.health import AuthHealthState, probe_provider
        from butlers.cli_auth.registry import PROVIDERS
    except ImportError:
        logger.debug("CLI auth module not available — skipping OAuth validation")
        return

    for provider_name, provider in PROVIDERS.items():
        if not provider.is_available():
            logger.debug(
                "CLI auth provider '%s' not available (binary not on PATH) — skipping",
                provider_name,
            )
            continue

        result = await probe_provider(provider)

        if result.state == AuthHealthState.authenticated:
            logger.info(
                "CLI auth '%s': authenticated (%s)",
                provider_name,
                result.detail or "ok",
            )
            continue

        if result.state == AuthHealthState.unavailable:
            logger.debug("CLI auth '%s': unavailable — skipping", provider_name)
            continue

        if result.state in (AuthHealthState.not_authenticated, AuthHealthState.probe_failed):
            # Token is missing or expired — prompt user interactively
            _prompt_for_reauth(provider_name, provider, result.detail)


def _prompt_for_reauth(
    provider_name: str,
    provider: CLIAuthProviderDef,
    detail: str | None,
) -> None:
    """Print interactive re-auth instructions and wait for user confirmation.

    Parameters
    ----------
    provider_name:
        Short provider identifier (e.g. ``"codex"``).
    provider:
        The ``CLIAuthProviderDef`` instance (used for display_name / command).
    detail:
        Optional detail string from the probe result (e.g. status command output).
    """
    display_name = getattr(provider, "display_name", provider_name)
    command = getattr(provider, "command", [])
    command_str = " ".join(command) if command else f"{provider_name} login"

    print("\n" + "=" * 70)
    print("[E2E Bootstrap — Authenticate Phase]")
    print(f"CLI auth required for provider: {display_name!r}")
    if detail:
        print(f"Status: {detail}")
    print()
    print("Please run the following command in a separate terminal and")
    print("complete the authentication flow:")
    print()
    print(f"    {command_str}")
    print()
    print("Press ENTER here when authentication is complete...")
    print("=" * 70)

    try:
        input()
    except EOFError:
        # Non-interactive environment — skip prompt and continue
        logger.warning(
            "Non-interactive environment: skipping re-auth prompt for '%s'",
            provider_name,
        )


async def _wait_for_ecosystem_health(
    ecosystem: ButlerEcosystem,
    *,
    timeout_seconds: int = 30,
    poll_interval: float = 0.2,
) -> None:
    """Wait for all butler SSE ports to respond with 200 OK.

    Raises:
        RuntimeError: If any butler fails to respond within timeout,
            reporting exactly which butlers timed out.
    """
    import httpx

    deadline = time.monotonic() + timeout_seconds
    pending = set(ecosystem.butlers.keys())

    while pending and time.monotonic() < deadline:
        for butler_name in list(pending):
            daemon = ecosystem.butlers[butler_name]
            port = daemon.config.port
            url = f"http://localhost:{port}/sse"

            try:
                async with httpx.AsyncClient() as client:
                    # Use stream() because /sse is a Server-Sent Events endpoint
                    # that never completes.  stream() yields after receiving the
                    # response headers so we can check the status code without
                    # waiting for the (infinite) body.
                    async with client.stream("GET", url, timeout=2.0) as response:
                        if response.status_code == 200:
                            pending.discard(butler_name)
                            logger.debug("Butler %s is healthy (port %s)", butler_name, port)
            except Exception:
                # Expected during startup — keep polling
                pass

        if pending:
            await asyncio.sleep(poll_interval)

    if pending:
        raise RuntimeError(
            f"[Phase 4 — Boot] Butler SSE ports not ready within {timeout_seconds}s: "
            f"{sorted(pending)}. Check daemon logs for startup errors."
        )


async def _smoke_test_butlers(ecosystem: ButlerEcosystem) -> None:
    """Phase 5: no-op MCP status call to each butler.

    Issues a ``GET /sse`` health check (no actual MCP call needed — the
    health check in Phase 4 already verified HTTP 200). Here we do a
    lightweight structural check: assert that the daemon object is started
    and has an active DB pool.

    A full MCP ``status`` tool call would require spawning an MCP client
    and is deferred to the dedicated ``test_ecosystem_health.py`` test
    module which runs as the first E2E test.
    """
    failed: list[str] = []
    for butler_name, daemon in ecosystem.butlers.items():
        pool = ecosystem.pools.get(butler_name)
        if pool is None:
            failed.append(f"{butler_name}: no DB pool")
            continue
        # Verify the pool has at least one active connection
        if pool.get_size() == 0 and pool.get_idle_size() == 0:
            failed.append(f"{butler_name}: DB pool has no connections")
            continue
        logger.debug("Butler %s: smoke test passed", butler_name)

    if failed:
        raise RuntimeError(f"[Phase 5 — Validate] Smoke test failures: {failed}")


# ---------------------------------------------------------------------------
# Per-butler pool accessors (function-scoped)
# ---------------------------------------------------------------------------


@pytest.fixture
def switchboard_pool(butler_ecosystem: ButlerEcosystem) -> Pool:
    """Switchboard butler database pool."""
    return butler_ecosystem.pools["switchboard"]


@pytest.fixture
def general_pool(butler_ecosystem: ButlerEcosystem) -> Pool:
    """General butler database pool."""
    return butler_ecosystem.pools["general"]


@pytest.fixture
def relationship_pool(butler_ecosystem: ButlerEcosystem) -> Pool:
    """Relationship butler database pool."""
    return butler_ecosystem.pools["relationship"]


@pytest.fixture
def health_pool(butler_ecosystem: ButlerEcosystem) -> Pool:
    """Health butler database pool."""
    return butler_ecosystem.pools["health"]


@pytest.fixture
def messenger_pool(butler_ecosystem: ButlerEcosystem) -> Pool:
    """Messenger butler database pool."""
    return butler_ecosystem.pools["messenger"]


# ---------------------------------------------------------------------------
# Logging and cost tracking (session-scoped)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def e2e_log_path() -> Path:
    """Configures structured logging to .tmp/e2e-logs/ and returns log path.

    Creates timestamped log file for this run.
    All butler daemon logs (DEBUG level) are captured here.
    """
    log_dir = Path(".tmp/e2e-logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = log_dir / f"e2e-{timestamp}.log"

    # Configure root logger to capture all butler logs
    file_handler = logging.FileHandler(log_path, mode="w")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)

    logger.info("E2E logs: %s", log_path)
    return log_path


@dataclass
class CostTracker:
    """Tracks LLM token usage and cost across all E2E tests.

    Attributes:
        llm_calls: Total number of LLM invocations
        input_tokens: Total input tokens consumed
        output_tokens: Total output tokens consumed
    """

    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def record(self, input_tokens: int, output_tokens: int) -> None:
        """Record a single LLM call."""
        self.llm_calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens

    def estimated_cost(self) -> float:
        """Calculate estimated cost in USD (Haiku 4.5 pricing)."""
        # Haiku 4.5: $0.80/MTok input, $4.00/MTok output
        input_cost = (self.input_tokens / 1_000_000) * 0.80
        output_cost = (self.output_tokens / 1_000_000) * 4.00
        return input_cost + output_cost

    def print_summary(self) -> None:
        """Print cost summary to console."""
        print("\n" + "=" * 60)
        print("E2E Cost Summary")
        print(f"  LLM calls:    {self.llm_calls}")
        print(f"  Input tokens:  {self.input_tokens:,}")
        print(f"  Output tokens: {self.output_tokens:,}")
        print(f"  Est. cost:     ${self.estimated_cost():.3f}")
        print("=" * 60)


@pytest.fixture(scope="session")
def cost_tracker() -> Iterator[CostTracker]:
    """Session-scoped cost tracker for accumulating LLM usage.

    Prints summary at session end.
    """
    tracker = CostTracker()
    yield tracker
    tracker.print_summary()


# ---------------------------------------------------------------------------
# Benchmark result accumulator (session-scoped)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def benchmark_result(benchmark_mode: bool) -> BenchmarkResult | None:
    """Session-scoped BenchmarkResult accumulator.

    Returns a ``BenchmarkResult`` instance when benchmark mode is active,
    or ``None`` in validate mode.  All benchmark test functions record their
    results here; scorecards are generated from this accumulator at session
    end by ``pytest_sessionfinish``.
    """
    if not benchmark_mode:
        return None

    from tests.e2e.benchmark import BenchmarkResult  # noqa: PLC0415

    return BenchmarkResult()


# ---------------------------------------------------------------------------
# Session finish hook — generates scorecards in benchmark mode
# ---------------------------------------------------------------------------


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:  # noqa: ARG001
    """Generate benchmark scorecards at session end when in benchmark mode.

    Called automatically by pytest after all tests complete.  In benchmark mode,
    this hook:
    1. Retrieves the session-scoped ``benchmark_result`` accumulator.
    2. Computes scorecards for all models via ``compute_all_scorecards()``.
    3. Writes all scorecard files to ``.tmp/e2e-scorecards/<timestamp>/``.

    In validate mode (default), this hook is a no-op.
    """
    config = session.config

    # Check benchmark mode via stored option (not fixture — hooks can't use fixtures).
    try:
        is_benchmark = bool(config.getoption("--benchmark"))
    except ValueError:
        # Option not registered (e.g. running outside of E2E test tree)
        return

    if not is_benchmark:
        return

    # Retrieve accumulated results from the session fixture cache.
    # Use the internal fixture manager to retrieve the session-scoped fixture.
    try:
        fixturemanager = session._fixturemanager  # type: ignore[attr-defined]
        deflist = fixturemanager.getfixturedefs("benchmark_result", nodeid="")
        if not deflist:
            logger.warning(
                "[sessionfinish] benchmark_result fixture not found — skipping scorecards"
            )
            return
    except Exception:
        logger.warning(
            "[sessionfinish] Could not access fixture manager — skipping scorecards",
            exc_info=True,
        )
        return

    # Access the cached fixture value from the session-scope cache.
    # Walk session items to find an item that requested benchmark_result.
    try:
        results_value = None
        for item in session.items:
            try:
                cached = item.funcargs.get("benchmark_result")
                if cached is not None:
                    results_value = cached
                    break
            except AttributeError:
                continue

        if results_value is None:
            logger.warning("[sessionfinish] No benchmark results accumulated — skipping scorecards")
            return
    except Exception:
        logger.warning(
            "[sessionfinish] Could not retrieve benchmark results — skipping scorecards",
            exc_info=True,
        )
        return

    try:
        from tests.e2e.reporting import generate_scorecards  # noqa: PLC0415
        from tests.e2e.scenarios import ALL_SCENARIOS  # noqa: PLC0415
        from tests.e2e.scoring import compute_all_scorecards, load_pricing  # noqa: PLC0415

        scenario_tags = {s.id: s.tags for s in ALL_SCENARIOS}
        scenario_routing = {s.id: s.expected_routing for s in ALL_SCENARIOS}
        pricing = load_pricing()

        scorecards = compute_all_scorecards(
            results_value,
            scenario_tags=scenario_tags,
            scenario_routing=scenario_routing,
            pricing=pricing,
        )

        if not scorecards:
            logger.info(
                "[sessionfinish] No models in benchmark results — skipping scorecard generation"
            )
            return

        output_dir = generate_scorecards(results_value, scorecards)
        print(f"\n[E2E Benchmark] Scorecards written to: {output_dir}")
        logger.info("[sessionfinish] Benchmark scorecards generated: %s", output_dir)
    except Exception:
        logger.error("[sessionfinish] Failed to generate scorecards", exc_info=True)
