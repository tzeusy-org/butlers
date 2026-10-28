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
    - Switchboard:  http://localhost:41100/sse
    - General:      http://localhost:41101/sse
    - Relationship: http://localhost:41102/sse
    - Health:       http://localhost:41103/sse
    - Messenger:    http://localhost:41104/sse

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
        const res = http.post('http://localhost:41100/sse', JSON.stringify({
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
from unittest.mock import patch

from testcontainers.postgres import PostgresContainer

from butlers.config import list_butlers
from butlers.daemon import ButlerDaemon
from butlers.testing.migration import async_serving_test_deployment

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
    logger.info("Starting PostgreSQL testcontainer...")
    pg = PostgresContainer("pgvector/pgvector:pg17")
    pg.start()

    try:
        async with async_serving_test_deployment(pg) as deployment:
            with patch.dict(
                os.environ,
                {"BUTLERS_CREDENTIAL_DATABASE_URL": deployment.purpose_url("credential")},
            ):
                # Actual source profiles/full held chains/finalizer are ready.
                # Every daemon receives its own dedicated native serving login.
                configs = list_butlers()
                butler_names = [config.name for config in configs]
                logger.info("Discovered %d butlers: %s", len(butler_names), butler_names)
                butlers = {}
                for config in configs:
                    db = deployment.runtime_database(config.db_schema, min_size=2, max_size=10)
                    await db.connect()
                    daemon = ButlerDaemon(butler_name=config.name, db=db)
                    daemon.config = config
                    deployment.retain(daemon, "shutdown")
                    await daemon.start()
                    butlers[config.name] = daemon
                    logger.info("Butler %s started on port %s", config.name, config.port)

                print("\n" + "=" * 60)
                print("Ecosystem running. Press Ctrl+C to stop.")
                print("=" * 60)
                for butler_name in sorted(butler_names):
                    daemon = butlers[butler_name]
                    print(f"  {butler_name:12} http://localhost:{daemon.config.port}/sse")
                print("=" * 60 + "\n")
                try:
                    await asyncio.Event().wait()
                except KeyboardInterrupt:
                    logger.info("Received interrupt signal, shutting down...")
                logger.info("Shutting down ecosystem...")
            # The owned context joins partial startups and every native pool
            # before revoking disposable memberships or stopping the server.
        logger.info("Ecosystem shutdown complete")

    finally:
        logger.info("Stopping PostgreSQL testcontainer...")
        pg.stop()
        logger.info("PostgreSQL testcontainer stopped")


if __name__ == "__main__":
    asyncio.run(main())
