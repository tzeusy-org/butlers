"""E2E test fixtures — provisions the complete butler ecosystem.

Session-scoped fixtures:
- require_api_key: Skips all E2E tests when ANTHROPIC_API_KEY is not set
- require_claude_binary: Skips all E2E tests when claude binary is not on PATH
- postgres_container: Shared testcontainer PostgreSQL instance
- butler_ecosystem: Boots all roster butlers as ButlerDaemon instances
- e2e_log_path: Configures structured logging to .tmp/e2e-logs/
- cost_tracker: Accumulates token usage and prints summary at session end

Function-scoped fixtures:
- switchboard_pool, health_pool, etc.: Per-butler database pool accessors
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from testcontainers.postgres import PostgresContainer

if TYPE_CHECKING:
    from asyncpg.pool import Pool

    from butlers.daemon import ButlerDaemon

logger = logging.getLogger(__name__)


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
    """Provision and boot all roster butlers in the E2E ecosystem.

    Auto-discovers all butlers from roster/ directory, provisions their
    databases, runs migrations, and starts their ButlerDaemon instances
    on the configured SSE ports.

    Health check: Waits for all SSE ports to respond before yielding.

    Lifecycle:
    1. Discover roster butlers
    2. For each butler:
       a. Create Database instance with testcontainer connection params
       b. Provision database (CREATE DATABASE IF NOT EXISTS)
       c. Run core + module migrations
       d. Initialize ButlerDaemon
       e. Start daemon (FastMCP SSE server on configured port)
    3. Wait for all ports to be healthy (HTTP 200 on /sse)
    4. Yield ecosystem
    5. Shutdown all daemons in reverse order
    6. Close all database pools

    Yields:
        ButlerEcosystem with all daemons running and pools connected.
    """
    from butlers.config import list_butlers, load_config
    from butlers.daemon import ButlerDaemon
    from butlers.db import Database

    ecosystem = ButlerEcosystem(postgres_container=postgres_container)

    # Port offset so e2e tests don't collide with production daemons or
    # infrastructure (40100→51100).  9100 is taken by Prometheus node_exporter.
    E2E_PORT_OFFSET = 11000

    # Connection params from testcontainer
    host = postgres_container.get_container_host_ip()
    port = int(postgres_container.get_exposed_port(5432))
    user = postgres_container.username
    password = postgres_container.password

    # Discover all roster butlers
    butler_names = [b.name for b in list_butlers()]
    logger.info("Discovered %d butlers: %s", len(butler_names), butler_names)

    # Pre-compute switchboard e2e port for switchboard_url patching.
    # Load switchboard config to read its base port rather than hardcoding.
    switchboard_base_config = load_config(Path("roster") / "switchboard")
    switchboard_e2e_port = switchboard_base_config.port + E2E_PORT_OFFSET

    # Bootstrap each butler
    for butler_name in butler_names:
        logger.info("Bootstrapping butler: %s", butler_name)

        # Create and provision database
        db = Database(
            db_name=f"butler_{butler_name}",
            host=host,
            port=port,
            user=user,
            password=password,
            min_pool_size=2,
            max_pool_size=10,
        )
        await db.provision()
        pool = await db.connect()
        ecosystem.pools[butler_name] = pool

        # Initialize daemon and pre-load config with offset port
        daemon = ButlerDaemon(butler_name=butler_name, db=db)
        config = load_config(daemon.config_dir)
        config.port += E2E_PORT_OFFSET
        if config.name != "switchboard":
            config.switchboard_url = f"http://localhost:{switchboard_e2e_port}/sse"
        daemon.config = config

        await daemon.start()
        ecosystem.butlers[butler_name] = daemon

        logger.info(
            "Butler %s started on port %s",
            butler_name,
            daemon.config.port,
        )

    # Health check: wait for all SSE ports to respond
    logger.info("Waiting for all butler SSE ports to be healthy...")
    await _wait_for_ecosystem_health(ecosystem)
    logger.info("Ecosystem health check passed — all butlers responding")

    yield ecosystem

    # Graceful shutdown in reverse order
    logger.info("Shutting down ecosystem...")
    for butler_name in reversed(butler_names):
        if butler_name in ecosystem.butlers:
            await ecosystem.butlers[butler_name].shutdown()
        if butler_name in ecosystem.pools:
            await ecosystem.pools[butler_name].close()
    logger.info("Ecosystem shutdown complete")


async def _wait_for_ecosystem_health(
    ecosystem: ButlerEcosystem,
    *,
    timeout_seconds: int = 10,
    poll_interval: float = 0.2,
) -> None:
    """Wait for all butler SSE ports to respond with 200 OK.

    Raises:
        TimeoutError: If any butler fails to respond within timeout.
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
                            pending.remove(butler_name)
                            logger.debug("Butler %s is healthy (port %s)", butler_name, port)
            except Exception:
                # Expected during startup — keep polling
                pass

        if pending:
            await asyncio.sleep(poll_interval)

    if pending:
        raise TimeoutError(
            f"Butler SSE ports not ready within {timeout_seconds}s: {sorted(pending)}"
        )


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
