"""Cross-butler calendar overlay contribution jobs.

Deterministic, **zero-LLM** scheduled jobs that precompute per-day overlay
envelopes for the calendar workspace's ``view=overlays`` read path. Each
contributing specialist butler (finance, travel, relationship, health) writes
per-date envelopes under state key ``calendar/overlay/<YYYY-MM-DD>`` carrying
domain-relevant entries (``bill_due``, ``subscription_renewal``, ``departure``,
``birthday``, ``appointment``, ...).

The cross-schema view ``calendar.v_overlay_contributions`` (migration
``core_140``) unions these ``calendar/overlay/%`` keys for the workspace read
path. This module's envelope shape MUST match what that view's reader expects:

    {
        "butler":      "<schema>",       # string literal, matches the view's source col
        "date":        "YYYY-MM-DD",      # target calendar date (SGT)
        "has_entries": <bool>,            # honest empty-state flag
        "entries": [
            {"kind": "<str>", "label": "<str>", "priority": "high|medium|low",
             "meta": { ... optional kind-specific ... }},
            ...                            # ordered priority-descending
        ],
    }

There is **no** ``summary`` field in the v1 envelope (RFC-0020 adopted the
no-LLM structured variant; the batched pre-rendered narrative layer is deferred
to ``bu-jdrkbj``).

This module REUSES the briefing contribution pattern (``butlers.jobs.briefing``):
the same ``state`` store, the same key-suffix prune approach, and the same
``_DETERMINISTIC_SCHEDULE_JOB_REGISTRY``. It is NOT a parallel scheduler.

Design reference: openspec/changes/calendar-cross-domain-overlays/
"""

from __future__ import annotations

import logging
from datetime import date as date_cls
from datetime import timedelta
from typing import Any, TypedDict

import asyncpg

from butlers.core.state import state_delete, state_list, state_set
from butlers.jobs.briefing import today_sgt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: State key prefix bounding overlay contributions away from briefing keys.
#: Mirrors ``calendar.v_overlay_contributions``'s ``key LIKE 'calendar/overlay/%'``
#: filter (RFC 0010 Guardrail #3).
OVERLAY_KEY_PREFIX = "calendar/overlay/"

#: How many days ahead (inclusive of today) the finance job writes envelopes for.
#: A bounded rolling lookahead window; one envelope is written per date so empty
#: dates carry an honest ``has_entries=false`` rather than being absent.
FINANCE_OVERLAY_LOOKAHEAD_DAYS = 14

#: Retention window for prune: overlay envelopes whose date suffix is older than
#: ``today - OVERLAY_RETENTION_DAYS`` are deleted. ``0`` means "prune everything
#: strictly before today" (a forward-looking overlay has no use for past dates).
OVERLAY_RETENTION_DAYS = 0

#: Priority-descending sort rank. Lower rank sorts first.
_PRIORITY_RANK: dict[str, int] = {"high": 0, "medium": 1, "low": 2}


# ---------------------------------------------------------------------------
# Envelope schema
# ---------------------------------------------------------------------------


class _OverlayEntryRequired(TypedDict):
    kind: str
    label: str
    priority: str  # "high" | "medium" | "low"


class OverlayEntry(_OverlayEntryRequired, total=False):
    """A single overlay entry within a contribution envelope.

    ``meta`` is an optional kind-specific object the overlay read layer does not
    interpret (passed through to the FE verbatim).
    """

    meta: dict[str, Any]


class OverlayContribution(TypedDict):
    """Standard per-date overlay contribution envelope.

    Matches the shape ``calendar.v_overlay_contributions`` exposes as ``value``.
    """

    butler: str
    date: str  # ISO date YYYY-MM-DD
    has_entries: bool
    entries: list[OverlayEntry]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def overlay_key(date: str) -> str:
    """Return the state store key for an overlay contribution on *date*."""
    return f"{OVERLAY_KEY_PREFIX}{date}"


def sort_entries_by_priority(entries: list[OverlayEntry]) -> list[OverlayEntry]:
    """Return *entries* ordered priority-descending (high → medium → low).

    The sort is stable, so entries of equal priority keep their insertion order
    (the SQL query's ordering, e.g. due_date ASC then amount DESC).
    """
    return sorted(entries, key=lambda e: _PRIORITY_RANK.get(e["priority"], len(_PRIORITY_RANK)))


async def write_overlay_envelopes(
    pool: asyncpg.Pool,
    *,
    butler: str,
    today: date_cls,
    lookahead_days: int,
    entries_by_date: dict[str, list[OverlayEntry]],
) -> tuple[int, int]:
    """Upsert one overlay envelope per date in ``[today, today+lookahead_days]``.

    Every date in the window gets an envelope written (via ``state_set`` upsert),
    so dates with no domain events carry ``has_entries=false`` and ``entries=[]``
    — the honest empty-state contract. Entries are ordered priority-descending.

    Returns ``(dates_written, total_entries)``.
    """
    dates_written = 0
    total_entries = 0
    for offset in range(lookahead_days + 1):
        date_str = (today + timedelta(days=offset)).isoformat()
        entries = sort_entries_by_priority(entries_by_date.get(date_str, []))
        envelope: OverlayContribution = {
            "butler": butler,
            "date": date_str,
            "has_entries": len(entries) > 0,
            "entries": entries,
        }
        await state_set(pool, overlay_key(date_str), envelope)
        dates_written += 1
        total_entries += len(entries)
    return dates_written, total_entries


async def prune_old_overlay_contributions(pool: asyncpg.Pool, *, today: str) -> int:
    """Delete overlay envelopes whose date suffix is older than the retention window.

    Mirrors ``briefing.delete_old_contributions``: collect ``calendar/overlay/*``
    keys, parse each date suffix, and delete those before
    ``today - OVERLAY_RETENTION_DAYS``. A no-op when there is nothing to prune.

    Returns the number of deleted entries.
    """
    cutoff = date_cls.fromisoformat(today) - timedelta(days=OVERLAY_RETENTION_DAYS)

    keys: list[str] = await state_list(pool, prefix=OVERLAY_KEY_PREFIX)  # type: ignore[assignment]
    pruned = 0
    for key in keys:
        date_suffix = key[len(OVERLAY_KEY_PREFIX) :]
        try:
            entry_date = date_cls.fromisoformat(date_suffix)
        except ValueError:
            continue
        if entry_date < cutoff:
            await state_delete(pool, key)
            pruned += 1
            logger.debug("Pruned stale overlay contribution key: %s", key)
    return pruned


def _normalize_currency(raw: Any) -> Any:
    """Trim CHAR(3) currency padding; pass non-str values through unchanged."""
    return raw.strip() if isinstance(raw, str) else raw


# ---------------------------------------------------------------------------
# Finance overlay contribution job
# ---------------------------------------------------------------------------


async def run_finance_calendar_overlay_contribution(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Finance butler calendar overlay contribution job (deterministic, zero LLM).

    Queries the finance schema's own ``bills`` and ``subscriptions`` tables for
    obligations falling within the rolling lookahead window and writes per-date
    overlay envelopes under ``calendar/overlay/<date>``:

    - ``bill_due`` entries for bills (status ``pending``/``overdue``) due on a
      date in the window.
    - ``subscription_renewal`` entries for active subscriptions renewing on a
      date in the window.

    Each date in the window gets an envelope (``has_entries=false`` when empty),
    entries are ordered priority-descending, writes upsert via ``state_set``, and
    stale past-date entries are pruned. No LLM session is spawned.
    """
    del job_args

    today = today_sgt()
    today_str = today.isoformat()
    horizon = today + timedelta(days=FINANCE_OVERLAY_LOOKAHEAD_DAYS)

    # --- Bills due within the lookahead window ---
    bills_rows = await pool.fetch(
        """
        SELECT payee, amount, currency, due_date, status, payment_method
        FROM bills
        WHERE status IN ('pending', 'overdue')
          AND due_date >= $1
          AND due_date <= $2
        ORDER BY due_date ASC, amount DESC
        """,
        today,
        horizon,
    )

    # --- Subscriptions renewing within the lookahead window ---
    sub_rows = await pool.fetch(
        """
        SELECT service, amount, currency, next_renewal, auto_renew, frequency
        FROM subscriptions
        WHERE status = 'active'
          AND next_renewal >= $1
          AND next_renewal <= $2
        ORDER BY next_renewal ASC, amount DESC
        """,
        today,
        horizon,
    )

    entries_by_date: dict[str, list[OverlayEntry]] = {}

    for row in bills_rows:
        due = row["due_date"]
        date_str = due.isoformat()
        if row["status"] == "overdue" or due <= today:
            priority = "high"
        elif (due - today).days <= 3:
            priority = "medium"
        else:
            priority = "low"
        entry: OverlayEntry = {
            "kind": "bill_due",
            "label": str(row["payee"]),
            "priority": priority,
            "meta": {
                "amount": float(row["amount"]),
                "currency": _normalize_currency(row["currency"]),
                "status": row["status"],
                "payment_method": row["payment_method"],
            },
        }
        entries_by_date.setdefault(date_str, []).append(entry)

    for row in sub_rows:
        renew = row["next_renewal"]
        date_str = renew.isoformat()
        days_until = (renew - today).days
        priority = "medium" if days_until <= 2 else "low"
        entry = {
            "kind": "subscription_renewal",
            "label": str(row["service"]),
            "priority": priority,
            "meta": {
                "amount": float(row["amount"]),
                "currency": _normalize_currency(row["currency"]),
                "auto_renew": bool(row["auto_renew"]),
                "frequency": row["frequency"],
            },
        }
        entries_by_date.setdefault(date_str, []).append(entry)

    dates_written, total_entries = await write_overlay_envelopes(
        pool,
        butler="finance",
        today=today,
        lookahead_days=FINANCE_OVERLAY_LOOKAHEAD_DAYS,
        entries_by_date=entries_by_date,
    )

    pruned = await prune_old_overlay_contributions(pool, today=today_str)

    logger.info(
        "Finance calendar overlay contribution: date=%s dates_written=%d "
        "bills=%d subs=%d entries=%d pruned=%d",
        today_str,
        dates_written,
        len(bills_rows),
        len(sub_rows),
        total_entries,
        pruned,
    )

    return {
        "butler": "finance",
        "date": today_str,
        "dates_written": dates_written,
        "bill_due_entries": len(bills_rows),
        "subscription_renewal_entries": len(sub_rows),
        "total_entries": total_entries,
        "pruned": pruned,
    }
