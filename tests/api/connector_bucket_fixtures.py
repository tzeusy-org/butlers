"""Software-only transaction adapter for existing connector-summary fixtures.

It consumes the original registry/count/device/cadence fetch sequence without
pretending to enforce SQL locks, catalog authority or receiver recording.
"""

import datetime as dt
from unittest.mock import AsyncMock, MagicMock


def attach_bucket_reader(pool):
    connection = AsyncMock()
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=connection)
    context.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=context)
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=None)
    transaction.__aexit__ = AsyncMock(return_value=None)
    connection.transaction = MagicMock(return_value=transaction)
    end = dt.datetime.now(dt.UTC).replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
    connection.fetchval.side_effect = lambda sql, *args: end if "clock_timestamp" in sql else None

    async def fetch(sql, *args):
        if "FOR UPDATE" in sql or " AS observed" in sql:
            return []
        source = await pool.fetch(sql, *args)
        combined = {}
        for row in source:
            key = (row["connector_type"], row["endpoint_identity"], row["hour_bucket"])
            value = combined.setdefault(
                key,
                {
                    "connector_type": key[0],
                    "endpoint_identity": key[1],
                    "bucket": key[2],
                    "messages_ingested": 0,
                    "messages_failed": 0,
                    "messages_filtered": 0,
                },
            )
            value["messages_ingested" if row["source"] == "ingested" else "messages_filtered"] += (
                row["event_count"]
            )
        return list(combined.values())

    connection.fetch.side_effect = fetch
