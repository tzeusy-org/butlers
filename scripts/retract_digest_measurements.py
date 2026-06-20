"""Retract weight measurements that were created from butler-generated digest/briefing text.

BACKGROUND
----------
A circular ingestion bug caused the health butler to re-read weight values from its own
Telegram daily digest/briefing summaries and call measurement_log() on them, creating
self-reinforcing fake measurements (all 68 kg, 0 from the real scale).

This script identifies and retracts (sets validity='retracted') those poisoned rows in
the health schema's facts table. Retraction is reversible — the rows remain in the table
for audit but are excluded from all validity='active' read surfaces.

IDENTIFICATION CRITERIA
-----------------------
Poisoned rows are identified by metadata->>'notes' containing passive/digest/briefing
provenance markers — the same set used by the measurement_log() code guard in
roster/health/tools/measurements.py::_PASSIVE_PROVENANCE_MARKERS.

Specifically, a fact row is considered poisoned if:
  - predicate = 'measurement_weight'
  - scope = 'health'
  - validity = 'active'
  - metadata->>'notes' ILIKE any of: '%briefing%', '%digest%', '%passive%',
    '%daily summary%', '%weekly summary%', '%health summary%', '%trend report%'

TARGET DATABASE
---------------
This script targets butlers-db-dev (the LIVE system with data). The DATABASE_URL must
point to the health schema (or a connection with search_path=health,public).

WARNING: Do NOT run this against butlers-db (the prod/empty instance at .env.prod).
Confirm the target via:
  psql "$DATABASE_URL" -c "SELECT count(*) FROM facts WHERE scope='health';"

USAGE
-----
  # Dry run (default) — shows affected rows without modifying anything
  uv run python scripts/retract_digest_measurements.py

  # Execute retraction on confirmed target
  uv run python scripts/retract_digest_measurements.py --execute

  # With explicit DB URL
  DATABASE_URL=postgresql://... uv run python scripts/retract_digest_measurements.py

ENVIRONMENT
-----------
  DATABASE_URL  PostgreSQL connection string (reads from .env.dev by default if unset)
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Provenance markers — must match _PASSIVE_PROVENANCE_MARKERS in measurements.py
_PASSIVE_PROVENANCE_MARKERS = [
    "briefing",
    "digest",
    "passive",
    "daily summary",
    "weekly summary",
    "health summary",
    "trend report",
]

_SELECT_SQL = """
SELECT
    id,
    predicate,
    valid_at,
    created_at,
    metadata->>'notes'   AS notes,
    metadata->>'value'   AS value,
    validity
FROM facts
WHERE
    predicate = 'measurement_weight'
    AND scope = 'health'
    AND validity = 'active'
    AND (
        {conditions}
    )
ORDER BY valid_at DESC
""".strip()

_RETRACT_SQL = """
UPDATE facts
SET validity = 'retracted'
WHERE
    predicate = 'measurement_weight'
    AND scope = 'health'
    AND validity = 'active'
    AND (
        {conditions}
    )
"""


def _build_conditions() -> str:
    parts = [f"metadata->>'notes' ILIKE '%{m}%'" for m in _PASSIVE_PROVENANCE_MARKERS]
    return "\n        OR ".join(parts)


async def _run(execute: bool, database_url: str) -> None:
    import asyncpg

    conditions = _build_conditions()
    select_sql = _SELECT_SQL.format(conditions=conditions)
    retract_sql = _RETRACT_SQL.format(conditions=conditions)

    print("Connecting to database...")
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)

    try:
        rows = await pool.fetch(select_sql)

        if not rows:
            print("No poisoned measurement_weight facts found. Nothing to retract.")
            return

        print(f"\nFound {len(rows)} poisoned measurement_weight fact(s):\n")
        print(f"{'ID':<38}  {'valid_at':<26}  {'value':>8}  notes")
        print("-" * 100)
        for row in rows:
            fact_id = str(row["id"])
            valid_at = str(row["valid_at"])[:26]
            value = str(row["value"] or "")[:8]
            notes = (row["notes"] or "")[:60]
            print(f"{fact_id}  {valid_at}  {value:>8}  {notes}")

        if not execute:
            print(
                f"\nDRY RUN — {len(rows)} row(s) identified. "
                "Re-run with --execute to retract them.\n"
                "Target: butlers-db-dev (confirm this is the live system before executing)."
            )
            return

        # Confirmation prompt before destructive write
        print(f"\n⚠️  RETRACTION: This will set validity='retracted' on {len(rows)} row(s).")
        print("Target: butlers-db-dev (the LIVE system). Retraction is reversible.")
        confirm = input("Type 'yes' to proceed: ").strip()
        if confirm != "yes":
            print("Aborted.")
            return

        result = await pool.execute(retract_sql)
        print(f"Retracted {result}.")

    finally:
        await pool.close()


def _get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url

    # Try loading from .env.dev (butlers-db-dev is the live system)
    env_dev = Path(__file__).parent.parent / ".env.dev"
    if env_dev.exists():
        for line in env_dev.read_text().splitlines():
            line = line.strip()
            if line.startswith("DATABASE_URL=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")

    print(
        "ERROR: DATABASE_URL not set and .env.dev not found.\n"
        "Set DATABASE_URL or ensure .env.dev exists at the repo root.",
        file=sys.stderr,
    )
    sys.exit(1)


if __name__ == "__main__":
    execute = "--execute" in sys.argv
    database_url = _get_database_url()
    asyncio.run(_run(execute=execute, database_url=database_url))
