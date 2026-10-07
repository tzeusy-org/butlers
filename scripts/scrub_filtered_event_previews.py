#!/usr/bin/env python3
"""One-shot visibility-preview redaction. Default CLI mode is dry run.

No migrations, startup invocation, replay/payload changes or role provisioning.
Live apply requires separate authorization and a fixed-writer/quiescence plan.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

import asyncpg

from butlers import ingestion_bearer_scrub
from butlers.db import database_name_from_env, db_params_from_env, register_jsonb_codec
from butlers.ingestion_bearer_scrub import scrub_filtered_preview


@dataclass
class ScrubReceipt:
    """Content-free progress; cursor advances only after durable readback."""

    verdict: str
    cutoff: str
    detector_sha256: str
    cursor: tuple[str, str] | None = None
    scanned: int = 0
    changed: int = 0
    batches: int = 0
    phase: str = "initial"
    error_class: str | None = None
    sqlstate: str | None = None


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must include a UTC offset")
    return value.astimezone(UTC)


async def scrub_existing_previews(
    pool: asyncpg.Pool,
    *,
    cutoff: datetime,
    resume_after: tuple[datetime, UUID] | None = None,
    batch_size: int = 500,
    dry_run: bool = False,
) -> ScrubReceipt:
    """Visit a finite cutoff in key order, changing only subject_or_preview.

    Apply locks each selected row (no SKIP LOCKED), with bounded waits. Unknown
    commit/readback acknowledgements retain the prior cursor: repeating a batch
    is safe because placeholders are idempotent. Retention may remove rows and
    is never reversed. Old writers inserting behind a cursor require a new pass.
    """
    cutoff = _aware(cutoff)
    if not 1 <= batch_size <= 500:
        raise ValueError("batch_size must be between 1 and 500")
    cursor = (_aware(resume_after[0]), resume_after[1]) if resume_after else None
    receipt = ScrubReceipt(
        "INCOMPLETE",
        cutoff.isoformat(),
        hashlib.sha256(Path(ingestion_bearer_scrub.__file__).read_bytes()).hexdigest(),
        (cursor[0].isoformat(), str(cursor[1])) if cursor else None,
    )
    while True:
        rows = []
        changed = []
        try:
            receipt.phase = "select"
            async with pool.acquire() as connection, connection.transaction():
                await connection.execute("SET LOCAL lock_timeout = '3s'")
                await connection.execute("SET LOCAL statement_timeout = '15s'")
                rows = await connection.fetch(
                    "SELECT received_at, id, connector_type, sender_identity, "
                    "subject_or_preview, full_payload FROM connectors.filtered_events "
                    "WHERE received_at <= $1 "
                    "AND ($2::timestamptz IS NULL OR (received_at,id) > ($2,$3::uuid)) "
                    "ORDER BY received_at,id LIMIT $4" + ("" if dry_run else " FOR UPDATE"),
                    cutoff,
                    cursor[0] if cursor else None,
                    cursor[1] if cursor else None,
                    batch_size,
                )
                for row in rows:
                    preview = scrub_filtered_preview(
                        row["subject_or_preview"],
                        connector_type=row["connector_type"],
                        sender_identity=row["sender_identity"],
                        full_payload=row["full_payload"],
                    )
                    if preview != row["subject_or_preview"]:
                        changed.append((row["received_at"], row["id"], preview))
                        if not dry_run:
                            receipt.phase = "update"
                            await connection.execute(
                                "UPDATE connectors.filtered_events SET subject_or_preview=$3 "
                                "WHERE received_at=$1 AND id=$2",
                                row["received_at"],
                                row["id"],
                                preview,
                            )
                receipt.phase = "commit"
            if not dry_run:
                receipt.phase = "readback"
                # A distinct acquisition positions this witness after commit.
                async with pool.acquire() as readback:
                    for received_at, row_id, preview in changed:
                        stored = await readback.fetchrow(
                            "SELECT subject_or_preview FROM connectors.filtered_events "
                            "WHERE received_at=$1 AND id=$2",
                            received_at,
                            row_id,
                        )
                        if stored is not None and stored["subject_or_preview"] != preview:
                            raise RuntimeError("Preview readback disagrees")
        except Exception as exc:
            receipt.verdict = "UNKNOWN" if receipt.phase in {"commit", "readback"} else "INCOMPLETE"
            receipt.error_class = type(exc).__name__
            receipt.sqlstate = getattr(exc, "sqlstate", None)
            return receipt
        if not rows:
            receipt.verdict = "DRY-RUN" if dry_run else "COMPLETE"
            receipt.phase = "finished"
            return receipt
        cursor = rows[-1]["received_at"], rows[-1]["id"]
        receipt.cursor = cursor[0].isoformat(), str(cursor[1])
        receipt.scanned += len(rows)
        receipt.changed += len(changed)
        receipt.batches += 1
        receipt.phase = "batch-verified"


async def _main(args: argparse.Namespace) -> int:
    # Require an explicit target/operator identity; never provision via Database.connect.
    if not os.environ.get("DATABASE_URL") and not all(
        os.environ.get(key)
        for key in ("POSTGRES_HOST", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")
    ):
        raise ValueError("An explicit database target and operator identity are required")
    if os.environ.get("DATABASE_URL"):
        target = urlparse(os.environ["DATABASE_URL"])
        if not target.hostname or not target.username or not target.password:
            raise ValueError("DATABASE_URL must specify host and operator credentials")
    params = db_params_from_env()
    params["database"] = database_name_from_env("")
    if not params["database"]:
        raise ValueError("An explicit database name is required")
    pool = await asyncpg.create_pool(**params, min_size=1, max_size=2, init=register_jsonb_codec)
    try:
        cursor = (
            (datetime.fromisoformat(args.resume_time), UUID(args.resume_id))
            if args.resume_time and args.resume_id
            else None
        )
        receipt = await scrub_existing_previews(
            pool,
            cutoff=datetime.fromisoformat(args.cutoff),
            resume_after=cursor,
            batch_size=args.batch_size,
            dry_run=not args.apply,
        )
        print(json.dumps(asdict(receipt), sort_keys=True))
        return 0 if receipt.verdict in {"COMPLETE", "DRY-RUN"} else 1
    finally:
        await pool.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cutoff", required=True, help="Fixed timestamp with UTC offset")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--resume-time")
    parser.add_argument("--resume-id")
    parser.add_argument(
        "--apply", action="store_true", help="Irreversible redaction; separately authorize live use"
    )
    args = parser.parse_args()
    if bool(args.resume_time) != bool(args.resume_id):
        parser.error("--resume-time and --resume-id must be supplied together")
    try:
        return asyncio.run(_main(args))
    except Exception as exc:
        print(
            json.dumps(
                {
                    "verdict": "INCOMPLETE",
                    "phase": "startup",
                    "error_class": type(exc).__name__,
                    "sqlstate": getattr(exc, "sqlstate", None),
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
