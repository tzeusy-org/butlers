#!/usr/bin/env python3
"""Offline operator entrypoint; live use requires separate operational release.

The connection string flows through stdin directly to asyncpg, never an argv,
report, plan or log. This source does not discover credentials or activate work.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import select
import stat
import sys
import time
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
    # A FIFO/device must never block the event loop before its deadline can
    # run. Open without waiting, reject symlinks and inspect the actual opened
    # descriptor (not a racy path check) before reading this bounded local file.
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise RecoveryRefused("invalid_plan")
            raw = stream.read(65537)
    except OSError:
        raise RecoveryRefused("invalid_plan") from None
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
            or not isinstance(row["reason"], str)
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


def _read_connection(*, deadline: float) -> str:
    """Read one bounded stdin line within the same overall operation deadline.

    No thread can outlive the operator, and no raw connection value is emitted.
    A partial pipe without newline must not hold the process before wait_for.
    """
    descriptor = sys.stdin.fileno()
    data = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([descriptor], [], [], remaining)[0]:
            raise RecoveryRefused("connection_input_timeout")
        chunk = os.read(descriptor, min(4096, 16385 - len(data)))
        data.extend(chunk)
        newline = data.find(b"\n")
        if newline >= 0:
            del data[newline:]
        if len(data) > 16384:
            raise RecoveryRefused("connection_input_invalid")
        if newline >= 0 or not chunk:
            try:
                value = data.decode("utf-8").strip()
            except UnicodeDecodeError:
                raise RecoveryRefused("connection_input_invalid") from None
            if not value:
                raise RecoveryRefused("connection_input_missing")
            return value


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
        deadline = time.monotonic() + 120
        dsn = _read_connection(deadline=deadline)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RecoveryRefused("connection_input_timeout")
        return asyncio.run(asyncio.wait_for(run(args, dsn), timeout=remaining))
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
