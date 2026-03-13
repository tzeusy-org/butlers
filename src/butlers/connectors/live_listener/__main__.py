"""Allow ``python -m butlers.connectors.live_listener`` invocation."""

import asyncio

from butlers.connectors.live_listener.connector import run_connector

asyncio.run(run_connector())
