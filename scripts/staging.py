#!/usr/bin/env python3
"""Hold the E2E ecosystem open indefinitely for interactive load testing.

This script boots the full butler ecosystem (all roster butlers on their
configured SSE ports) and keeps it running until Ctrl+C is pressed.

Use this for interactive load testing with external tools like k6, locust,
wrk, or hey. The ecosystem is identical to the one provisioned by the
butler_ecosystem fixture in tests/e2e/conftest.py.

Usage:
    uv run python scripts/staging.py

Ports (from roster/*/butler.toml):
    - Switchboard: http://localhost:8100/sse
    - Health:      http://localhost:8101/sse
    - General:     http://localhost:8102/sse
    - Heartbeat:   http://localhost:8103/sse
    - Relationship: http://localhost:8104/sse
    - Messenger:   http://localhost:8105/sse

Example k6 script (save as load-test.js):
    import http from 'k6/http';
    import { check, sleep } from 'k6';

    export const options = {
        stages: [
            { duration: '30s', target: 5 },
            { duration: '1m', target: 5 },
            { duration: '30s', target: 0 },
        ],
    };

    export default function () {
        const res = http.post('http://localhost:8100/sse', JSON.stringify({
            method: 'tools/call',
            params: { name: 'status', arguments: {} },
        }), { headers: { 'Content-Type': 'application/json' } });

        check(res, {
            'status is 200': (r) => r.status === 200,
            'response time < 500ms': (r) => r.timings.duration < 500,
        });

        sleep(1);
    }

Run with:
    k6 run --vus 10 --duration 30s load-test.js
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from testcontainers.postgres import PostgresContainer

from butlers.config import list_butlers
from butlers.daemon import ButlerDaemon
from butlers.db import Database

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    """Boot the ecosystem and hold it open until interrupted."""
    # Check for API key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.error("ANTHROPIC_API_KEY not set — ecosystem requires real LLM calls")
        sys.exit(1)

    logger.info("Starting PostgreSQL testcontainer...")
    pg = PostgresContainer("pgvector/pgvector:pg17")
    pg.start()

    try:
        host = pg.get_container_host_ip()
        port = int(pg.get_exposed_port(5432))
        user = pg.username
        password = pg.password

        logger.info(
            "PostgreSQL testcontainer started: host=%s port=%s",
            host,
            port,
        )

        # Discover all roster butlers
        butler_names = [b.name for b in list_butlers()]
        logger.info("Discovered %d butlers: %s", len(butler_names), butler_names)

        butlers = {}
        pools = {}

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
            pools[butler_name] = pool

            # Initialize and start daemon
            daemon = ButlerDaemon(butler_name=butler_name, db=db)
            await daemon.start()
            butlers[butler_name] = daemon

            logger.info(
                "Butler %s started on port %s",
                butler_name,
                daemon.config.butler.port,
            )

        # Print endpoints
        print("\n" + "=" * 60)
        print("Ecosystem running. Press Ctrl+C to stop.")
        print("=" * 60)
        for butler_name in sorted(butler_names):
            daemon = butlers[butler_name]
            port = daemon.config.butler.port
            print(f"  {butler_name:12} http://localhost:{port}/sse")
        print("=" * 60 + "\n")

        # Block forever until Ctrl+C
        try:
            await asyncio.Event().wait()
        except KeyboardInterrupt:
            logger.info("Received interrupt signal, shutting down...")

        # Graceful shutdown in reverse order
        logger.info("Shutting down ecosystem...")
        for butler_name in reversed(butler_names):
            if butler_name in butlers:
                await butlers[butler_name].shutdown()
            if butler_name in pools:
                await pools[butler_name].close()
        logger.info("Ecosystem shutdown complete")

    finally:
        logger.info("Stopping PostgreSQL testcontainer...")
        pg.stop()
        logger.info("PostgreSQL testcontainer stopped")


if __name__ == "__main__":
    asyncio.run(main())
