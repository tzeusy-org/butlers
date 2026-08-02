"""Feed-vs-email transaction reconciliation + per-account aggregator freshness.

bu-8bnn9 (follow-up from PR #3589, bu-ep4ks.16). Perception-tier slice 3:
cross-checks aggregator-sourced transactions (``transactions.source =
'aggregator'``) against the existing email-parsed ledger
(``transactions.source_message_id IS NOT NULL``) on (amount, date,
merchant-fuzzy), and reports per-account freshness of the aggregator feed.

Reuses the merchant-fuzzy matcher from ``roster/finance/tools/reconciliation.py``
(the bill<->payment reconciliation engine) rather than reimplementing token
normalization -- one merchant-similarity definition for the whole butler.

The optional SimpleFIN Bridge can write aggregator transactions and update an
account's ``last_synced_at`` after the owner supplies its credentials and a
sync succeeds. ``reconcile_feed_vs_email()`` derives ``configured`` from
persisted completed feed-sync evidence, not from a current credential or
configuration.

Degraded-mode honesty (CLAUDE.md "Degraded-Mode Response Envelope"): before
any successful aggregator sync, ``reconcile_feed_vs_email()`` reports
``configured=False`` rather than a fabricated "all clear" from an empty
aggregator set. That indicator does not report whether a credential or
configuration is currently present. Accounts without a completed sync remain
``degraded=True, reason="never_synced"``; successfully synced accounts are
then classified as fresh or stale individually.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import asyncpg

from butlers.tools.finance.reconciliation import _payee_match

_DEFAULT_LOOKBACK_DAYS = 30
_DATE_TOLERANCE_DAYS = 3
_AMOUNT_TOLERANCE = Decimal("0.01")
_DEFAULT_STALENESS_HOURS = 24


def _ensure_utc(dt: datetime) -> datetime:
    """Attach UTC to a naive datetime (defensive -- asyncpg normally returns aware values)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _within_tolerance(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) <= _AMOUNT_TOLERANCE


def _dates_close(a: datetime, b: datetime) -> bool:
    return abs((_ensure_utc(a) - _ensure_utc(b)).days) <= _DATE_TOLERANCE_DAYS


def _row_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "account_id": str(row["account_id"]) if row.get("account_id") else None,
        "merchant": row["merchant"],
        "amount": str(Decimal(str(row["amount"]))),
        "currency": row["currency"],
        "posted_at": row["posted_at"].isoformat() if row.get("posted_at") else None,
    }


async def reconcile_feed_vs_email(
    pool: asyncpg.Pool,
    lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
) -> dict[str, Any]:
    """Match aggregator-sourced transactions against the email-parsed ledger.

    Matching is per-currency, within ``_AMOUNT_TOLERANCE`` of the amount,
    within ``_DATE_TOLERANCE_DAYS`` of the posted date, and a merchant-fuzzy
    match (whole-token subset or substring containment, same rule the bill
    reconciliation engine uses). Greedy: each feed transaction claims the
    first unclaimed email transaction that matches it.

    Returns
    -------
    dict
        ``configured``: whether any account has persisted completed feed-sync
        evidence (``accounts.last_synced_at IS NOT NULL``). ``False`` means the
        empty ``matched``/``unmatched_feed`` lists reflect no completed feed
        sync, not "everything reconciled"; it does not report whether an
        optional SimpleFIN credential or configuration is currently present.
        ``matched``: list of ``{feed, email}`` pairs.
        ``unmatched_feed``: aggregator transactions with no matching email
        receipt -- e.g. a merchant that does not send email receipts.
        ``unmatched_email_count``: count of email-parsed transactions with no
        matching aggregator transaction in the window (not itemized -- while
        no aggregator source is configured this is just "every email
        transaction", which is not itself informative).
    """
    configured = await _any_account_ever_synced(pool)

    horizon = datetime.now(UTC) - timedelta(days=lookback_days)
    feed_rows = [
        dict(r)
        for r in await pool.fetch(
            """
            SELECT * FROM transactions
            WHERE source = 'aggregator'
              AND posted_at >= $1
              AND deleted_at IS NULL
            ORDER BY posted_at DESC
            """,
            horizon,
        )
    ]
    email_rows = [
        dict(r)
        for r in await pool.fetch(
            """
            SELECT * FROM transactions
            WHERE source_message_id IS NOT NULL
              AND posted_at >= $1
              AND deleted_at IS NULL
            ORDER BY posted_at DESC
            """,
            horizon,
        )
    ]

    unclaimed_email = list(email_rows)
    matched: list[dict[str, Any]] = []
    unmatched_feed: list[dict[str, Any]] = []

    for feed_row in feed_rows:
        feed_amount = abs(Decimal(str(feed_row["amount"])))
        candidate = None
        for email_row in unclaimed_email:
            if feed_row["currency"] != email_row["currency"]:
                continue
            if feed_row.get("account_id") and email_row.get("account_id"):
                if feed_row["account_id"] != email_row["account_id"]:
                    continue
            email_amount = abs(Decimal(str(email_row["amount"])))
            if not _within_tolerance(feed_amount, email_amount):
                continue
            if not _dates_close(feed_row["posted_at"], email_row["posted_at"]):
                continue
            is_match, _is_exact = _payee_match(feed_row["merchant"], email_row["merchant"])
            if not is_match:
                continue
            candidate = email_row
            break

        if candidate is not None:
            unclaimed_email.remove(candidate)
            matched.append({"feed": _row_summary(feed_row), "email": _row_summary(candidate)})
        else:
            unmatched_feed.append(_row_summary(feed_row))

    return {
        "configured": configured,
        "matched": matched,
        "unmatched_feed": unmatched_feed,
        "unmatched_email_count": len(unclaimed_email),
        "feed_transactions_checked": len(feed_rows),
        "email_transactions_checked": len(email_rows),
    }


async def _any_account_ever_synced(pool: asyncpg.Pool) -> bool:
    row = await pool.fetchrow("SELECT 1 FROM accounts WHERE last_synced_at IS NOT NULL LIMIT 1")
    return row is not None


async def account_feed_freshness(
    pool: asyncpg.Pool,
    staleness_threshold_hours: int = _DEFAULT_STALENESS_HOURS,
) -> list[dict[str, Any]]:
    """Report per-account aggregator-feed freshness.

    An account is ``degraded`` when it has never completed an aggregator sync
    (``reason="never_synced"``) or its last sync is older than
    ``staleness_threshold_hours`` (``reason="stale"``). Never fabricates a
    healthy status from an absent ``last_synced_at`` -- see module docstring.
    """
    rows = await pool.fetch(
        """
        SELECT id, institution, type, name, last_synced_at
        FROM accounts
        ORDER BY institution, name
        """
    )
    now = datetime.now(UTC)
    threshold = timedelta(hours=staleness_threshold_hours)

    results: list[dict[str, Any]] = []
    for row in rows:
        last_synced = row["last_synced_at"]
        if last_synced is None:
            degraded, reason = True, "never_synced"
        elif _ensure_utc(last_synced) < now - threshold:
            degraded, reason = True, "stale"
        else:
            degraded, reason = False, None

        results.append(
            {
                "account_id": str(row["id"]),
                "institution": row["institution"],
                "type": row["type"],
                "name": row["name"],
                "last_synced_at": _ensure_utc(last_synced).isoformat() if last_synced else None,
                "degraded": degraded,
                "reason": reason,
            }
        )

    return results


__all__ = ["account_feed_freshness", "reconcile_feed_vs_email"]
