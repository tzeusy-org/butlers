"""Child-process producers for fleet-event transport integration coverage.

This module is intentionally invoked with ``python -m`` from
``test_fleet_events_notify_bridge.py``.  It must retain real Calendar and
Chronicler publish paths: mocks would collapse the OS-process boundary the
parent test is intended to prove.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from types import SimpleNamespace

import asyncpg

from butlers.chronicler.adapters.base import AdapterResult, ProjectionAdapter
from butlers.chronicler.jobs import _run_adapter
from butlers.chronicler.models import PointEvent
from butlers.chronicler.storage import upsert_point_event
from butlers.db import register_jsonb_codec
from butlers.modules.calendar import CalendarModule

_DATABASE_URL_ENV = "BUTLERS_FLEET_EVENTS_TEST_DATABASE_URL"


class _DurableProofAdapter(ProjectionAdapter):
    """Minimal real projection used to drive Chronicler's scheduled handler.

    The adapter writes a canonical point-event row before returning material
    counts.  ``_run_adapter`` then performs its normal source/checkpoint work
    and publishes the production ``chronicles`` freshness envelope.
    """

    def __init__(self) -> None:
        super().__init__("core.sessions")

    async def project(
        self,
        pool: asyncpg.Pool,
        *,
        chronicler_pool: asyncpg.Pool,
        since: datetime | None,
        since_id: int | None = None,
    ) -> AdapterResult:
        del pool, since, since_id
        await upsert_point_event(
            chronicler_pool,
            PointEvent(
                source_name=self.source_name,
                source_ref=f"fleet-event-transport-proof:{os.getpid()}",
                event_type="fleet_event_transport_proof",
                occurred_at=datetime.now(UTC),
                title="fleet event transport proof",
            ),
        )
        return AdapterResult(
            source_name=self.source_name,
            rows_projected=1,
            point_events=1,
        )


async def _publish_calendar(pool: asyncpg.Pool) -> None:
    calendar = CalendarModule()
    calendar._db = SimpleNamespace(pool=pool)
    await calendar._publish_calendar_fleet_event(
        kind="provider_projection",
        data={"updated_events": 1, "cancelled_events": 0},
    )


async def _publish_chronicler(pool: asyncpg.Pool) -> None:
    await _run_adapter(db_pool=pool, adapter=_DurableProofAdapter())


async def _run(producer: str, database_url: str) -> None:
    pool = await asyncpg.create_pool(
        database_url,
        min_size=1,
        max_size=1,
        init=register_jsonb_codec,
    )
    try:
        if producer == "calendar":
            await _publish_calendar(pool)
        else:
            await _publish_chronicler(pool)
    finally:
        await pool.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("producer", choices=("calendar", "chronicler"))
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    database_url = os.environ.get(_DATABASE_URL_ENV)
    if not database_url:
        raise RuntimeError(f"{_DATABASE_URL_ENV} must be set")

    asyncio.run(_run(args.producer, database_url))
    print(json.dumps({"producer": args.producer, "pid": os.getpid()}))


if __name__ == "__main__":
    main()
