"""Finance readers over published tables: ledger, summary, obligations and records (RFC 0039 D10).

``merchant`` and ``category`` are the source-owned ``display_merchant``/``display_category``
materializations (REQ-public-mcp-projection-013); amounts stay the decimal strings the view
renders, never floats. The transactions view already hides soft-deleted rows.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
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
from butlers.knowledge_read.records import read_records

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


SUMMARY_GROUPS = 500
OBLIGATION_LIMIT = 200
_GROUP_KEYS = {
    "category": "fields->>'display_category'",
    "merchant": "fields->>'display_merchant'",
    "month": "to_char((fields->>'posted_at')::timestamptz AT TIME ZONE 'UTC', 'YYYY-MM')",
}


async def finance_summary(
    conn: asyncpg.Connection, context: ReadContext, arguments: object
) -> dict[str, Any]:
    """Every non-deleted transaction on UTC days ``start``..``end``, per currency, never converted.

    Debits and credits are never netted: ``totals`` is per (currency, direction) and ``groups``
    per (group key, currency, direction). Amounts are decimal strings; soft-deleted rows are
    already absent from the view.
    """
    args = Arguments(arguments, {"start", "end", "group_by", "currency"})
    start, end = args.day("start"), args.day("end")
    span(start, end, max_days=LEDGER_MAX_DAYS)
    group_by = args.choice("group_by", tuple(_GROUP_KEYS))
    currency = args.text("currency", max_length=3)
    if currency is not None:
        if len(currency) != 3:
            raise ReadError("invalid_argument", argument="currency")
        currency = currency.upper()
        args.values["currency"] = currency
    context.require("finance")
    _, view = published("finance.transactions", "finance_summary")
    lower = datetime.combine(start, time(), UTC)
    upper = datetime.combine(end + timedelta(days=1), time(), UTC)
    where = """(fields->>'posted_at')::timestamptz >= $1
        AND (fields->>'posted_at')::timestamptz < $2
        AND ($3::text IS NULL OR fields->>'currency' = $3)"""

    async def read() -> tuple[list[asyncpg.Record], list[asyncpg.Record]]:
        totals = await conn.fetch(
            f"""SELECT fields->>'currency' AS currency, fields->>'direction' AS direction,
                   sum((fields->>'amount')::numeric)::text AS total, count(*) AS n
            FROM {view} WHERE {where} GROUP BY 1, 2 ORDER BY 1, 2""",
            lower,
            upper,
            currency,
        )
        groups = await conn.fetch(
            f"""SELECT {_GROUP_KEYS[group_by]} AS grp, fields->>'currency' AS currency,
                   fields->>'direction' AS direction,
                   sum((fields->>'amount')::numeric)::text AS total, count(*) AS n
            FROM {view} WHERE {where} GROUP BY 1, 2, 3 ORDER BY 1 NULLS LAST, 2, 3 LIMIT $4""",
            lower,
            upper,
            currency,
            SUMMARY_GROUPS + 1,
        )
        return totals, groups

    totals, groups = await in_snapshot(conn, read)
    totals_out = [
        {
            "currency": r["currency"],
            "direction": r["direction"],
            "total": r["total"],
            "count": r["n"],
        }
        for r in totals
    ]
    group_out = [
        {
            "key": r["grp"],
            "currency": r["currency"],
            "direction": r["direction"],
            "total": r["total"],
            "count": r["n"],
        }
        for r in groups[:SUMMARY_GROUPS]
    ]
    source = context.aggregate_source("finance", "finance_summary")
    result = fit(
        group_out, lambda kept, _: {"totals": totals_out, "groups": kept, "source": source}
    )
    if len(groups) > SUMMARY_GROUPS:
        result["truncated"] = True
    return result


async def finance_obligations(
    conn: asyncpg.Connection, context: ReadContext, arguments: object
) -> dict[str, Any]:
    """Unpaid bills and active subscriptions falling due within ``horizon_days`` of today (UTC).

    Every unpaid bill due on or before the horizon is included, past-due ones too: the
    ``overdue`` status is derived on read in the finance tools and is not reliably stored, and
    the schema's only other status is ``paid``. A field the item's table lacks is ``null``.
    """
    args = Arguments(arguments, {"horizon_days"})
    horizon = args.integer("horizon_days", low=1, high=90)
    context.require("finance")
    bills, bill_view = published("finance.bills", "finance_obligations")
    subs, sub_view = published("finance.subscriptions", "finance_obligations")
    today: date = context.now.astimezone(UTC).date()
    end = today + timedelta(days=horizon)

    async def read() -> tuple[list[asyncpg.Record], list[asyncpg.Record]]:
        due = await conn.fetch(
            f"""SELECT source_id, fields FROM {bill_view}
            WHERE fields->>'status' IS DISTINCT FROM 'paid'
              AND (fields->>'due_date')::date <= $2::date
            ORDER BY (fields->>'due_date')::date, source_id LIMIT $1""",
            OBLIGATION_LIMIT + 1,
            end,
        )
        renewals = await conn.fetch(
            f"""SELECT source_id, fields FROM {sub_view}
            WHERE fields->>'status' = 'active'
              AND (fields->>'next_renewal')::date BETWEEN $2::date AND $3::date
            ORDER BY (fields->>'next_renewal')::date, source_id LIMIT $1""",
            OBLIGATION_LIMIT + 1,
            today,
            end,
        )
        return due, renewals

    due, renewals = await in_snapshot(conn, read)
    items = []
    for row in due:
        data = fields(row["fields"])
        _, uri = record_uri(bills, row["source_id"])
        items.append(
            (
                data.get("due_date") or "",
                row["source_id"],
                {
                    "payee": data.get("payee"),
                    "kind": "bill",
                    "amount": data.get("amount"),
                    "currency": data.get("currency"),
                    "cadence": data.get("frequency"),
                    "next_due": data.get("due_date"),
                    "status": data.get("status"),
                    "payment_method": data.get("payment_method"),
                    "autopay": data.get("autopay"),
                    "cancellation_url": None,
                    "notice_period_days": None,
                    "cancel_by": None,
                    "source": context.source("bills", uri),
                },
            )
        )
    for row in renewals:
        data = fields(row["fields"])
        _, uri = record_uri(subs, row["source_id"])
        items.append(
            (
                data.get("next_renewal") or "",
                row["source_id"],
                {
                    "payee": data.get("service"),
                    "kind": "subscription",
                    "amount": data.get("amount"),
                    "currency": data.get("currency"),
                    "cadence": data.get("frequency"),
                    "next_due": data.get("next_renewal"),
                    "status": data.get("status"),
                    "payment_method": data.get("payment_method"),
                    "autopay": data.get("auto_renew"),
                    "cancellation_url": data.get("cancellation_url"),
                    "notice_period_days": data.get("notice_period_days"),
                    "cancel_by": data.get("cancel_by"),
                    "source": context.source("subscriptions", uri),
                },
            )
        )
    items.sort(key=lambda entry: entry[:2])
    cut = len(items) > OBLIGATION_LIMIT
    result = fit(items[:OBLIGATION_LIMIT], lambda kept, _: {"items": [e[2] for e in kept]})
    if cut:
        result["truncated"] = True
    return result


async def finance_records(
    conn: asyncpg.Connection, context: ReadContext, arguments: object
) -> dict[str, Any]:
    """One page of one published finance table, newest first (REQ-public-mcp-surface-014)."""
    return await read_records(conn, context, arguments, tool="finance_records", domain="finance")
