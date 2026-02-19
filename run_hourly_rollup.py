#!/usr/bin/env python3
"""Run the hourly connector statistics rollup job."""

import asyncio
import logging
import sys
from datetime import UTC, datetime

from src.butlers.db import Database
from roster.switchboard.jobs import run_connector_stats_hourly_rollup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def main():
    """Run the hourly connector statistics rollup job."""
    logger.info("Starting hourly connector statistics rollup job...")
    logger.info("Current time (UTC): %s", datetime.now(UTC).isoformat())

    # Create database connection
    db = Database.from_env("switchboard")

    try:
        logger.info("Connecting to Switchboard database...")
        pool = await db.connect()
        logger.info("Connected successfully")

        # Run the hourly rollup
        logger.info("Running hourly rollup...")
        result = await run_connector_stats_hourly_rollup(pool)

        logger.info("Rollup completed successfully!")
        logger.info("Results: %s", result)

        return 0

    except Exception as e:
        logger.error("Error running hourly rollup: %s", e, exc_info=True)
        return 1

    finally:
        await db.close()
        logger.info("Database connection closed")


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
