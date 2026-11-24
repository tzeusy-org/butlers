#!/usr/bin/env python3
"""Read-only smoke check of raw OwnTracks expiry (bu-s11n0s.75).

Prints a count-only JSON report and exits non-zero when something is wrong:

* the policy row is missing;
* the hourly ``chronicler_location_retention`` run is failing;
* expiry is on and no run started within :data:`RUN_STALE_AFTER`;
* expiry is off and a grant with rows was prepared after the switch last changed;
* a grant failed reconciliation.

Everything runs in one read-only, repeatable-read transaction on a session whose transactions
default to read-only. Nothing about coordinates, devices or the owner is read or printed:
only the policy, run bookkeeping and counts.

The DSN is read from stdin (never argv, so it stays out of the process list and shell
history), e.g. ``python scripts/location_retention_smoke.py < dsn-file``.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta
from typing import Any

#: A run older than this while expiry is on means the schedule is not running.
RUN_STALE_AFTER = timedelta(hours=2)

_POLICY = """
SELECT days, version, enforced, updated_at
FROM chronicler.location_retention_policy WHERE id = 1
"""
_RUN = """
SELECT last_outcome, last_started_at, last_success_at, consecutive_failures, last_error_class
FROM chronicler.location_retention_runs WHERE id = 1
"""
_GRANTS = """
SELECT COALESCE(latest.state, 'prepared') AS state, count(*) AS grants,
       COALESCE(sum(g.item_count), 0)::bigint AS items
FROM chronicler.location_retention_grants AS g
LEFT JOIN LATERAL (
    SELECT s.state FROM chronicler.location_retention_grant_states AS s
    WHERE s.grant_id = g.id ORDER BY s.id DESC LIMIT 1
) AS latest ON true
GROUP BY 1 ORDER BY 1
"""
_PREPARED_SINCE = """
SELECT count(*) FROM chronicler.location_retention_grants
WHERE item_count > 0 AND prepared_at > $1
"""
_OVERDUE = """
SELECT count(*) FROM connectors.owntracks_points
WHERE retention_at < now() - make_interval(days => $1)
"""
_CONDITIONS = """
SELECT source, count(*) AS open FROM public.owner_conditions
WHERE source LIKE 'chronicler:location-retention-%' AND state IN ('open', 'aging')
GROUP BY source ORDER BY source
"""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


async def collect(conn: Any) -> dict[str, Any]:
    """One consistent, read-only snapshot of the retention state, counts only."""
    async with conn.transaction(isolation="repeatable_read", readonly=True):
        now = await conn.fetchval("SELECT now()")
        policy = await conn.fetchrow(_POLICY)
        run = await conn.fetchrow(_RUN)
        grants = await conn.fetch(_GRANTS)
        conditions = await conn.fetch(_CONDITIONS)
        prepared_since = overdue = None
        if policy is not None:
            prepared_since = await conn.fetchval(_PREPARED_SINCE, policy["updated_at"])
            overdue = await conn.fetchval(_OVERDUE, policy["days"])
    return {
        "now": now,
        "policy": None
        if policy is None
        else {
            "days": policy["days"],
            "version": policy["version"],
            "enforced": policy["enforced"],
            "updated_at": policy["updated_at"],
        },
        "run": None if run is None else dict(run),
        "grants": {
            row["state"]: {"grants": row["grants"], "items": row["items"]} for row in grants
        },
        "grants_with_rows_since_switch": prepared_since,
        "overdue_raw_fixes": overdue,
        "open_conditions": {row["source"]: row["open"] for row in conditions},
    }


def problems(report: dict[str, Any]) -> list[str]:
    """What is wrong in a :func:`collect` report; empty when healthy."""
    policy = report["policy"]
    if policy is None:
        return ["policy_missing"]
    found = []
    run = report["run"]
    if run is not None and run["consecutive_failures"]:
        found.append(f"run_failing:{run['last_error_class']}")
    if policy["enforced"]:
        started = None if run is None else run["last_started_at"]
        if started is None or report["now"] - started > RUN_STALE_AFTER:
            found.append("run_stale")
    elif report["grants_with_rows_since_switch"]:
        found.append("granted_while_off")
    if report["grants"].get("failed"):
        found.append("grant_failed")
    return found


def render(report: dict[str, Any]) -> str:
    def default(value: Any) -> Any:
        if isinstance(value, datetime):
            return _iso(value)
        raise TypeError(type(value).__name__)

    return json.dumps(
        {**report, "problems": problems(report)}, default=default, indent=2, sort_keys=True
    )


async def _main(dsn: str) -> int:
    import asyncpg

    conn = await asyncpg.connect(
        dsn, server_settings={"default_transaction_read_only": "on"}, timeout=30
    )
    try:
        report = await collect(conn)
    finally:
        await conn.close()
    print(render(report))
    return 1 if problems(report) else 0


def main() -> int:
    dsn = sys.stdin.readline().strip()
    if not dsn:
        print("location_retention_smoke: pass the DSN on stdin", file=sys.stderr)
        return 2
    return asyncio.run(_main(dsn))


if __name__ == "__main__":
    sys.exit(main())
