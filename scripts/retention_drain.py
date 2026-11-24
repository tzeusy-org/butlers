#!/usr/bin/env python3
"""Read-only, fixed-cutoff Switchboard retention inventory. DSN enters stdin only."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import stat
import time
from datetime import datetime
from pathlib import Path
from uuid import UUID

from butlers.modules.memory.retention_drain import Plan, Refused, dry_run
from scripts.repair_attention_ledger_metadata import (
    Refused as ConnectionRefused,
)
from scripts.repair_attention_ledger_metadata import (
    connection_options,
    open_database,
    read_connection,
)


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Refused("plan_invalid")
        result[key] = value
    return result


def load_plan(path: Path) -> Plan:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
            ):
                raise Refused("plan_invalid")
            raw = stream.read(8193)
        if len(raw) > 8192:
            raise Refused("plan_invalid")
        data = json.loads(raw, object_pairs_hook=unique)
        data["run_id"] = UUID(data["run_id"])
        for key in ("cutoff", "window_start", "window_end"):
            data[key] = datetime.fromisoformat(data[key])
        result = Plan(**data)
        result.validate()
        if result.environment != "butlers-dev":
            raise Refused("target_invalid")
        return result
    except (ValueError, TypeError, KeyError, AttributeError, OSError, RecursionError):
        raise Refused("plan_invalid") from None


async def run(plan: Plan, dsn: str, remaining: float | None = None) -> dict:
    options, target = connection_options(dsn)
    if options["db_name"] != plan.database:
        raise Refused("database_identity")
    async with asyncio.timeout(plan.duration_seconds if remaining is None else remaining):
        async with open_database(options) as pool:
            async with pool.acquire() as conn:
                return {**await dry_run(conn, plan), "endpoint_fingerprint": target}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--dry-run", required=True, action="store_true")
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        plan = load_plan(args.plan)
        deadline = time.monotonic() + plan.duration_seconds
        dsn = read_connection(min(deadline, time.monotonic() + 10))
        result = asyncio.run(run(plan, dsn, deadline - time.monotonic()))
        print(json.dumps(result, sort_keys=True))
        return 0 if result["status"] == "READ_ONLY" else 1
    except (Refused, ConnectionRefused) as exc:
        print(json.dumps({"status": "REFUSED", "reason": str(exc)}))
    except Exception:
        print(json.dumps({"status": "UNKNOWN", "reason": "database_read_failed"}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
