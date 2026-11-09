"""``finance_ledger`` over published transactions and accounts (RFC 0039 D10, OD17).

``merchant`` and ``category`` are the source-owned ``display_merchant``/``display_category``
materializations (REQ-public-mcp-projection-013); amounts stay the decimal strings the view
renders, never floats. The transactions view already hides soft-deleted rows.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any

import asyncpg

from butlers.knowledge_read.core import (
    Arguments,
    ReadContext,
    ReadError,
    decode_cursor,
    encode_cursor,
    fields,
    fit,
    in_snapshot,
    published,
    record_uri,
    span,
)

LEDGER_MAX_DAYS = 366
LEDGER_LIMIT = 500


def _account(row: dict[str, Any] | None) -> tuple[str | None, dict[str, Any] | None]:
    if not row:
        return None, None
    label = " ".join(x for x in (row.get("institution"), row.get("type")) if x) or None
    return label, {
        "id": row["id"],
        "institution": row.get("institution"),
        "type": row.get("type"),
        "name": row.get("name"),
        "last_four": row.get("last_four"),
    }


async def finance_ledger(
    conn: asyncpg.Connection, context: ReadContext, arguments: object
) -> dict[str, Any]:
    """Transactions posted on UTC days ``start``..``end``, newest first, paged.

    ``category`` and ``merchant`` match the displayed values case-insensitively.
    """
    args = Arguments(arguments, {"start", "end", "category", "merchant", "cursor", "limit"})
    start, end = args.day("start"), args.day("end")
    span(start, end, max_days=LEDGER_MAX_DAYS)
    category = args.text("category", max_length=128)
    merchant = args.text("merchant", max_length=256)
    limit = args.integer("limit", low=1, high=LEDGER_LIMIT, default=100)
    after = decode_cursor(context, "finance_ledger", args.values, args.cursor())
    if after is not None and (len(after) != 2 or not all(isinstance(x, str) for x in after)):
        raise ReadError("invalid_argument", argument="cursor")
    after_at = None if after is None else datetime.fromisoformat(after[0])
    context.require("finance")
    transactions, view = published("finance.transactions", "finance_ledger")
    _, account_view = published("finance.accounts", "finance_ledger")
    lower = datetime.combine(start, time(), UTC)
    upper = datetime.combine(end + timedelta(days=1), time(), UTC)

    async def read() -> tuple[list[asyncpg.Record], dict[str, dict[str, Any]]]:
        rows = await conn.fetch(
            f"""SELECT source_id, fields, (fields->>'posted_at')::timestamptz AS at FROM {view}
            WHERE (fields->>'posted_at')::timestamptz >= $1
              AND (fields->>'posted_at')::timestamptz < $2
              AND ($3::text IS NULL OR lower(fields->>'display_category') = lower($3))
              AND ($4::text IS NULL OR lower(fields->>'display_merchant') = lower($4))
              AND ($5::timestamptz IS NULL
                   OR ((fields->>'posted_at')::timestamptz, source_id)
                      < ($5::timestamptz, $6::text))
            ORDER BY at DESC, source_id DESC LIMIT $7""",
            lower,
            upper,
            category,
            merchant,
            after_at,
            None if after is None else after[1],
            limit + 1,
        )
        account_ids = sorted({a for r in rows if (a := fields(r["fields"]).get("account_id"))})
        accounts = await conn.fetch(
            f"SELECT source_id, fields FROM {account_view} WHERE source_id = ANY($1)",
            account_ids,
        )
        return rows, {a["source_id"]: fields(a["fields"]) for a in accounts}

    rows, accounts = await in_snapshot(conn, read)
    more = len(rows) > limit
    items = []
    for row in rows[:limit]:
        data = fields(row["fields"])
        label, account = _account(accounts.get(data.get("account_id")))
        kind, uri = record_uri(transactions, row["source_id"])
        items.append(
            (
                row["at"].isoformat(),
                row["source_id"],
                {
                    "id": row["source_id"],
                    "date": data.get("posted_at"),
                    "amount": data.get("amount"),
                    "currency": data.get("currency"),
                    "direction": data.get("direction"),
                    "merchant": data.get("display_merchant"),
                    "category": data.get("display_category"),
                    "description": data.get("description"),
                    "notes": data.get("notes"),
                    "payment_method": data.get("payment_method"),
                    "receipt_url": data.get("receipt_url"),
                    "external_ref": data.get("external_ref"),
                    "tags": data.get("tags") or [],
                    "account_label": label,
                    "account": account,
                    "source": context.source(kind, uri),
                },
            )
        )

    def build(kept: list, cut: bool) -> dict[str, Any]:
        cursor = None
        if kept and (more or cut):
            cursor = encode_cursor(context, "finance_ledger", args.values, list(kept[-1][:2]))
        return {"items": [entry[2] for entry in kept], "next_cursor": cursor}

    return fit(items, build)
