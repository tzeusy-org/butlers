#!/usr/bin/env python3
"""Offline operator entrypoint; live use requires separate operational release.

The connection string flows through stdin directly to asyncpg, never an argv,
report, plan or log. This source does not discover credentials or activate work.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

import asyncpg

from butlers.chronicler.day_close_recovery import (
    RecoveryEntry,
    RecoveryPlan,
    RecoveryRefused,
    apply_recovery,
    plan_recovery,
)
from butlers.db import register_jsonb_codec


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RecoveryRefused("invalid_plan")
        result[key] = value
    return result


def read_plan(path: Path) -> RecoveryPlan:
    with path.open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise RecoveryRefused("invalid_plan")
    value = json.loads(raw, object_pairs_hook=_strict_object)
    if not isinstance(value, dict) or set(value) != {"timezone", "entries"}:
        raise RecoveryRefused("invalid_plan")
    entries = value["entries"]
    if not isinstance(value["timezone"], str) or not isinstance(entries, list):
        raise RecoveryRefused("invalid_plan")
    if not 1 <= len(entries) <= 31:
        raise RecoveryRefused("invalid_plan")
    result = []
    allowed = {
        "eligible",
        "missing_source",
        "ambiguous_session",
        "incomplete_session",
        "unproven_notification",
        "missing_or_ambiguous_link",
        "source_mismatch",
        "malformed_source",
    }
    for row in entries:
        if (
            not isinstance(row, dict)
            or set(row) != {"day", "reason", "fingerprint"}
            or row["reason"] not in allowed
            or not isinstance(row["day"], str)
            or (
                row["fingerprint"] is not None
                and (
                    not isinstance(row["fingerprint"], str)
                    or len(row["fingerprint"]) != 64
                    or any(c not in "0123456789abcdef" for c in row["fingerprint"])
                )
            )
        ):
            raise RecoveryRefused("invalid_plan")
        if (row["reason"] == "eligible") != (row["fingerprint"] is not None):
            raise RecoveryRefused("invalid_plan")
        result.append(
            RecoveryEntry(date.fromisoformat(row["day"]), row["reason"], row["fingerprint"])
        )
    return RecoveryPlan(value["timezone"], tuple(result))


async def run(args, dsn: str) -> int:
    async with asyncpg.create_pool(
        dsn=dsn,
        min_size=1,
        max_size=1,
        command_timeout=30,
        init=register_jsonb_codec,
        server_settings={"search_path": "chronicler,public"},
    ) as pool:
        async with pool.acquire() as conn:
            if await conn.fetchval("SELECT current_user") != "butler_chronicler_rw":
                raise RecoveryRefused("wrong_database_role")
        if args.apply_plan:
            plan = read_plan(args.apply_plan)
            entries = await apply_recovery(pool, plan)
            payload = RecoveryPlan(plan.timezone, entries).safe_dict()
        else:
            if args.start is None or args.end is None or args.timezone is None:
                raise RecoveryRefused("invalid_range")
            plan = await plan_recovery(pool, start=args.start, end=args.end, timezone=args.timezone)
            payload = plan.safe_dict()
        print(json.dumps(payload, sort_keys=True))
        return (
            0
            if all(
                e["reason"] in {"eligible", "applied", "already_applied", "preserved_valid"}
                for e in payload["entries"]
            )
            else 2
        )


class _Parser(argparse.ArgumentParser):
    def error(self, _message):
        # argparse's default echoes the caller's raw argument values.
        print('{"error":"invalid_arguments"}')
        raise SystemExit(2)


def main() -> int:
    parser = _Parser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--timezone")
    parser.add_argument("--apply-plan", type=Path)
    args = parser.parse_args()
    # There is no default application mode or credential-file discovery.
    if args.apply_plan and any(x is not None for x in [args.start, args.end, args.timezone]):
        print('{"error":"invalid_arguments"}')
        return 2
    try:
        dsn = sys.stdin.readline(16385).strip()
        if not dsn or len(dsn) > 16384:
            raise RecoveryRefused("connection_input_missing")
        return asyncio.run(asyncio.wait_for(run(args, dsn), timeout=120))
    except RecoveryRefused as exc:
        # Only locally constructed closed reasons can reach this class.
        print(json.dumps({"error": str(exc)}))
        return 2
    except KeyboardInterrupt:
        print('{"error":"cancelled"}')
        return 130
    except Exception:
        print('{"error":"operation_failed"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
