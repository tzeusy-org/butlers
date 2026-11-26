"""Cross-butler calendar overlay contribution jobs.

Deterministic, **zero-LLM** scheduled jobs that precompute per-day overlay
envelopes for the calendar workspace's ``view=overlays`` read path. Each
contributing specialist butler (finance, travel, relationship, health) writes
per-date envelopes under state key ``calendar/overlay/<YYYY-MM-DD>`` carrying
domain-relevant entries (``bill_due``, ``subscription_renewal``, ``departure``,
``arrival``, ``check_in``, ``check_out``, ``birthday``, ``appointment``, ...).

The cross-schema view ``calendar.v_overlay_contributions`` (migration
``core_140``) unions these ``calendar/overlay/%`` keys for the workspace read
path. This module's envelope shape MUST match what that view's reader expects:

    {
        "butler":      "<schema>",       # string literal, matches the view's source col
        "date":        "YYYY-MM-DD",      # target calendar date (owner zone)
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

import hashlib
import logging
import re
from datetime import UTC, datetime, time, timedelta, tzinfo
from datetime import date as date_cls
from typing import Any, NotRequired, TypedDict

import asyncpg

from butlers.core.entity_posture import ACTIVE, MEMORIAL
from butlers.core.state import state_delete, state_get, state_list, state_set
from butlers.core.temporal.domain_claims import CLAIM_SCHEMA_VERSION, clip_label
from butlers.jobs.briefing import SGT, briefing_day, day_start_utc

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
#: How often every contribution job is scheduled (daily). An envelope older than twice this is
#: stale.
OVERLAY_CADENCE_SECONDS = 24 * 60 * 60

#: Priority-descending sort rank. Lower rank sorts first.
_PRIORITY_RANK: dict[str, int] = {"high": 0, "medium": 1, "low": 2}

#: How many days ahead (inclusive of today) the travel job writes envelopes for.
#: Travel itineraries are planned further out than monthly bills, so the travel
#: lookahead is wider than finance's. One envelope is written per date so empty
#: dates carry an honest ``has_entries=false`` rather than being absent.
TRAVEL_OVERLAY_LOOKAHEAD_DAYS = 30

#: Trip lifecycle statuses whose bookings surface on the overlay. Matches the
#: travel briefing contribution job (``planned``/``active`` only — completed and
#: cancelled trips are not actionable on a forward-looking overlay).
_TRAVEL_ACTIVE_STATUSES = ("planned", "active")

#: How many days ahead (inclusive of today) the relationship job writes envelopes
#: for. Birthdays/anniversaries are recurring annual dates worth a generous lead
#: time, so the relationship lookahead matches travel's wider window. One envelope
#: is written per date so empty dates carry an honest ``has_entries=false``.
RELATIONSHIP_OVERLAY_LOOKAHEAD_DAYS = 30

#: How many days ahead (inclusive of today) the health job writes envelopes for.
#: Medication reminders recur daily, so a tighter operational window (matching
#: finance's near-term cadence) keeps the daily-reminder volume sensible; the
#: rolling window advances every run, so appointments scheduled further out come
#: into view as their date approaches.
HEALTH_OVERLAY_LOOKAHEAD_DAYS = 14


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
    #: When the producing job wrote this envelope (UTC ISO-8601) and how often it runs; envelopes
    #: written before these existed lack them and read as stale.
    generated_at: NotRequired[str]
    cadence_seconds: NotRequired[int]
    #: ``False`` when the producer could not read all of its records; absent reads as complete.
    complete: NotRequired[bool]
    #: Schema version 2 only: one claim per domain record (see ``core.temporal.domain_claims``).
    schema_version: NotRequired[int]
    claims: NotRequired[list[dict[str, Any]]]


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


def _utcnow() -> datetime:
    """The instant an envelope is stamped with (a seam: tests pin it)."""
    return datetime.now(UTC)


async def write_overlay_envelopes(
    pool: asyncpg.Pool,
    *,
    butler: str,
    today: date_cls,
    lookahead_days: int,
    entries_by_date: dict[str, list[OverlayEntry]],
    complete: bool = True,
    claims_by_date: dict[str, list[dict[str, Any]]] | None = None,
) -> tuple[int, int]:
    """Upsert one overlay envelope per date in ``[today, today+lookahead_days]``.

    Every date in the window gets an envelope written (via ``state_set`` upsert),
    so dates with no domain events carry ``has_entries=false`` and ``entries=[]``
    — the honest empty-state contract. Entries are ordered priority-descending.

    ``complete=False`` marks every envelope partial (the producer could not read all its records).

    Returns ``(dates_written, total_entries)``.
    """
    dates_written = 0
    total_entries = 0
    generated_at = _utcnow().isoformat()
    for offset in range(lookahead_days + 1):
        date_str = (today + timedelta(days=offset)).isoformat()
        entries = sort_entries_by_priority(entries_by_date.get(date_str, []))
        envelope: OverlayContribution = {
            "butler": butler,
            "date": date_str,
            "has_entries": len(entries) > 0,
            "entries": entries,
            "generated_at": generated_at,
            "cadence_seconds": OVERLAY_CADENCE_SECONDS,
        }
        if not complete:
            envelope["complete"] = False
        if claims_by_date is not None:
            envelope["schema_version"] = CLAIM_SCHEMA_VERSION
            envelope["claims"] = claims_by_date.get(date_str, [])
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


#: Local hour a reminder claim fires at.
_REMINDER_HOUR = 9
#: Days before a bill is due that its reminder fires.
BILL_REMINDER_DAYS = 3
#: Days before an auto-renewing subscription renews that its reminder fires.
RENEWAL_REMINDER_DAYS = 7
#: Days before a subscription's ``cancel_by`` that its reminder fires.
CANCEL_BY_REMINDER_DAYS = 3


def _version(*parts: Any) -> str:
    """A short stable fingerprint of what a claim depends on (REQ ``record_version``)."""
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _row_id(row: Any) -> str | None:
    try:
        value = row["id"]
    except (KeyError, IndexError):
        return None
    return str(value) if value is not None else None


def _zone_name(zone: tzinfo) -> str:
    """The IANA name a claim carries for ``zone`` (SGT, the no-zone fallback, is Singapore)."""
    key = getattr(zone, "key", None)
    if isinstance(key, str):
        return key
    return "Asia/Singapore" if zone is SGT else "UTC"


def _claim(
    *,
    key: str,
    version: str,
    kind: str,
    label: str,
    priority: str,
    day: date_cls,
    remind_days: int | None,
    zone: tzinfo,
    now: datetime,
    ref: str,
) -> dict[str, Any]:
    """One claim: a reminder if its reminder moment is still ahead, else an overlay-only claim."""
    zone_name = _zone_name(zone)
    presence = "overlay"
    span: dict[str, Any] = {"date": day.isoformat(), "timezone": zone_name}
    if remind_days is not None:
        at = datetime.combine(day - timedelta(days=remind_days), time(_REMINDER_HOUR), tzinfo=zone)
        if at > now:
            presence = "reminder"
            span = {"start": at.isoformat(), "timezone": zone_name}
    return {
        "claim_key": key,
        "record_version": version,
        "kind": kind,
        "label": clip_label(label),
        "priority": priority,
        "presence": presence,
        "span": span,
        "owner_action": True,
        "status": "active",
        "door": {"route": "/finance", "ref": ref},
    }


MAX_REMINDER_DAYS = 30


def parse_reminder_days(content: Any) -> int | None:
    """Days from a ``bill_reminder_preference`` fact; None if none is stated.

    The preference is free text the owner's words were stored as: "5 days before due date" is 5,
    "2 weeks" is 14 (``7*N``, capped at ``MAX_REMINDER_DAYS``). A bare number of days beyond the cap
    is ignored (the default applies).
    """
    text = str(content or "")
    weeks = re.search(r"(\d+)\s*(?:weeks?|wks?)\b", text, re.IGNORECASE)
    if weeks is not None:
        return min(7 * int(weeks.group(1)), MAX_REMINDER_DAYS)
    match = re.search(r"\d+", text)
    if match is None:
        return None
    days = int(match.group())
    return days if 0 <= days <= MAX_REMINDER_DAYS else None


def finance_claims(
    bills: list[Any],
    subscriptions: list[Any],
    *,
    today: date_cls,
    zone: tzinfo,
    now: datetime,
    horizon: date_cls | None = None,
    bill_days: int = BILL_REMINDER_DAYS,
) -> dict[str, list[dict[str, Any]]]:
    """Claims for pending bills, subscription renewals and ``cancel_by`` dates, by date. Pure.

    A bill gets a reminder ``bill_days`` (the owner's ``bill_reminder_preference``, else
    ``BILL_REMINDER_DAYS``) before it is due, an auto-renewing subscription
    ``RENEWAL_REMINDER_DAYS`` before it renews, and a ``cancel_by`` date a reminder
    ``CANCEL_BY_REMINDER_DAYS`` before it. A reminder whose moment has passed is an overlay-only
    claim, so a late run never fires a reminder for the past. Rows without an id carry no claim.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for row in bills:
        ref = _row_id(row)
        if ref is None:
            continue
        due = row["due_date"]
        overdue = row["status"] == "overdue" or due <= today
        priority = "high" if overdue else "medium" if (due - today).days <= 3 else "low"
        out.setdefault(due.isoformat(), []).append(
            _claim(
                key=f"finance:bill:{ref}",
                version=_version(
                    due, row["amount"], row["currency"], row["status"], row["payee"], bill_days
                ),
                kind="bill_due",
                label=str(row["payee"]),
                priority=priority,
                day=due,
                remind_days=bill_days,
                zone=zone,
                now=now,
                ref=ref,
            )
        )
    for row in subscriptions:
        ref = _row_id(row)
        if ref is None:
            continue
        renew = row["next_renewal"]
        auto = bool(row["auto_renew"])
        # A subscription is read for its cancel_by too, so its renewal may lie outside the window.
        in_window = renew >= today and (horizon is None or renew <= horizon)
        if in_window:
            out.setdefault(renew.isoformat(), []).append(
                _claim(
                    key=f"finance:subscription:{ref}",
                    version=_version(renew, row["amount"], row["currency"], auto, row["service"]),
                    kind="subscription_renewal",
                    label=str(row["service"]),
                    priority="medium" if (renew - today).days <= 2 else "low",
                    day=renew,
                    remind_days=RENEWAL_REMINDER_DAYS if auto else None,
                    zone=zone,
                    now=now,
                    ref=ref,
                )
            )
        cancel_by = row["cancel_by"] if "cancel_by" in row.keys() else None  # noqa: SIM118
        if cancel_by is not None and today <= cancel_by:
            out.setdefault(cancel_by.isoformat(), []).append(
                _claim(
                    key=f"finance:subscription_cancel_by:{ref}",
                    version=_version(cancel_by, row["service"]),
                    kind="subscription_cancel_by",
                    label=f"Cancel {row['service']}",
                    priority="high",
                    day=cancel_by,
                    remind_days=CANCEL_BY_REMINDER_DAYS,
                    zone=zone,
                    now=now,
                    ref=ref,
                )
            )
    return out


async def mark_overlay_envelopes_incomplete(
    pool: asyncpg.Pool, *, today: date_cls, lookahead_days: int
) -> int:
    """Flag the window's existing envelopes ``complete=false``, leaving everything else as it was.

    Used when a producer could not read all its records: the previous entries and claims stand,
    ``generated_at`` is untouched, and the read side names the butler as ``partial``.
    """
    marked = 0
    for offset in range(lookahead_days + 1):
        key = overlay_key((today + timedelta(days=offset)).isoformat())
        existing = await state_get(pool, key)
        if isinstance(existing, dict) and existing.get("complete") is not False:
            await state_set(pool, key, {**existing, "complete": False})
            marked += 1
    return marked


async def reconcile_claims_in_process(
    pool: asyncpg.Pool,
    *,
    butler: str,
    claims: list[dict[str, Any]],
    complete: bool,
    window_start: datetime,
) -> dict[str, Any]:
    """Reconcile claims into the butler's own calendar rows; never raises (the job must finish).

    On success the result carries the per-outcome counts. On failure it carries
    ``{"error": "<ExceptionName>"}``; the caller treats that as a partial run.
    """
    try:
        from butlers.modules.calendar import CalendarModule

        module = CalendarModule.for_pool(pool, butler_name=butler)
        result = await module.reconcile_domain_claims(
            butler, claims, complete, window_start=window_start
        )
    except Exception as exc:
        logger.warning("%s calendar claims not reconciled", butler, exc_info=True)
        return {"error": type(exc).__name__}
    return {
        "created": len(result.created),
        "updated": len(result.updated),
        "revived": len(result.revived),
        "retracted": len(result.retracted),
        "unchanged": len(result.unchanged),
        "invalid": len(result.invalid),
        "retraction_ran": result.retraction_ran,
    }


async def settle_claims(
    pool: asyncpg.Pool,
    *,
    butler: str,
    claims: list[dict[str, Any]],
    complete: bool,
    window_start: datetime,
    today: date_cls,
    lookahead_days: int,
) -> tuple[dict[str, Any], bool]:
    """Reconcile, and let a failed reconcile make the run partial.

    Returns ``(claim_result, complete)``. When the reconcile raised, the window's envelopes are
    flagged incomplete (their entries and ``generated_at`` stand) and ``complete`` is False, so the
    overlay reads as partial instead of silently current while no claim reached the calendar.
    """
    claim_result = await reconcile_claims_in_process(
        pool, butler=butler, claims=claims, complete=complete, window_start=window_start
    )
    if complete and "error" not in claim_result:
        # An invalid claim, or a retraction that did not run (the projection tables are absent),
        # means the calendar was not fully reconciled: the run is partial.
        if claim_result.get("invalid") or not claim_result.get("retraction_ran"):
            claim_result = {**claim_result, "error": "invalid_claims"}
    if "error" in claim_result and complete:
        await mark_overlay_envelopes_incomplete(pool, today=today, lookahead_days=lookahead_days)
        complete = False
    return claim_result, complete


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

    today, zone = await briefing_day(pool)
    today_str = today.isoformat()
    horizon = today + timedelta(days=FINANCE_OVERLAY_LOOKAHEAD_DAYS)

    # A source that cannot be read makes the run partial: its previous entries and claims stand,
    # the envelopes are flagged incomplete, and nothing is retracted.
    complete = True

    # --- Bills due within the lookahead window ---
    try:
        bills_rows = await pool.fetch(
            """
            SELECT id, payee, amount, currency, due_date, status, payment_method
            FROM bills
            WHERE status IN ('pending', 'overdue')
              AND due_date >= $1
              AND due_date <= $2
            ORDER BY due_date ASC, amount DESC
            """,
            today,
            horizon,
        )
    except Exception:
        logger.warning("finance overlay: bills read failed; run is partial", exc_info=True)
        bills_rows, complete = [], False

    # --- Subscriptions renewing, or reaching cancel_by, within the lookahead window ---
    try:
        sub_rows = await pool.fetch(
            """
            SELECT id, service, amount, currency, next_renewal, auto_renew, frequency, cancel_by
            FROM subscriptions
            WHERE status = 'active'
              AND ((next_renewal >= $1 AND next_renewal <= $2)
                   OR (cancel_by >= $1 AND cancel_by <= $2))
            ORDER BY next_renewal ASC, amount DESC
            """,
            today,
            horizon,
        )
    except Exception:
        logger.warning("finance overlay: subscriptions read failed; run is partial", exc_info=True)
        sub_rows, complete = [], False

    # --- The owner's reminder preference (days before due); the default applies when unset ---
    bill_days = BILL_REMINDER_DAYS
    preference_failed = False
    try:
        pref = await pool.fetch(
            """
            SELECT content FROM facts
            WHERE predicate = 'bill_reminder_preference' AND validity = 'active'
            ORDER BY created_at DESC
            LIMIT 1
            """
        )
        preferred = parse_reminder_days(pref[0]["content"]) if pref else None
        if preferred is not None:
            bill_days = preferred
    except asyncpg.UndefinedTableError:
        pass  # a finance without the memory tables has no preference to honour
    except Exception:
        logger.warning(
            "finance overlay: reminder preference read failed; run is partial", exc_info=True
        )
        complete = False
        preference_failed = True

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
        if renew > horizon or renew < today:
            continue  # selected for its cancel_by only
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

    claims_by_date = finance_claims(
        bills_rows,
        sub_rows,
        today=today,
        zone=zone,
        now=_utcnow(),
        horizon=horizon,
        bill_days=bill_days,
    )
    if preference_failed:
        # The bill reminder's lead time is unknown this run: leave the bill rows as they are
        # rather than reconciling them with the default.
        claims_by_date = {
            day: [c for c in claims if not c["claim_key"].startswith("finance:bill:")]
            for day, claims in claims_by_date.items()
        }
    for row in sub_rows:
        cancel_by = row["cancel_by"] if "cancel_by" in row.keys() else None  # noqa: SIM118
        if cancel_by is not None and today <= cancel_by <= horizon:
            entries_by_date.setdefault(cancel_by.isoformat(), []).append(
                {
                    "kind": "subscription_cancel_by",
                    "label": f"Cancel {row['service']}",
                    "priority": "high",
                    "meta": {},
                }
            )

    if complete:
        dates_written, total_entries = await write_overlay_envelopes(
            pool,
            butler="finance",
            today=today,
            lookahead_days=FINANCE_OVERLAY_LOOKAHEAD_DAYS,
            entries_by_date=entries_by_date,
            claims_by_date=claims_by_date,
        )
    else:
        dates_written = await mark_overlay_envelopes_incomplete(
            pool, today=today, lookahead_days=FINANCE_OVERLAY_LOOKAHEAD_DAYS
        )
        total_entries = 0
    claim_result, complete = await settle_claims(
        pool,
        butler="finance",
        claims=[c for day in claims_by_date.values() for c in day],
        complete=complete,
        window_start=day_start_utc(today, zone),
        today=today,
        lookahead_days=FINANCE_OVERLAY_LOOKAHEAD_DAYS,
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
        "complete": complete,
        "claims": claim_result,
    }


# ---------------------------------------------------------------------------
# Travel overlay contribution job
# ---------------------------------------------------------------------------


def _local_date_str(ts: datetime, zone: tzinfo) -> str:
    """Return the owner-zone calendar date (``YYYY-MM-DD``) a tz-aware timestamp falls on."""
    return ts.astimezone(zone).date().isoformat()


def _departure_priority(days_until: int) -> str:
    """Departure urgency: same/next day is high, within 3 days medium, else low."""
    if days_until <= 1:
        return "high"
    if days_until <= 3:
        return "medium"
    return "low"


def _checkin_priority(days_until: int) -> str:
    """Check-in urgency mirrors departure: same/next day high, within 3 medium."""
    return _departure_priority(days_until)


async def run_travel_calendar_overlay_contribution(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Travel butler calendar overlay contribution job (deterministic, zero LLM).

    Queries the travel schema's own ``legs`` and ``accommodations`` tables (joined
    to ``trips`` for active/planned status) for itinerary events falling within the
    rolling lookahead window and writes per-date overlay envelopes under
    ``calendar/overlay/<date>``:

    - ``departure`` entries for transport legs departing on a date in the window.
    - ``arrival`` entries for transport legs arriving on a date in the window.
    - ``check_in`` entries for accommodations whose check-in lands in the window.
    - ``check_out`` entries for accommodations whose check-out lands in the window.

    Timestamps are ``TIMESTAMPTZ``; each event is bucketed onto the owner-zone calendar
    date it falls on. Each date in the window gets an envelope
    (``has_entries=false`` when empty), entries are ordered priority-descending,
    writes upsert via ``state_set``, and stale past-date entries are pruned. No
    LLM session is spawned.
    """
    del job_args

    today, zone = await briefing_day(pool)
    today_str = today.isoformat()

    # owner-local midnight window boundaries converted to UTC for TIMESTAMPTZ comparison.
    # ``window_end`` is the exclusive owner-local midnight after the last lookahead date,
    # so every in-window timestamp buckets onto a date in [today, today+lookahead].
    local_midnight = datetime(today.year, today.month, today.day, tzinfo=zone)
    window_start = day_start_utc(today, zone)
    window_end = (local_midnight + timedelta(days=TRAVEL_OVERLAY_LOOKAHEAD_DAYS + 1)).astimezone(
        UTC
    )

    # --- Transport legs departing or arriving within the window ---
    leg_rows = await pool.fetch(
        """
        SELECT l.type, l.carrier, l.departure_city, l.arrival_city,
               l.departure_at, l.arrival_at, l.pnr, l.seat, l.confirmation_number,
               t.name AS trip_name, t.id AS trip_id
        FROM travel.legs l
        JOIN travel.trips t ON t.id = l.trip_id
        WHERE t.status = ANY($3::text[])
          AND (
            (l.departure_at >= $1 AND l.departure_at < $2)
            OR (l.arrival_at >= $1 AND l.arrival_at < $2)
          )
        ORDER BY l.departure_at ASC
        """,
        window_start,
        window_end,
        list(_TRAVEL_ACTIVE_STATUSES),
    )

    # --- Accommodations checking in or out within the window ---
    accom_rows = await pool.fetch(
        """
        SELECT a.type, a.name, a.address, a.check_in, a.check_out,
               a.confirmation_number, t.name AS trip_name, t.id AS trip_id
        FROM travel.accommodations a
        JOIN travel.trips t ON t.id = a.trip_id
        WHERE t.status = ANY($3::text[])
          AND (
            (a.check_in >= $1 AND a.check_in < $2)
            OR (a.check_out >= $1 AND a.check_out < $2)
          )
        ORDER BY a.check_in ASC NULLS LAST
        """,
        window_start,
        window_end,
        list(_TRAVEL_ACTIVE_STATUSES),
    )

    entries_by_date: dict[str, list[OverlayEntry]] = {}
    departure_count = arrival_count = check_in_count = check_out_count = 0

    def _add(date_str: str, entry: OverlayEntry) -> None:
        entries_by_date.setdefault(date_str, []).append(entry)

    def _in_window(ts: datetime | None) -> bool:
        return ts is not None and window_start <= ts < window_end

    for row in leg_rows:
        trip_id = str(row["trip_id"])
        carrier = row["carrier"] or row["type"]

        if _in_window(row["departure_at"]):
            date_str = _local_date_str(row["departure_at"], zone)
            days_until = (date_cls.fromisoformat(date_str) - today).days
            origin = row["departure_city"] or "?"
            dest = row["arrival_city"] or "?"
            _add(
                date_str,
                {
                    "kind": "departure",
                    "label": f"{origin} → {dest}",
                    "priority": _departure_priority(days_until),
                    "meta": {
                        "trip_id": trip_id,
                        "trip_name": row["trip_name"],
                        "type": row["type"],
                        "carrier": carrier,
                        "departure_city": row["departure_city"],
                        "arrival_city": row["arrival_city"],
                        "departure_at": row["departure_at"].isoformat(),
                        "arrival_at": row["arrival_at"].isoformat(),
                        "pnr": row["pnr"],
                        "seat": row["seat"],
                        "confirmation_number": row["confirmation_number"],
                    },
                },
            )
            departure_count += 1

        if _in_window(row["arrival_at"]):
            date_str = _local_date_str(row["arrival_at"], zone)
            days_until = (date_cls.fromisoformat(date_str) - today).days
            dest = row["arrival_city"] or "?"
            _add(
                date_str,
                {
                    "kind": "arrival",
                    "label": f"Arrive {dest}",
                    "priority": "medium" if days_until <= 1 else "low",
                    "meta": {
                        "trip_id": trip_id,
                        "trip_name": row["trip_name"],
                        "type": row["type"],
                        "carrier": carrier,
                        "arrival_city": row["arrival_city"],
                        "arrival_at": row["arrival_at"].isoformat(),
                        "pnr": row["pnr"],
                    },
                },
            )
            arrival_count += 1

    for row in accom_rows:
        trip_id = str(row["trip_id"])
        name = row["name"] or row["type"]

        if _in_window(row["check_in"]):
            date_str = _local_date_str(row["check_in"], zone)
            days_until = (date_cls.fromisoformat(date_str) - today).days
            _add(
                date_str,
                {
                    "kind": "check_in",
                    "label": f"Check-in: {name}",
                    "priority": _checkin_priority(days_until),
                    "meta": {
                        "trip_id": trip_id,
                        "trip_name": row["trip_name"],
                        "accommodation_type": row["type"],
                        "name": row["name"],
                        "address": row["address"],
                        "check_in": row["check_in"].isoformat(),
                        "confirmation_number": row["confirmation_number"],
                    },
                },
            )
            check_in_count += 1

        if _in_window(row["check_out"]):
            date_str = _local_date_str(row["check_out"], zone)
            days_until = (date_cls.fromisoformat(date_str) - today).days
            _add(
                date_str,
                {
                    "kind": "check_out",
                    "label": f"Check-out: {name}",
                    "priority": "medium" if days_until <= 1 else "low",
                    "meta": {
                        "trip_id": trip_id,
                        "trip_name": row["trip_name"],
                        "accommodation_type": row["type"],
                        "name": row["name"],
                        "check_out": row["check_out"].isoformat(),
                    },
                },
            )
            check_out_count += 1

    dates_written, total_entries = await write_overlay_envelopes(
        pool,
        butler="travel",
        today=today,
        lookahead_days=TRAVEL_OVERLAY_LOOKAHEAD_DAYS,
        entries_by_date=entries_by_date,
    )

    pruned = await prune_old_overlay_contributions(pool, today=today_str)

    logger.info(
        "Travel calendar overlay contribution: date=%s dates_written=%d "
        "departures=%d arrivals=%d check_ins=%d check_outs=%d entries=%d pruned=%d",
        today_str,
        dates_written,
        departure_count,
        arrival_count,
        check_in_count,
        check_out_count,
        total_entries,
        pruned,
    )

    return {
        "butler": "travel",
        "date": today_str,
        "dates_written": dates_written,
        "departure_entries": departure_count,
        "arrival_entries": arrival_count,
        "check_in_entries": check_in_count,
        "check_out_entries": check_out_count,
        "total_entries": total_entries,
        "pruned": pruned,
    }


# ---------------------------------------------------------------------------
# Relationship overlay contribution job
# ---------------------------------------------------------------------------


def _annual_date_index(today: date_cls, lookahead_days: int) -> dict[tuple[int, int], date_cls]:
    """Map each ``(month, day)`` in the window to the concrete date it lands on.

    Birthdays and important dates are stored as recurring ``(month, day)`` pairs
    (year-agnostic). The lookahead window (≤ ~31 days) crosses at most one
    month/year boundary, so any ``(month, day)`` occurs at most once within it —
    ``setdefault`` keeps the earliest occurrence if one ever repeated.
    """
    index: dict[tuple[int, int], date_cls] = {}
    for offset in range(lookahead_days + 1):
        d = today + timedelta(days=offset)
        index.setdefault((d.month, d.day), d)
    return index


def _annual_date_priority(days_until: int) -> str:
    """Birthday/important-date urgency: today high, within a week medium, else low."""
    if days_until <= 0:
        return "high"
    if days_until <= 7:
        return "medium"
    return "low"


def _follow_up_priority(days_until: int) -> str:
    """Follow-up urgency: due/overdue high, within 3 days medium, else low."""
    if days_until <= 0:
        return "high"
    if days_until <= 3:
        return "medium"
    return "low"


async def run_relationship_calendar_overlay_contribution(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Relationship butler calendar overlay contribution job (deterministic, zero LLM).

    Queries the relationship schema's own date-keyed data for events falling
    within the rolling lookahead window and writes per-date overlay envelopes
    under ``calendar/overlay/<date>``:

    - ``birthday`` entries for ``important_dates`` rows labelled as a birthday,
      recurring on their ``(month, day)``.
    - ``important_date`` entries for all other ``important_dates`` rows
      (anniversaries, milestones, …), recurring on their ``(month, day)``.
    - ``follow_up`` entries for active reminder facts (``predicate='reminder'``)
      whose next trigger falls within the window; overdue reminders clamp to
      today as high priority.

    Both ``important_dates`` arms mirror the relationship briefing contribution:
    the contact-anchored path (``contact_id`` → ``contact_entity_map`` →
    ``entities``) and the entity-anchored path (``local_entity_id`` → ``entities``),
    each gated on ``entities.listed = true``. Posture (bu-q7vx1q.8) then shapes each
    row: ``active`` people keep their ``birthday``/``important_date`` entries, a
    ``memorial`` person's birthday becomes a low-priority ``remembrance`` entry
    (no other date of theirs surfaces), and ``quiet``/``no_contact`` people
    contribute nothing. Each date in the window gets an
    envelope (``has_entries=false`` when empty), entries are ordered
    priority-descending, writes upsert via ``state_set``, and stale past-date
    entries are pruned. No LLM session is spawned.
    """
    del job_args

    today, zone = await briefing_day(pool)
    today_str = today.isoformat()
    lookahead = RELATIONSHIP_OVERLAY_LOOKAHEAD_DAYS

    # (month, day) → concrete in-window date, plus the parallel arrays the SQL
    # uses to filter important_dates down to the window without a recurrence calc
    # in SQL.
    date_index = _annual_date_index(today, lookahead)
    months = [(today + timedelta(days=i)).month for i in range(lookahead + 1)]
    days = [(today + timedelta(days=i)).day for i in range(lookahead + 1)]

    # --- Birthdays + important dates recurring within the window ---
    # Dual-path UNION (mirrors run_relationship_briefing_contribution): contact-
    # anchored via contact_entity_map and entity-anchored via local_entity_id.
    # Unlike the briefing, this query keeps ALL labels (not just birthdays); the
    # birthday/important_date split happens in Python by label.
    date_rows = await pool.fetch(
        """
        SELECT COALESCE(e.canonical_name, 'Unknown') AS name, id.label, id.month, id.day, id.year,
               e.posture
        FROM important_dates id
        JOIN contact_entity_map cem ON cem.contact_id = id.contact_id
        JOIN public.entities e ON e.id = cem.entity_id
        WHERE id.contact_id IS NOT NULL
          AND e.listed = true
          AND EXISTS (
            SELECT 1 FROM unnest($1::int[], $2::int[]) AS t(m, d)
            WHERE t.m = id.month AND t.d = id.day
          )

        UNION ALL

        SELECT COALESCE(e.canonical_name, 'Unknown') AS name,
               id.label, id.month, id.day, id.year, e.posture
        FROM important_dates id
        JOIN public.entities e ON e.id = id.local_entity_id
        WHERE id.contact_id IS NULL
          AND id.local_entity_id IS NOT NULL
          AND e.listed = true
          AND EXISTS (
            SELECT 1 FROM unnest($1::int[], $2::int[]) AS t(m, d)
            WHERE t.m = id.month AND t.d = id.day
          )

        ORDER BY month, day, name
        """,
        months,
        days,
    )

    # --- Follow-up reminders due within the window (and overdue) ---
    # Reminders are SPO facts (predicate='reminder'); the contact UUID is embedded
    # in the subject key as "contact:{uuid}:reminder:{...}" and resolved to a name
    # via contact_entity_map → entities. window_end is the exclusive owner-local midnight
    # after the last lookahead date, converted to UTC for the TIMESTAMPTZ compare.
    local_midnight = datetime(today.year, today.month, today.day, tzinfo=zone)
    window_end = (local_midnight + timedelta(days=lookahead + 1)).astimezone(UTC)

    reminder_rows = await pool.fetch(
        """
        SELECT COALESCE(e.canonical_name, 'Unknown') AS name,
               f.content AS label,
               COALESCE(
                   (f.metadata->>'next_trigger_at')::timestamptz,
                   (f.metadata->>'due_at')::timestamptz
               ) AS trigger_at
        FROM facts f
        JOIN contact_entity_map cem
          ON cem.contact_id = (split_part(f.subject, ':', 2))::uuid
        JOIN public.entities e ON e.id = cem.entity_id
        WHERE f.predicate = 'reminder'
          AND f.scope = 'relationship'
          AND f.validity = 'active'
          AND f.valid_at IS NULL
          AND COALESCE((f.metadata->>'dismissed')::boolean, false) = false
          AND COALESCE(
                  (f.metadata->>'next_trigger_at')::timestamptz,
                  (f.metadata->>'due_at')::timestamptz
              ) IS NOT NULL
          AND COALESCE(
                  (f.metadata->>'next_trigger_at')::timestamptz,
                  (f.metadata->>'due_at')::timestamptz
              ) < $1
        ORDER BY trigger_at ASC
        """,
        window_end,
    )

    entries_by_date: dict[str, list[OverlayEntry]] = {}
    birthday_count = important_date_count = follow_up_count = remembrance_count = 0

    for row in date_rows:
        landing = date_index.get((row["month"], row["day"]))
        if landing is None:
            continue
        date_str = landing.isoformat()
        days_until = (landing - today).days
        raw_label = row["label"]
        is_birthday = "birthday" in (raw_label or "").lower()
        posture = row["posture"]
        if posture == MEMORIAL and is_birthday:
            entries_by_date.setdefault(date_str, []).append(
                {
                    "kind": "remembrance",
                    "label": f"{row['name']}'s birthday (remembrance)",
                    "priority": "low",
                    "meta": {"person": row["name"], "month": row["month"], "day": row["day"]},
                }
            )
            remembrance_count += 1
            continue
        if posture != ACTIVE:
            continue
        kind = "birthday" if is_birthday else "important_date"
        entry: OverlayEntry = {
            "kind": kind,
            "label": row["name"],
            "priority": _annual_date_priority(days_until),
            "meta": {
                "person": row["name"],
                "occasion": raw_label,
                "month": row["month"],
                "day": row["day"],
                "year": row["year"],
            },
        }
        entries_by_date.setdefault(date_str, []).append(entry)
        if is_birthday:
            birthday_count += 1
        else:
            important_date_count += 1

    for row in reminder_rows:
        trigger = row["trigger_at"]
        local_date = trigger.astimezone(zone).date()
        # Overdue reminders (trigger before today) clamp onto today as high-priority.
        landing = today if local_date < today else local_date
        date_str = landing.isoformat()
        days_until = (landing - today).days
        is_overdue = local_date < today
        entry = {
            "kind": "follow_up",
            "label": row["label"] or "follow-up",
            "priority": _follow_up_priority(days_until),
            "meta": {
                "person": row["name"],
                "due_at": trigger.isoformat(),
                "overdue": is_overdue,
            },
        }
        entries_by_date.setdefault(date_str, []).append(entry)
        follow_up_count += 1

    dates_written, total_entries = await write_overlay_envelopes(
        pool,
        butler="relationship",
        today=today,
        lookahead_days=lookahead,
        entries_by_date=entries_by_date,
    )

    pruned = await prune_old_overlay_contributions(pool, today=today_str)

    logger.info(
        "Relationship calendar overlay contribution: date=%s dates_written=%d "
        "birthdays=%d important_dates=%d follow_ups=%d entries=%d pruned=%d",
        today_str,
        dates_written,
        birthday_count,
        important_date_count,
        follow_up_count,
        total_entries,
        pruned,
    )

    return {
        "butler": "relationship",
        "date": today_str,
        "dates_written": dates_written,
        "birthday_entries": birthday_count,
        "important_date_entries": important_date_count,
        "follow_up_entries": follow_up_count,
        "remembrance_entries": remembrance_count,
        "total_entries": total_entries,
        "pruned": pruned,
    }


# ---------------------------------------------------------------------------
# Health overlay contribution job
# ---------------------------------------------------------------------------


def _appointment_priority(days_until: int) -> str:
    """Appointment urgency: same/next day high, within 3 days medium, else low."""
    if days_until <= 1:
        return "high"
    if days_until <= 3:
        return "medium"
    return "low"


async def run_health_calendar_overlay_contribution(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Health butler calendar overlay contribution job (deterministic, zero LLM).

    Queries the health schema's own ``facts`` table (the butler's memory-backed
    store of record for medications and appointments — the legacy relational
    ``health.medications`` table is orphaned) and writes per-date overlay
    envelopes under ``calendar/overlay/<date>``:

    - ``appointment`` entries for appointment temporal facts (``predicate =
      'appointment'``) whose ``valid_at`` falls within the lookahead window,
      bucketed onto the owner-zone calendar date they land on.
    - ``medication_reminder`` entries for active medication property facts
      (``predicate = 'medication'``, ``metadata->>'active' = true``). Medications
      recur daily, so one reminder is emitted per active medication on **every**
      date in the lookahead window.

    Each date in the window gets an envelope (``has_entries=false`` when empty),
    entries are ordered priority-descending, writes upsert via ``state_set``, and
    stale past-date entries are pruned. No LLM session is spawned.
    """
    del job_args

    today, zone = await briefing_day(pool)
    today_str = today.isoformat()

    # owner-local midnight window boundaries converted to UTC for TIMESTAMPTZ comparison.
    # ``window_end`` is the exclusive owner-local midnight after the last lookahead date,
    # so every in-window appointment buckets onto a date in [today, today+lookahead].
    local_midnight = datetime(today.year, today.month, today.day, tzinfo=zone)
    window_start = day_start_utc(today, zone)
    window_end = (local_midnight + timedelta(days=HEALTH_OVERLAY_LOOKAHEAD_DAYS + 1)).astimezone(
        UTC
    )

    # --- Appointments: temporal facts whose valid_at lands within the window ---
    appointment_rows = await pool.fetch(
        """
        SELECT id, content, valid_at, metadata
        FROM facts
        WHERE predicate = 'appointment'
          AND scope = 'health'
          AND validity = 'active'
          AND valid_at >= $1
          AND valid_at < $2
        ORDER BY valid_at ASC
        """,
        window_start,
        window_end,
    )

    # --- Active medications: property facts that recur daily ---
    medication_rows = await pool.fetch(
        """
        SELECT id,
               metadata->>'name' AS name,
               metadata->>'dosage' AS dosage,
               metadata->>'frequency' AS frequency,
               metadata->'schedule' AS schedule
        FROM facts
        WHERE predicate = 'medication'
          AND scope = 'health'
          AND validity = 'active'
          AND (metadata->>'active')::boolean = true
        ORDER BY metadata->>'name'
        """,
    )

    entries_by_date: dict[str, list[OverlayEntry]] = {}

    def _add(date_str: str, entry: OverlayEntry) -> None:
        entries_by_date.setdefault(date_str, []).append(entry)

    appointment_count = 0
    for row in appointment_rows:
        valid_at: datetime = row["valid_at"]
        if valid_at is None or not (window_start <= valid_at < window_end):
            continue
        date_str = _local_date_str(valid_at, zone)
        days_until = (date_cls.fromisoformat(date_str) - today).days
        meta = dict(row["metadata"] or {})
        title = meta.get("title") or meta.get("name") or row["content"] or "Appointment"
        entry: OverlayEntry = {
            "kind": "appointment",
            "label": str(title),
            "priority": _appointment_priority(days_until),
            "meta": {
                "appointment_id": str(row["id"]),
                "starts_at": valid_at.isoformat(),
                "location": meta.get("location"),
                "provider": meta.get("provider") or meta.get("doctor"),
                "notes": meta.get("notes"),
            },
        }
        _add(date_str, entry)
        appointment_count += 1

    # Medication reminders recur daily across the whole window.
    medication_count = len(medication_rows)
    if medication_count:
        for offset in range(HEALTH_OVERLAY_LOOKAHEAD_DAYS + 1):
            date_str = (today + timedelta(days=offset)).isoformat()
            for row in medication_rows:
                name = row["name"] or "Medication"
                _add(
                    date_str,
                    {
                        "kind": "medication_reminder",
                        "label": str(name),
                        "priority": "low",
                        "meta": {
                            "medication_id": str(row["id"]),
                            "dosage": row["dosage"],
                            "frequency": row["frequency"],
                            "schedule": row["schedule"] or [],
                        },
                    },
                )

    dates_written, total_entries = await write_overlay_envelopes(
        pool,
        butler="health",
        today=today,
        lookahead_days=HEALTH_OVERLAY_LOOKAHEAD_DAYS,
        entries_by_date=entries_by_date,
    )

    pruned = await prune_old_overlay_contributions(pool, today=today_str)

    logger.info(
        "Health calendar overlay contribution: date=%s dates_written=%d "
        "appointments=%d medications=%d entries=%d pruned=%d",
        today_str,
        dates_written,
        appointment_count,
        medication_count,
        total_entries,
        pruned,
    )

    return {
        "butler": "health",
        "date": today_str,
        "dates_written": dates_written,
        "appointment_entries": appointment_count,
        "medication_reminder_medications": medication_count,
        "total_entries": total_entries,
        "pruned": pruned,
    }
