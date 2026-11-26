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
from butlers.jobs.briefing import briefing_day, day_start_utc

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
    #: The domain time claim this entry shows (``<domain>:<kind>:<id>``), so the workspace can show
    #: a record once when its claim also owns a calendar row, and offer a door to the record.
    claim_key: str


def _with_claim(entry: OverlayEntry, key: str, row: Any) -> OverlayEntry:
    """``entry`` tagged with the claim ``key`` built from the row's id (untagged without an id)."""
    ref = _row_id(row)
    if ref is not None:
        entry["claim_key"] = f"{key}:{ref}"
    return entry


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


def _row_id(row: Any, column: str = "id") -> str | None:
    try:
        value = row[column]
    except (KeyError, IndexError):
        return None
    return str(value) if value is not None else None


def owner_zone_key(zone: tzinfo) -> str | None:
    """The IANA name of a resolved owner zone, or None for the unsaved-zone fallback.

    ``briefing_day`` returns the owner's saved ``ZoneInfo``; when no zone was ever saved it returns
    a fixed-offset fallback with no IANA name. Claims name their zone, so they exist only for a
    resolved one: a fallback is a display convention, not a zone the owner lives in.
    """
    key = getattr(zone, "key", None)
    return key if isinstance(key, str) else None


def _zone_name(zone: tzinfo) -> str:
    """The IANA name a claim carries for ``zone`` (the caller has checked it is resolved)."""
    key = owner_zone_key(zone)
    if key is None:
        raise ValueError("claims need a resolved owner zone; none is saved")
    return key


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


async def _read_source(
    pool: asyncpg.Pool,
    name: str,
    sql: str,
    *args: Any,
    butler: str,
    missing_ok: bool = False,
) -> tuple[list[Any], bool]:
    """Read one domain source; a failure is reported (``ok=False``) rather than raised.

    The caller treats any failed source as a partial run: previous entries and claims stand.
    ``missing_ok`` reads a source whose table may legitimately not exist (the butler does not run
    that module) as empty and complete, rather than as a failure.
    """
    try:
        return list(await pool.fetch(sql, *args)), True
    except asyncpg.UndefinedTableError:
        if missing_ok:
            return [], True
        logger.warning("%s overlay: %s table missing; run is partial", butler, name)
        return [], False
    except Exception:
        logger.warning("%s overlay: %s read failed; run is partial", butler, name, exc_info=True)
        return [], False


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


async def finish_contribution(
    pool: asyncpg.Pool,
    *,
    butler: str,
    today: date_cls,
    lookahead_days: int,
    entries_by_date: dict[str, list[OverlayEntry]],
    claims_by_date: dict[str, list[dict[str, Any]]],
    complete: bool,
    window_start: datetime,
    zone: tzinfo,
) -> tuple[int, int, dict[str, Any], bool]:
    """Write a producer's envelopes and reconcile its claims, honouring a partial run.

    A complete run rewrites every envelope in the window with its entries and claims. A partial run
    (a source could not be read) rewrites nothing: it flags the existing envelopes incomplete and
    reconciles with ``complete=False``, so nothing is retracted. A reconcile that raises also makes
    the run partial. Returns ``(dates_written, total_entries, claim_result, complete)``.
    """
    if complete:
        dates_written, total_entries = await write_overlay_envelopes(
            pool,
            butler=butler,
            today=today,
            lookahead_days=lookahead_days,
            entries_by_date=entries_by_date,
            claims_by_date=claims_by_date,
        )
    else:
        dates_written = await mark_overlay_envelopes_incomplete(
            pool, today=today, lookahead_days=lookahead_days
        )
        total_entries = 0
    claim_result, complete = await settle_claims(
        pool,
        butler=butler,
        claims=[c for day in claims_by_date.values() for c in day],
        complete=complete,
        window_start=window_start,
        today=today,
        lookahead_days=lookahead_days,
        zone=zone,
    )
    return dates_written, total_entries, claim_result, complete


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
    zone: tzinfo,
) -> tuple[dict[str, Any], bool]:
    """Reconcile, and let a failed reconcile make the run partial.

    With no owner zone saved there is no zone to name a claim in: no claim was built, nothing is
    reconciled or retracted, and the previous calendar rows stand (``skipped`` says so).

    Returns ``(claim_result, complete)``. When the reconcile raised, the window's envelopes are
    flagged incomplete (their entries and ``generated_at`` stand) and ``complete`` is False, so the
    overlay reads as partial instead of silently current while no claim reached the calendar.
    """
    if owner_zone_key(zone) is None:
        return {"skipped": "owner_zone_unsaved"}, complete
    claim_result = await reconcile_claims_in_process(
        pool, butler=butler, claims=claims, complete=complete, window_start=window_start
    )
    if complete and "error" not in claim_result:
        # An invalid claim, or a retraction that did not run (the projection tables are absent),
        # means the calendar was not fully reconciled: the run is partial.
        if claim_result.get("invalid"):
            claim_result = {**claim_result, "error": "invalid_claims"}
        elif not claim_result.get("retraction_ran"):
            # Nothing was wrong with the claims, but retraction could not run (for example the
            # projection tables are absent): the calendar is not fully reconciled.
            claim_result = {**claim_result, "error": "retraction_skipped"}
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
        entries_by_date.setdefault(date_str, []).append(_with_claim(entry, "finance:bill", row))

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
        entries_by_date.setdefault(date_str, []).append(
            _with_claim(entry, "finance:subscription", row)
        )

    claims_by_date = (
        finance_claims(
            bills_rows,
            sub_rows,
            today=today,
            zone=zone,
            now=_utcnow(),
            horizon=horizon,
            bill_days=bill_days,
        )
        if owner_zone_key(zone) is not None
        else {}
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
                _with_claim(
                    {
                        "kind": "subscription_cancel_by",
                        "label": f"Cancel {row['service']}",
                        "priority": "high",
                        "meta": {},
                    },
                    "finance:subscription_cancel_by",
                    row,
                )
            )

    dates_written, total_entries, claim_result, complete = await finish_contribution(
        pool,
        butler="finance",
        today=today,
        lookahead_days=FINANCE_OVERLAY_LOOKAHEAD_DAYS,
        entries_by_date=entries_by_date,
        claims_by_date=claims_by_date,
        complete=complete,
        window_start=day_start_utc(today, zone),
        zone=zone,
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


#: Hours before a flight's departure that its online check-in reminder fires.
CHECKIN_REMINDER_HOURS = 24
#: Days before a visa or insurance expiry that its reminder fires.
DOCUMENT_EXPIRY_REMINDER_DAYS = 30
_RESERVATION_MINUTES = {"restaurant": 90, "car_rental": 60, "activity": 120, "tour": 120}
_EXPIRING_DOCUMENT_TYPES = ("visa", "insurance")


def _timed_claim(
    *,
    key: str,
    version: str,
    kind: str,
    label: str,
    priority: str,
    presence: str,
    start: datetime,
    end: datetime | None,
    zone: tzinfo,
    ref: str,
    owner_action: bool = False,
    route: str = "/travel",
) -> dict[str, Any]:
    span: dict[str, Any] = {"start": start.isoformat(), "timezone": _zone_name(zone)}
    if end is not None:
        span["end"] = end.isoformat()
    return {
        "claim_key": key,
        "record_version": version,
        "kind": kind,
        "label": clip_label(label),
        "priority": priority,
        "presence": presence,
        "span": span,
        "owner_action": owner_action,
        "status": "active",
        "door": {"route": route, "ref": ref},
    }


def _day_claim(
    *,
    key: str,
    version: str,
    kind: str,
    label: str,
    priority: str,
    presence: str,
    day: date_cls,
    zone: tzinfo,
    ref: str,
    owner_action: bool = False,
    route: str = "/travel",
) -> dict[str, Any]:
    return {
        "claim_key": key,
        "record_version": version,
        "kind": kind,
        "label": clip_label(label),
        "priority": priority,
        "presence": presence,
        "span": {"date": day.isoformat(), "timezone": _zone_name(zone)},
        "owner_action": owner_action,
        "status": "active",
        "door": {"route": route, "ref": ref},
    }


def travel_claims(
    legs: list[Any],
    accommodations: list[Any],
    reservations: list[Any],
    documents: list[Any],
    *,
    today: date_cls,
    zone: tzinfo,
    now: datetime,
) -> dict[str, list[dict[str, Any]]]:
    """Claims for legs, flight check-in reminders, stays, reservations and document expiry. Pure.

    A leg blocks its departure-to-arrival time; a flight also claims an online check-in reminder
    ``CHECKIN_REMINDER_HOURS`` before departure (omitted once that moment has passed). A stay
    claims an all-day block for its check-in day and for its check-out day. A reservation with a
    time blocks it for a type-dependent length. A visa or insurance expiry claims a reminder
    ``DOCUMENT_EXPIRY_REMINDER_DAYS`` before it (an overlay-only claim once that has passed).
    Rows without an id carry no claim. Returns claims keyed by owner-local record date.
    """
    out: dict[str, list[dict[str, Any]]] = {}

    def add(day: date_cls, claim: dict[str, Any]) -> None:
        out.setdefault(day.isoformat(), []).append(claim)

    def local_day(ts: datetime) -> date_cls:
        return ts.astimezone(zone).date()

    for row in legs:
        ref = _row_id(row)
        dep, arr = row["departure_at"], row["arrival_at"]
        if ref is None or dep is None:
            continue
        origin = row["departure_city"] or "?"
        dest = row["arrival_city"] or "?"
        days_until = (local_day(dep) - today).days
        version = _version(dep.isoformat(), arr.isoformat() if arr else "", origin, dest)
        if arr is not None and arr > dep:
            claim = _timed_claim(
                key=f"travel:leg:{ref}",
                version=version,
                kind="leg",
                label=f"{origin} → {dest}",
                priority=_departure_priority(days_until),
                presence="block",
                start=dep,
                end=arr,
                zone=zone,
                ref=ref,
            )
        else:
            claim = _day_claim(
                key=f"travel:leg:{ref}",
                version=version,
                kind="leg",
                label=f"{origin} → {dest}",
                priority=_departure_priority(days_until),
                presence="overlay",
                day=local_day(dep),
                zone=zone,
                ref=ref,
            )
        add(local_day(dep), claim)
        remind_at = dep - timedelta(hours=CHECKIN_REMINDER_HOURS)
        if row["type"] == "flight" and remind_at > now:
            add(
                local_day(dep),
                _timed_claim(
                    key=f"travel:leg_checkin:{ref}",
                    version=_version(dep.isoformat()),
                    kind="check_in_reminder",
                    label=f"Check in: {origin} → {dest}",
                    priority="high",
                    presence="reminder",
                    start=remind_at,
                    end=None,
                    zone=zone,
                    ref=ref,
                    owner_action=True,
                ),
            )

    for row in accommodations:
        ref = _row_id(row)
        if ref is None:
            continue
        name = row["name"] or row["type"]
        for which, kind, label in (
            ("check_in", "check_in", f"Check-in: {name}"),
            ("check_out", "check_out", f"Check-out: {name}"),
        ):
            ts = row[which]
            if ts is None:
                continue
            day = local_day(ts)
            add(
                day,
                _day_claim(
                    key=f"travel:accommodation_{which}:{ref}",
                    version=_version(ts.isoformat(), name),
                    kind=kind,
                    label=label,
                    priority=_checkin_priority((day - today).days)
                    if which == "check_in"
                    else "low",
                    # An all-day marker: a hotel day must not read as busy all day.
                    presence="overlay",
                    day=day,
                    zone=zone,
                    ref=ref,
                ),
            )

    for row in reservations:
        ref = _row_id(row)
        at = row["datetime"]
        if ref is None or at is None:
            continue
        minutes = _RESERVATION_MINUTES.get(row["type"], 120)
        label = str(row["provider"] or row["type"]).strip() or str(row["type"])
        add(
            local_day(at),
            _timed_claim(
                key=f"travel:reservation:{ref}",
                version=_version(at.isoformat(), row["type"], label),
                kind="reservation",
                label=label,
                priority="medium",
                presence="block",
                start=at,
                end=at + timedelta(minutes=minutes),
                zone=zone,
                ref=ref,
            ),
        )

    for row in documents:
        ref = _row_id(row)
        expiry = row["expiry_date"]
        if ref is None or expiry is None or row["type"] not in _EXPIRING_DOCUMENT_TYPES:
            continue
        label = f"{str(row['type']).capitalize()} expires"
        remind_at = datetime.combine(
            expiry - timedelta(days=DOCUMENT_EXPIRY_REMINDER_DAYS),
            time(_REMINDER_HOUR),
            tzinfo=zone,
        )
        version = _version(expiry.isoformat(), row["type"])
        if remind_at > now:
            claim = _timed_claim(
                key=f"travel:document_expiry:{ref}",
                version=version,
                kind="document_expiry",
                label=label,
                priority="medium",
                presence="reminder",
                start=remind_at,
                end=None,
                zone=zone,
                ref=ref,
                owner_action=True,
            )
        else:
            claim = _day_claim(
                key=f"travel:document_expiry:{ref}",
                version=version,
                kind="document_expiry",
                label=label,
                priority="high",
                presence="overlay",
                day=expiry,
                zone=zone,
                ref=ref,
                owner_action=True,
            )
        add(expiry, claim)
    return out


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

    complete = True
    # --- Transport legs departing or arriving within the window ---
    leg_rows, ok = await _read_source(
        pool,
        "legs",
        """
        SELECT l.id, l.type, l.carrier, l.departure_city, l.arrival_city,
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
        butler="travel",
    )

    # --- Accommodations checking in or out within the window ---
    complete &= ok
    accom_rows, ok = await _read_source(
        pool,
        "accommodations",
        """
        SELECT a.id, a.type, a.name, a.address, a.check_in, a.check_out,
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
        butler="travel",
    )
    complete &= ok

    reservation_rows, ok = await _read_source(
        pool,
        "reservations",
        """
        SELECT r.id, r.type, r.provider, r.datetime
        FROM travel.reservations r
        JOIN travel.trips t ON t.id = r.trip_id
        WHERE t.status = ANY($3::text[])
          AND r.datetime >= $1 AND r.datetime < $2
        ORDER BY r.datetime ASC
        """,
        window_start,
        window_end,
        list(_TRAVEL_ACTIVE_STATUSES),
        butler="travel",
    )
    complete &= ok
    document_rows, ok = await _read_source(
        pool,
        "documents",
        """
        SELECT d.id, d.type, d.expiry_date
        FROM travel.documents d
        JOIN travel.trips t ON t.id = d.trip_id
        WHERE t.status = ANY($3::text[])
          AND d.type = ANY($4::text[])
          AND d.expiry_date >= $1 AND d.expiry_date <= $2
        ORDER BY d.expiry_date ASC
        """,
        today,
        today + timedelta(days=TRAVEL_OVERLAY_LOOKAHEAD_DAYS + DOCUMENT_EXPIRY_REMINDER_DAYS),
        list(_TRAVEL_ACTIVE_STATUSES),
        list(_EXPIRING_DOCUMENT_TYPES),
        butler="travel",
    )
    complete &= ok

    entries_by_date: dict[str, list[OverlayEntry]] = {}
    departure_count = arrival_count = check_in_count = check_out_count = 0

    def _add(date_str: str, entry: OverlayEntry, *, leg: Any = None) -> None:
        if leg is not None:
            _with_claim(entry, "travel:leg", leg)
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
                leg=row,
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
                leg=row,
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

    claims_by_date = (
        travel_claims(
            leg_rows,
            accom_rows,
            reservation_rows,
            document_rows,
            today=today,
            zone=zone,
            now=_utcnow(),
        )
        if owner_zone_key(zone) is not None
        else {}
    )
    dates_written, total_entries, claim_result, complete = await finish_contribution(
        pool,
        butler="travel",
        today=today,
        lookahead_days=TRAVEL_OVERLAY_LOOKAHEAD_DAYS,
        entries_by_date=entries_by_date,
        claims_by_date=claims_by_date,
        complete=complete,
        window_start=window_start,
        zone=zone,
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
        "complete": complete,
        "claims": claim_result,
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


def _add_relationship_claim(
    claims_by_date: dict[str, list[dict[str, Any]]],
    date_str: str,
    row: Any,
    *,
    column: str,
    key_kind: str,
    version: str,
    kind: str,
    label: str,
    priority: str,
    landing: date_cls,
    zone: tzinfo,
    owner_action: bool = False,
) -> None:
    """Add an overlay-presence claim for a relationship record (none when the row has no id).

    Relationship records already reach the owner through their own flows, so a claim owns no
    calendar row: it gives the record a stable key, so the workspace shows it once and can offer a
    door to it. The person-posture filter has already decided the row is shown.
    """
    ref = _row_id(row, column)
    if ref is None or owner_zone_key(zone) is None:
        return
    claims_by_date.setdefault(date_str, []).append(
        _day_claim(
            key=f"relationship:{key_kind}:{ref}",
            version=version,
            kind=kind,
            label=label,
            priority=priority,
            presence="overlay",
            day=landing,
            zone=zone,
            ref=ref,
            route="/relationship",
            owner_action=owner_action,
        )
    )


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
    complete = True
    date_rows, ok = await _read_source(
        pool,
        "important dates",
        """
        SELECT id.id AS date_id, COALESCE(e.canonical_name, 'Unknown') AS name, id.label,
               id.month, id.day, id.year, e.posture
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

        SELECT id.id AS date_id, COALESCE(e.canonical_name, 'Unknown') AS name,
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
        butler="relationship",
    )
    complete &= ok

    # --- Follow-up reminders due within the window (and overdue) ---
    # Reminders are SPO facts (predicate='reminder'); the contact UUID is embedded
    # in the subject key as "contact:{uuid}:reminder:{...}" and resolved to a name
    # via contact_entity_map → entities. window_end is the exclusive owner-local midnight
    # after the last lookahead date, converted to UTC for the TIMESTAMPTZ compare.
    local_midnight = datetime(today.year, today.month, today.day, tzinfo=zone)
    window_end = (local_midnight + timedelta(days=lookahead + 1)).astimezone(UTC)

    reminder_rows, ok = await _read_source(
        pool,
        "follow-ups",
        """
        SELECT f.id AS fact_id, COALESCE(e.canonical_name, 'Unknown') AS name,
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
        butler="relationship",
    )
    complete &= ok

    entries_by_date: dict[str, list[OverlayEntry]] = {}
    claims_by_date: dict[str, list[dict[str, Any]]] = {}
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
            _add_relationship_claim(
                claims_by_date,
                date_str,
                row,
                column="date_id",
                key_kind="important_date",
                version=_version(date_str, raw_label, posture),
                kind="remembrance",
                label=f"{row['name']}'s birthday (remembrance)",
                priority="low",
                landing=landing,
                zone=zone,
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
        _add_relationship_claim(
            claims_by_date,
            date_str,
            row,
            column="date_id",
            key_kind="important_date",
            version=_version(date_str, raw_label, posture),
            kind=kind,
            label=str(row["name"]),
            priority=entry["priority"],
            landing=landing,
            zone=zone,
        )
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
        _add_relationship_claim(
            claims_by_date,
            date_str,
            row,
            column="fact_id",
            key_kind="follow_up",
            version=_version(trigger.isoformat(), row["label"], is_overdue),
            kind="follow_up",
            label=str(row["label"] or "follow-up"),
            priority=entry["priority"],
            landing=landing,
            zone=zone,
            owner_action=True,
        )
        follow_up_count += 1

    dates_written, total_entries, claim_result, complete = await finish_contribution(
        pool,
        butler="relationship",
        today=today,
        lookahead_days=lookahead,
        entries_by_date=entries_by_date,
        claims_by_date=claims_by_date,
        complete=complete,
        window_start=day_start_utc(today, zone),
        zone=zone,
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
        "complete": complete,
        "claims": claim_result,
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

    Reads the appointments Health booked from its calendar projection (the source the visit
    brief uses; the ``appointment`` facts nothing writes are no longer read, bu-rx0tyg) and the
    health schema's own ``facts`` table for medications (the legacy relational
    ``health.medications`` table is orphaned), and writes per-date overlay
    envelopes under ``calendar/overlay/<date>``:

    - ``appointment`` entries for those appointments starting within the lookahead window,
      bucketed onto the owner-zone calendar date they land on, each with an overlay-presence
      ``health:visit:<event id>`` claim.
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

    complete = True
    # --- Appointments Health booked: its calendar projection is the source (bu-rx0tyg) ---
    # The same structural identification the visit brief uses (Health's applied
    # ``workspace_user_create`` row), not the ``appointment`` facts nothing writes.
    from butlers.jobs.health_visit_brief import APPOINTMENTS_SQL

    appointment_rows, ok = await _read_source(
        pool,
        "appointments",
        APPOINTMENTS_SQL
        + """
        SELECT event_id, title, starts_at, ends_at FROM appts
        WHERE starts_at >= $1 AND starts_at < $2
        ORDER BY starts_at, event_id
        """,
        window_start,
        window_end,
        butler="health",
        missing_ok=True,  # a Health without the calendar module has no appointments to read
    )
    complete &= ok

    # --- Active medications: property facts that recur daily ---
    medication_rows, ok = await _read_source(
        pool,
        "medications",
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
        butler="health",
    )
    complete &= ok

    entries_by_date: dict[str, list[OverlayEntry]] = {}

    def _add(date_str: str, entry: OverlayEntry) -> None:
        entries_by_date.setdefault(date_str, []).append(entry)

    appointment_count = 0
    claims_by_date: dict[str, list[dict[str, Any]]] = {}
    for row in appointment_rows:
        starts_at: datetime = row["starts_at"]
        ends_at: datetime = row["ends_at"]
        if not (window_start <= starts_at < window_end):
            continue
        date_str = _local_date_str(starts_at, zone)
        days_until = (date_cls.fromisoformat(date_str) - today).days
        title = str(row["title"] or "Appointment")
        priority = _appointment_priority(days_until)
        event_id = str(row["event_id"])
        _add(
            date_str,
            {
                "kind": "appointment",
                "label": title,
                "priority": priority,
                "meta": {"appointment_id": event_id, "starts_at": starts_at.isoformat()},
                "claim_key": f"health:visit:{event_id}",
            },
        )
        # The visit already is a calendar event: its claim owns no row of its own (overlay
        # presence), so the workspace can show it once and offer a door to the record.
        if owner_zone_key(zone) is not None:
            claims_by_date.setdefault(date_str, []).append(
                _timed_claim(
                    key=f"health:visit:{event_id}",
                    version=_version(starts_at.isoformat(), ends_at.isoformat(), title),
                    kind="appointment",
                    label=title,
                    priority=priority,
                    presence="overlay",
                    start=starts_at,
                    end=ends_at,
                    zone=zone,
                    ref=event_id,
                    route="/health",
                )
            )
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

    dates_written, total_entries, claim_result, complete = await finish_contribution(
        pool,
        butler="health",
        today=today,
        lookahead_days=HEALTH_OVERLAY_LOOKAHEAD_DAYS,
        entries_by_date=entries_by_date,
        claims_by_date=claims_by_date,
        complete=complete,
        window_start=day_start_utc(today, zone),
        zone=zone,
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
        "complete": complete,
        "claims": claim_result,
    }


# ---------------------------------------------------------------------------
# Education job (bu-5ii4gj.13 S5b)
# ---------------------------------------------------------------------------

EDUCATION_OVERLAY_LOOKAHEAD_DAYS = 7


def _reviews_label(count: int) -> str:
    return f"{count} {'review' if count == 1 else 'reviews'} due"


def _reviews_priority(day: date_cls, today: date_cls) -> str:
    return "medium" if day <= today else "low"


def education_claims(
    due_by_date: dict[date_cls, int], *, today: date_cls, zone: tzinfo
) -> dict[str, list[dict[str, Any]]]:
    """One aggregated "N reviews due" claim per owner-local day. Pure.

    Reviews are the learner's own queue and arrive in their own flow, so the claim is an overlay
    presence: it gives the day a stable key (``education:reviews:<date>``) and a door, and owns no
    calendar row. Reviews already overdue count toward today.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for day, count in sorted(due_by_date.items()):
        if count <= 0:
            continue
        out.setdefault(day.isoformat(), []).append(
            _day_claim(
                key=f"education:reviews:{day.isoformat()}",
                version=_version(day, count),
                kind="reviews_due",
                label=_reviews_label(count),
                priority=_reviews_priority(day, today),
                presence="overlay",
                day=day,
                zone=zone,
                ref=day.isoformat(),
                route="/education",
            )
        )
    return out


async def run_education_calendar_overlay_contribution(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None,
) -> dict[str, Any]:
    """Education butler calendar overlay contribution job (deterministic, zero LLM).

    Counts the spaced-repetition reviews falling due on each owner-local day of the lookahead
    window (overdue reviews count toward today) across active mind maps, and writes a
    ``reviews_due`` entry and an overlay-presence claim per day that has any. A failed read makes
    the run partial: the envelopes are flagged incomplete and nothing is rewritten.
    """
    del job_args

    today, zone = await briefing_day(pool)
    today_str = today.isoformat()
    local_midnight = datetime(today.year, today.month, today.day, tzinfo=zone)
    window_end = (local_midnight + timedelta(days=EDUCATION_OVERLAY_LOOKAHEAD_DAYS + 1)).astimezone(
        UTC
    )

    rows, complete = await _read_source(
        pool,
        "reviews",
        """
        SELECT n.next_review_at
        FROM education.mind_map_nodes n
        JOIN education.mind_maps mm ON mm.id = n.mind_map_id
        WHERE n.next_review_at < $1
          AND mm.status = 'active'
          AND n.mastery_status NOT IN ('mastered', 'unseen')
        """,
        window_end,
        butler="education",
    )

    due_by_date: dict[date_cls, int] = {}
    for row in rows:
        day = max(date_cls.fromisoformat(_local_date_str(row["next_review_at"], zone)), today)
        due_by_date[day] = due_by_date.get(day, 0) + 1

    claims_by_date = (
        education_claims(due_by_date, today=today, zone=zone)
        if owner_zone_key(zone) is not None
        else {}
    )
    entries_by_date: dict[str, list[OverlayEntry]] = {
        day.isoformat(): [
            {
                "kind": "reviews_due",
                "label": _reviews_label(count),
                "priority": _reviews_priority(day, today),
                "meta": {"count": count},
            }
        ]
        for day, count in sorted(due_by_date.items())
        if count > 0
    }

    dates_written, total_entries, claim_result, complete = await finish_contribution(
        pool,
        butler="education",
        today=today,
        lookahead_days=EDUCATION_OVERLAY_LOOKAHEAD_DAYS,
        entries_by_date=entries_by_date,
        claims_by_date=claims_by_date,
        complete=complete,
        window_start=day_start_utc(today, zone),
        zone=zone,
    )
    pruned = await prune_old_overlay_contributions(pool, today=today_str)

    logger.info(
        "Education calendar overlay contribution: date=%s dates_written=%d reviews=%d pruned=%d",
        today_str,
        dates_written,
        len(rows),
        pruned,
    )
    return {
        "butler": "education",
        "date": today_str,
        "dates_written": dates_written,
        "review_entries": len(entries_by_date),
        "total_entries": total_entries,
        "pruned": pruned,
        "complete": complete,
        "claims": claim_result,
    }
