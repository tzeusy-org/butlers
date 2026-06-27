"""Finance butler bill tools — track payable obligations and surface upcoming dues."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import asyncpg

from butlers.tools.finance._helpers import _row_to_dict

logger = logging.getLogger(__name__)

_background_tasks: set[asyncio.Task[None]] = set()

_VALID_STATUSES = ("pending", "paid", "overdue")
_VALID_FREQUENCIES = ("one_time", "weekly", "monthly", "quarterly", "yearly", "custom")

_DEFAULT_DAYS_AHEAD = 14

_WHITESPACE_RE = re.compile(r"\s+")


def _payee_key(payee: str) -> str:
    """Normalize a payee name into a dedup key.

    Collapses case/whitespace/trailing-period variants so the same payee stops
    proliferating into duplicate bill rows on each ingest ("Tailscale Inc." and
    "Tailscale Inc" map to the same key).

    Kept in sync with the SQL backfill in migration ``010_bills_action_semantics``
    (lower + collapse whitespace + trim + strip trailing period). It deliberately
    does NOT attempt semantic alias merging (e.g. "Endowus" vs "Endowus CPF OA
    Investment") — those are handled by the one-time data cleanup and by the
    caller supplying a consistent payee name.
    """
    collapsed = _WHITESPACE_RE.sub(" ", payee.lower()).strip()
    return collapsed.rstrip(".").strip()


def _normalize_date(value: str | date) -> date:
    """Normalize a date value to a date object."""
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _urgency(due: date, today: date, is_overdue: bool) -> str:
    """Compute urgency classification for a bill."""
    if is_overdue:
        return "overdue"
    days = (due - today).days
    if days == 0:
        return "due_today"
    return "due_soon"


async def _mirror_bill_to_spo(
    pool: asyncpg.Pool,
    *,
    payee: str,
    amount: float,
    currency: str,
    due_date: date,
    frequency: str,
    status: str,
    payment_method: str | None,
    account_id: str | None,
    paid_at: datetime | None,
    reconciled_transaction_id: uuid.UUID | str | None,
    source_message_id: str | None,
) -> None:
    """Fire-and-forget SPO mirror write to public.facts after a bills upsert.

    Writes a property fact (predicate='bill', valid_at=NULL) via _write_bill_fact.
    Errors are swallowed so that a mirror failure never rolls back the primary
    finance.bills upsert.

    Canonical metadata written: payee, amount, currency, due_date, frequency,
    status, payment_method, account_id, paid_at, reconciled_transaction_id,
    source_message_id.

    This function is scheduled via asyncio.create_task and must never raise.
    """
    try:
        from butlers.tools.finance.facts import _write_bill_fact

        mirror_metadata: dict[str, Any] = {}
        if reconciled_transaction_id is not None:
            mirror_metadata["reconciled_transaction_id"] = str(reconciled_transaction_id)

        await _write_bill_fact(
            pool=pool,
            payee=payee,
            amount=amount,
            currency=currency,
            due_date=due_date,
            frequency=frequency,
            status=status,
            payment_method=payment_method,
            account_id=account_id,
            paid_at=paid_at,
            source_message_id=source_message_id,
            metadata=mirror_metadata if mirror_metadata else None,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "_mirror_bill_to_spo: SPO mirror write failed for payee=%r due_date=%r; "
            "primary upsert is unaffected",
            payee,
            due_date,
            exc_info=True,
        )


async def track_bill(
    pool: asyncpg.Pool,
    payee: str,
    amount: float,
    currency: str,
    due_date: str | date,
    frequency: str = "one_time",
    status: str = "pending",
    payment_method: str | None = None,
    account_id: str | uuid.UUID | None = None,
    statement_period_start: str | date | None = None,
    statement_period_end: str | date | None = None,
    paid_at: datetime | str | None = None,
    source_message_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    autopay: bool | None = None,
    predicted: bool | None = None,
) -> dict[str, Any]:
    """Create or update a bill obligation in finance.bills.

    Upsert logic: match on (payee_key, due_date) where ``payee_key`` is the
    normalized payee. This collapses case/whitespace variants of the same payee
    so re-ingesting "Tailscale Inc." after "Tailscale Inc" updates the existing
    row instead of inserting a duplicate. If a record is found, update all
    provided fields and refresh updated_at; otherwise insert.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    payee:
        Name of the payee (e.g. "PG&E", "Comcast", "Chase Credit Card").
    amount:
        Amount owed.
    currency:
        ISO-4217 uppercase currency code (e.g. "USD", "EUR").
    due_date:
        Date the bill is due. Accepts ISO date strings or date objects.
    frequency:
        Recurrence frequency. One of: one_time, weekly, monthly, quarterly,
        yearly, custom. Default: one_time.
    status:
        Bill status. One of: pending, paid, overdue. Default: pending.
    payment_method:
        Payment method description.
    account_id:
        UUID of linked financial account.
    statement_period_start:
        Start of the billing statement period.
    statement_period_end:
        End of the billing statement period.
    paid_at:
        Timestamp when the bill was paid (for status=paid transitions).
    source_message_id:
        Source email or provider message ID for provenance.
    metadata:
        Arbitrary JSON metadata for extended attributes.
    autopay:
        Whether the bill is auto-debited (GIRO / CPF / card autopay). When
        true, the bill is informational — the owner takes no action. ``None``
        (default) leaves an existing row's flag unchanged and inserts ``false``.
    predicted:
        Whether the row originated from a pattern-based prediction rather than a
        confirmed obligation. Predictions should normally stay out of the bills
        table (``predict_bills`` is read-only); this flag exists to quarantine
        any that are tracked from the actionable list. ``None`` (default) leaves
        an existing row's flag unchanged and inserts ``false``.

    Returns
    -------
    dict
        BillRecord with all persisted fields.
    """
    if status not in _VALID_STATUSES:
        raise ValueError(f"Invalid status {status!r}. Must be one of {_VALID_STATUSES}")
    if frequency not in _VALID_FREQUENCIES:
        raise ValueError(f"Invalid frequency {frequency!r}. Must be one of {_VALID_FREQUENCIES}")

    due = _normalize_date(due_date)
    period_start = _normalize_date(statement_period_start) if statement_period_start else None
    period_end = _normalize_date(statement_period_end) if statement_period_end else None
    metadata_value: dict[str, Any] = dict(metadata) if metadata is not None else {}
    account_uuid = uuid.UUID(str(account_id)) if account_id is not None else None
    payee_key = _payee_key(payee)

    # Normalize paid_at to datetime if provided as string
    paid_at_dt: datetime | None = None
    if paid_at is not None:
        if isinstance(paid_at, str):
            paid_at_dt = datetime.fromisoformat(paid_at)
        else:
            paid_at_dt = paid_at

    # Upsert: match on (payee_key, due_date) so payee name variants collapse.
    # Fall back to legacy payee match for rows predating the payee_key backfill.
    existing = await pool.fetchrow(
        "SELECT id FROM bills"
        " WHERE due_date = $2 AND (payee_key = $1 OR (payee_key IS NULL AND payee = $3))"
        " ORDER BY payee_key IS NULL"
        " LIMIT 1",
        payee_key,
        due,
        payee,
    )

    if existing is not None:
        row = await pool.fetchrow(
            """
            UPDATE bills
            SET
                payee                 = $1,
                payee_key             = $2,
                amount                = $3,
                currency              = $4,
                frequency             = $5,
                status                = $6,
                payment_method        = COALESCE($7, payment_method),
                account_id            = COALESCE($8, account_id),
                statement_period_start = COALESCE($9, statement_period_start),
                statement_period_end   = COALESCE($10, statement_period_end),
                paid_at               = COALESCE($11, paid_at),
                source_message_id     = COALESCE($12, source_message_id),
                metadata              = metadata || $13,
                autopay               = COALESCE($14, autopay),
                predicted             = COALESCE($15, predicted),
                updated_at            = now()
            WHERE id = $16
            RETURNING *
            """,
            payee,
            payee_key,
            amount,
            currency,
            frequency,
            status,
            payment_method,
            account_uuid,
            period_start,
            period_end,
            paid_at_dt,
            source_message_id,
            metadata_value,
            autopay,
            predicted,
            existing["id"],
        )
    else:
        row = await pool.fetchrow(
            """
            INSERT INTO bills (
                payee, payee_key, amount, currency, due_date, frequency, status,
                payment_method, account_id, statement_period_start,
                statement_period_end, paid_at, source_message_id, metadata,
                autopay, predicted
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7,
                $8, $9, $10,
                $11, $12, $13, $14,
                COALESCE($15, false), COALESCE($16, false)
            )
            RETURNING *
            """,
            payee,
            payee_key,
            amount,
            currency,
            due,
            frequency,
            status,
            payment_method,
            account_uuid,
            period_start,
            period_end,
            paid_at_dt,
            source_message_id,
            metadata_value,
            autopay,
            predicted,
        )

    result = _row_to_dict(row)

    # Fire-and-forget SPO mirror write to public.facts. Scheduled as a
    # background task so failures never roll back the primary upsert.
    # Hold a strong reference in _background_tasks so the task is not
    # garbage-collected before it completes; the done-callback removes it.
    _task = asyncio.create_task(
        _mirror_bill_to_spo(
            pool=pool,
            payee=payee,
            amount=amount,
            currency=currency,
            due_date=due,
            frequency=frequency,
            status=status,
            payment_method=payment_method,
            account_id=str(account_uuid) if account_uuid is not None else None,
            paid_at=paid_at_dt,
            reconciled_transaction_id=result.get("reconciled_transaction_id"),
            source_message_id=source_message_id,
        )
    )
    _background_tasks.add(_task)
    _task.add_done_callback(_background_tasks.discard)

    return result


async def upcoming_bills(
    pool: asyncpg.Pool,
    days_ahead: int = _DEFAULT_DAYS_AHEAD,
    include_overdue: bool = False,
) -> dict[str, Any]:
    """Query upcoming bills, segmented by whether the owner must act.

    Bills due within ``days_ahead`` days (and, when ``include_overdue`` is set,
    already-overdue obligations) are partitioned into three buckets so a digest
    can lead with what actually needs doing instead of burying it:

    - ``needs_action``: real, confirmed obligations the owner must pay manually
      (not autopay, not predicted, amount > 0).
    - ``autopay``: bills that auto-debit (GIRO / CPF / card autopay) — surfaced
      as informational FYIs, never as action items.
    - ``predicted``: pattern-based rows that are not confirmed obligations.

    Zero-amount placeholders (statements awaiting an amount) are excluded from
    every bucket and only counted in ``suppressed_placeholders`` — per the
    placeholder doctrine, they are not actionable until reconciliation backfills
    the amount.

    Each item carries an ``urgency`` (``overdue`` / ``due_today`` / ``due_soon``)
    and ``days_until_due``. Only ``needs_action`` contributes to
    ``totals.needs_action_amount`` — the money the owner must actively move.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    days_ahead:
        Number of days from today to include. Default: 14.
    include_overdue:
        Whether to include already-overdue bills. Default: False.

    Returns
    -------
    dict
        Segmented response with ``as_of``, ``window_days``, ``needs_action``,
        ``autopay``, ``predicted``, ``suppressed_placeholders``, and ``totals``.
    """
    # Local date for comparing against plain-date due_date columns.
    today = date.today()
    horizon = today + timedelta(days=days_ahead)

    if include_overdue:
        rows = await pool.fetch(
            """
            SELECT * FROM bills
            WHERE
                (due_date >= $1 AND due_date <= $2 AND status IN ('pending', 'overdue'))
                OR status = 'overdue'
                OR (due_date < $1 AND status = 'pending')
            ORDER BY due_date ASC, payee ASC
            """,
            today,
            horizon,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT * FROM bills
            WHERE due_date >= $1 AND due_date <= $2 AND status IN ('pending', 'overdue')
            ORDER BY due_date ASC, payee ASC
            """,
            today,
            horizon,
        )

    needs_action: list[dict[str, Any]] = []
    autopay_items: list[dict[str, Any]] = []
    predicted_items: list[dict[str, Any]] = []
    suppressed_placeholders = 0
    needs_action_amount = Decimal("0.00")
    autopay_amount = Decimal("0.00")

    for row in rows:
        bill = _row_to_dict(row)
        due = row["due_date"]
        bill_status = row["status"]

        try:
            amount = Decimal(str(row["amount"]))
        except Exception:
            amount = Decimal("0.00")

        # Zero-amount placeholders are not actionable — count and skip.
        if amount == 0:
            suppressed_placeholders += 1
            continue

        is_overdue = bill_status == "overdue" or (due < today and bill_status == "pending")
        item = {
            "bill": bill,
            "urgency": _urgency(due, today, is_overdue),
            "days_until_due": (due - today).days,
        }

        if row["autopay"]:
            autopay_items.append(item)
            autopay_amount += amount
        elif row["predicted"]:
            predicted_items.append(item)
        else:
            needs_action.append(item)
            needs_action_amount += amount

    return {
        "as_of": datetime.now(UTC).isoformat(),
        "window_days": days_ahead,
        "needs_action": needs_action,
        "autopay": autopay_items,
        "predicted": predicted_items,
        "suppressed_placeholders": suppressed_placeholders,
        "totals": {
            "needs_action_count": len(needs_action),
            "needs_action_amount": str(needs_action_amount),
            "autopay_count": len(autopay_items),
            "autopay_amount": str(autopay_amount),
            "predicted_count": len(predicted_items),
        },
    }


def compose_upcoming_bills_digest(
    sweep: dict,
    bills: dict,
    predictions: dict,
    *,
    today: date | None = None,
) -> str | None:
    """Compose the weekly bills digest from reconcile/upcoming/predict results.

    Implements the upcoming-bills-check SKILL.md Step 2 early-exit and Step 3 format.
    Returns None when there is nothing worth sending (early exit).
    Returns the formatted Telegram-ready digest string otherwise.

    Parameters
    ----------
    sweep:
        Output of ``reconcile_bills()`` — keys: ``auto_settled``, ``candidates``.
    bills:
        Output of ``upcoming_bills()`` — keys: ``needs_action``, ``autopay``,
        ``predicted``, ``totals``.
    predictions:
        Output of ``predict_bills()`` — key: ``predictions`` (list).
    today:
        Reference date for the header; defaults to today.
    """
    if today is None:
        today = date.today()

    auto_settled = sweep.get("auto_settled", [])
    candidates = sweep.get("candidates", [])
    needs_action = bills.get("needs_action", [])
    autopay = bills.get("autopay", [])
    totals = bills.get("totals", {})
    preds = [p for p in predictions.get("predictions", []) if not p.get("is_tracked")]

    # Step 2: early exit — nothing worth sending
    if not any([auto_settled, candidates, needs_action, autopay, preds]):
        return None

    # Step 3: compose the full digest before calling notify
    sections: list[str] = [f"Bills — {today.strftime('%-d %b %Y')}"]

    if auto_settled:
        sections.append(
            f"\n✅ Auto-settled ({len(auto_settled)}) — matched and settled in this sweep"
        )
        for s in auto_settled:
            paid_str = (s.get("paid_at") or "")[:10] or "—"
            sections.append(f"- {s['payee']}: {s['amount']} — paid {paid_str}")

    if candidates:
        sections.append(
            f"\n❓ Confirm needed ({len(candidates)}) — ambiguous matches, please verify"
        )
        for c in candidates:
            best = (c.get("candidates") or [{}])[0]
            posted = (best.get("posted_at") or "")[:10] or "?"
            txn_info = f"{best.get('amount', '?')} at {best.get('merchant', '?')} on {posted}"
            sections.append(
                f"- {c['payee']}: {c['amount']} due {c['due_date']} — possible match: {txn_info}"
            )

    if needs_action:
        needs_action_amount = totals.get("needs_action_amount", "0.00")
        sections.append(f"\n⚠️ Needs action ({len(needs_action)}) — {needs_action_amount}")
        sorted_items = sorted(
            needs_action,
            key=lambda x: (
                {"overdue": 0, "due_today": 1, "due_soon": 2}.get(x["urgency"], 3),
                -Decimal(str(x["bill"].get("amount", 0))),
            ),
        )
        for item in sorted_items:
            bill = item["bill"]
            days = item["days_until_due"]
            urg = item["urgency"]
            if urg == "overdue":
                urg_str = f"overdue {abs(days)} day{'s' if abs(days) != 1 else ''}"
            elif urg == "due_today":
                urg_str = "due today"
            else:
                urg_str = f"due in {days} day{'s' if days != 1 else ''}"
            sections.append(f"- {bill['payee']}: {bill['amount']} — {urg_str}")

    if autopay:
        sections.append("\n🔁 Auto-pays (no action)")
        for item in autopay:
            bill = item["bill"]
            sections.append(f"- {bill['payee']}: {bill['amount']}")

    if preds:
        sections.append("\n👀 Heads-up (predicted, not yet tracked)")
        for p in preds:
            sections.append(
                f"- {p['payee']}: ~{p['predicted_amount']} — expected {p['predicted_date']}"
            )

    return "\n".join(sections)
