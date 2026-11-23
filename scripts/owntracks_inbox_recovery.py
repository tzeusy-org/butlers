#!/usr/bin/env python3
"""Dry-run count of OwnTracks fixes that exist only in ``switchboard.message_inbox`` (bu-uhbhzq.4).

Before evidence-first ingest, an accepted OwnTracks location was submitted to Switchboard first
and persisted to ``connectors.owntracks_points`` afterwards, swallowing write errors. A fix whose
point write failed survives only as an inbox row. This script counts those rows and reports what a
copy could recover. It never writes, and it has no apply mode: the one-shot copy waits for the
owner's retention and archive decisions (bu-uhbhzq.7), and ships as a separate change.

A candidate is an OwnTracks location inbox row whose idempotency key is absent from
``owntracks_points`` and from ``location_retention_forgotten_keys``. A full-tier row carries the
phone's body in ``raw_payload.payload.raw``; a metadata-tier row carries the coordinates only in
its ``normalized_text``, which is parsed back. A row whose text was withheld by retention cannot be
recovered.

The connection string is read from stdin, never argv. The output is counts and date ranges only:
no key, coordinate, endpoint or payload is printed.

    printf '%s' "$DSN" | uv run python scripts/owntracks_inbox_recovery.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

import asyncpg

from butlers.connectors.owntracks import LocationPointRejected, location_point_fields

#: Kept equal to ``WITHHELD_TEXT`` in the Switchboard location-retention tool (a test pins it).
WITHHELD_TEXT = "[location withheld: raw retention]"

#: The read the whole script performs. ``DISTINCT ON`` collapses a key resent more than once.
CANDIDATES_SQL = """
SELECT DISTINCT ON (key) key, received_at, normalized_text, raw
FROM (
    SELECT i.request_context ->> 'idempotency_key' AS key,
           i.received_at,
           i.normalized_text,
           i.raw_payload -> 'payload' -> 'raw' AS raw
    FROM switchboard.message_inbox AS i
    WHERE i.request_context ->> 'source_channel' = 'owntracks'
      AND i.request_context ->> 'idempotency_key' LIKE 'owntracks:%:location'
) AS inbox
WHERE NOT EXISTS (
        SELECT 1 FROM connectors.owntracks_points AS p WHERE p.idempotency_key = inbox.key
    )
  AND NOT EXISTS (
        SELECT 1 FROM switchboard.location_retention_forgotten_keys AS k
        WHERE k.logical_source_key = inbox.key
    )
ORDER BY key, received_at
"""

_KEY = re.compile(r"^owntracks:(?P<endpoint>.+):(?P<tst>\d+):location$")
_TEXT = re.compile(
    r"^Location update: (?P<lat>[^NS,]+)(?P<ns>[NS]), (?P<lon>[^EW,]+)(?P<ew>[EW])"
    r"(?:, acc (?P<acc>[^m,]+)m)?"
)


def _from_text(text: str) -> dict[str, Any] | None:
    """Coordinates from a metadata-tier row's ``normalized_text``, or ``None`` if unreadable."""
    match = _TEXT.match(text)
    if match is None:
        return None
    try:
        lat = float(match["lat"]) * (-1 if match["ns"] == "S" else 1)
        lon = float(match["lon"]) * (-1 if match["ew"] == "W" else 1)
        acc = float(match["acc"]) if match["acc"] is not None else None
    except ValueError:
        return None
    return {"lat": lat, "lon": lon, "acc": acc}


def classify(key: str, normalized_text: str | None, raw: Any) -> tuple[str, int | None]:
    """One candidate's outcome and its fix time (Unix seconds): ``from_raw``, ``from_text`` or
    an ``unrecoverable_*`` / ``rejected`` reason. Pure: reads nothing but its arguments."""
    key_match = _KEY.match(key)
    if key_match is None:
        return "unrecoverable_bad_key", None
    tst = int(key_match["tst"])
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    if isinstance(raw, dict):
        try:
            fields = location_point_fields({**raw, "tst": raw.get("tst", tst)})
        except LocationPointRejected:
            return "rejected", None
        if fields["tst"] != tst:
            return "unrecoverable_key_mismatch", None
        return "from_raw", tst
    text = normalized_text or ""
    if text == WITHHELD_TEXT:
        return "unrecoverable_withheld", None
    parsed = _from_text(text)
    if parsed is None:
        return "unrecoverable_unreadable", None
    try:
        location_point_fields({**parsed, "tst": tst})
    except LocationPointRejected:
        return "rejected", None
    return "from_text", tst


def _iso(seconds: int | datetime | None) -> str | None:
    if seconds is None:
        return None
    moment = seconds if isinstance(seconds, datetime) else datetime.fromtimestamp(seconds, tz=UTC)
    return moment.astimezone(UTC).isoformat()


def summarize(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Counts and date ranges for the candidate rows. Never includes a key or a coordinate."""
    outcomes: Counter[str] = Counter()
    fix_times: list[int] = []
    received: list[datetime] = []
    for row in rows:
        outcome, tst = classify(row["key"], row["normalized_text"], row["raw"])
        outcomes[outcome] += 1
        received.append(row["received_at"])
        if tst is not None:
            fix_times.append(tst)
    recoverable = {name: outcomes.pop(name, 0) for name in ("from_raw", "from_text")}
    return {
        "mode": "dry-run",
        "candidates": sum(recoverable.values()) + sum(outcomes.values()),
        "recoverable": recoverable,
        "not_recoverable": dict(sorted(outcomes.items())),
        "fix_time_range": [_iso(min(fix_times, default=None)), _iso(max(fix_times, default=None))],
        "received_at_range": [_iso(min(received, default=None)), _iso(max(received, default=None))],
    }


async def run(dsn: str) -> dict[str, Any]:
    conn = await asyncpg.connect(dsn, command_timeout=60)
    try:
        async with conn.transaction(readonly=True):
            rows = await conn.fetch(CANDIDATES_SQL)
    finally:
        await conn.close()
    return summarize(
        {
            "key": r["key"],
            "received_at": r["received_at"],
            "normalized_text": r["normalized_text"],
            "raw": r["raw"],
        }
        for r in rows
    )


def main(argv: list[str] | None = None, *, stdin=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)  # no options: there is no apply mode
    dsn = (stdin or sys.stdin).read().strip()
    if not dsn:
        print('{"error":"connection_input_missing"}')
        return 2
    try:
        report = asyncio.run(run(dsn))
    except Exception:
        # A driver error can echo the connection string; print nothing of it.
        print('{"error":"operation_failed"}')
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
