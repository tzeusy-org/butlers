"""Scrub bearer material from historical ``message_inbox`` rows.

Revision ID: sw_040
Revises: sw_039
Create Date: 2026-10-03 00:00:00.000000

bu-q7vx1q.2.  One-time codes, reset links, magic links and Telegram login codes
were stored verbatim in ``message_inbox.raw_payload`` / ``normalized_text``.  New
ingests are scrubbed at the boundary; this migration applies the same detector
(imported, not copied, so the rules stay single-sourced) to rows already stored.

Idempotent: scrubbed text contains only placeholders, which the detector leaves
alone, so a second run updates nothing.  Rows are keyset-paged by
``(received_at, id)`` and only rewritten when the scrub changed them.

Only message content (``raw_payload.payload`` and ``normalized_text``) is rewritten;
sender, event and control metadata are never touched.  All batches run inside the single
alembic transaction, but only rows with a hit are updated, so the lock footprint is the
(small) set of rows that held bearer material.  This revision is unmerged until its PR
lands, so it has not run against any deployed database.

Downgrade is a deliberate no-op: scrubbed material is irreversible by design.
"""

from __future__ import annotations

import json
import logging

from alembic import op
from butlers.ingestion_bearer_scrub import (
    scrub_stored_record,
)

revision = "sw_040"
down_revision = "sw_039"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)

_BATCH = 500


def _scrub_row(raw_payload: dict, normalized_text: str) -> tuple[dict, str] | None:
    new_raw, new_text, fresh = scrub_stored_record(raw_payload, normalized_text)
    if not fresh:
        return None
    observed_at = (raw_payload.get("event") or {}).get("observed_at")
    new_raw.setdefault("control", {})["bearer_scrubbed"] = True
    new_raw["bearer_artifacts"] = [
        {**artifact.as_dict(), **({"observed_at": observed_at} if observed_at else {})}
        for artifact in fresh
    ]
    return new_raw, new_text


def upgrade() -> None:
    conn = op.get_bind()
    last_received, last_id = None, None
    scrubbed = 0
    while True:
        rows = conn.exec_driver_sql(
            "SELECT received_at, id, raw_payload, normalized_text FROM message_inbox "
            "WHERE (%(r)s::timestamptz IS NULL OR (received_at, id) > (%(r)s::timestamptz, %(i)s::uuid)) "
            "ORDER BY received_at, id LIMIT %(n)s",
            {"r": last_received, "i": last_id, "n": _BATCH},
        ).fetchall()
        if not rows:
            break
        for received_at, row_id, raw_payload, normalized_text in rows:
            if isinstance(raw_payload, str):
                raw_payload = json.loads(raw_payload)
            result = _scrub_row(raw_payload or {}, normalized_text or "")
            if result is None:
                continue
            new_raw, new_text = result
            conn.exec_driver_sql(
                "UPDATE message_inbox SET raw_payload = %(raw)s::jsonb, normalized_text = %(t)s "
                "WHERE received_at = %(r)s AND id = %(i)s",
                {"raw": json.dumps(new_raw), "t": new_text, "r": received_at, "i": row_id},
            )
            scrubbed += 1
        last_received, last_id = rows[-1][0], rows[-1][1]
    logger.info("sw_040 scrubbed bearer material from %d message_inbox rows", scrubbed)


def downgrade() -> None:
    """Scrubbed material cannot be restored (irreversible by design)."""
