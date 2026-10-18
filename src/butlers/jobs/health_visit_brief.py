"""Health ``health_visit_brief``: a pre-appointment brief for the owner (bu-jv1moa.15).

A deterministic job (no LLM, no cross-schema read besides ``public.facts`` / ``public.state``).
Once a day it looks for appointments on Health's own calendar that start within the next
two days and have not been briefed. For each one it composes a plain-text brief for the
period since the previous visit (90 days when there is none, and the brief says so) and
sends it to the owner only.

Content, every figure traced to its rows in the stored digest:

* symptoms by name, severity and frequency;
* adherence per active medication, with missed doses (the same missed-dose definition as
  the daily briefing: :func:`butlers.tools.health._medication_utils.dose_status_today`);
* weight, blood pressure and resting heart rate against the owner's personal baseline
  (:mod:`butlers.core.baselines`), with an honest state when history is thin;
* conditions whose record changed in the period.

A feed with no reading in the period renders "no data since <date>", never zero. The brief
is not a diagnosis: it states what was recorded, says so, and ends with the
"companion, not a doctor" line. Nothing is sent to the clinic or anyone but the owner; the
notify recipient is the owner's Telegram chat resolved from the entity store, and the job
emits no communicate effect.

Idempotence: ``health.visit_briefs`` is keyed by the calendar event id. A delivered row is
never re-sent; a rescheduled event re-anchors the row to its new start and, if it was
already delivered, sends nothing. A suppressed or failed delivery leaves the row undelivered
and is retried on the next run while the appointment is still inside the lead window.
"""

from __future__ import annotations

import asyncio
import logging
import statistics
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg

from butlers.core.attention_ledger import check_owner_notify_suppression, record_attention_event
from butlers.core.baselines import BaselineMetric, BaselineState, compute_baselines, score
from butlers.core.state import decode_jsonb
from butlers.core.tool_call_capture import get_current_switchboard_client
from butlers.credential_store import resolve_owner_telegram_recipient

logger = logging.getLogger(__name__)

#: An appointment is briefed once it starts within this long from now.
LEAD = timedelta(days=2)
#: Period covered when the owner has no earlier visit on Health's calendar.
DEFAULT_LOOKBACK_DAYS = 90
#: Settings key the fleet timezone lives under (``public.state``).
_GENERAL_SETTINGS_KEY = "settings.general"
#: Documented fallback owner zone (matches the daily briefing): SGT.
_SGT = timezone(timedelta(hours=8))

_ORIGIN = "health"
_NOTIFY_TIMEOUT_S = 30
_MAX_TITLE_CHARS = 80

DISCLAIMER = (
    "This is a record of what you logged, not medical advice: a companion, not a doctor. "
    "It was sent only to you."
)

#: Outcome strings a notifier returns.
DELIVERED = "delivered"
SUPPRESSED = "suppressed"
FAILED = "failed"

NotifyFn = Callable[[str], Awaitable[str]]


def _metric(key: str, unit: str, min_dispersion: float) -> BaselineMetric:
    # Owner-logged readings are irregular, so the band needs fewer days than a wearable's.
    return BaselineMetric(
        key=key,
        unit=unit,
        bucket="day",
        window_days=60,
        min_n=5,
        min_coverage=0.05,
        k_mad=2.6,
        k_consecutive=1,
        concern="both",
        min_dispersion=min_dispersion,
        expected_signal_key=f"health:visit-brief:{key}",
    )


@dataclass(frozen=True)
class _Series:
    label: str
    predicate: str
    metric: BaselineMetric
    #: Key inside a compound value (blood pressure); ``None`` for a scalar value.
    component: str | None = None
    decimals: int = 1


_SERIES: tuple[_Series, ...] = (
    _Series("Weight", "measurement_weight", _metric("weight", "kg", 0.5)),
    _Series(
        "Blood pressure (systolic)",
        "measurement_blood_pressure",
        _metric("bp_systolic", "mmHg", 3.0),
        "systolic",
        0,
    ),
    _Series(
        "Blood pressure (diastolic)",
        "measurement_blood_pressure",
        _metric("bp_diastolic", "mmHg", 2.0),
        "diastolic",
        0,
    ),
    _Series(
        "Resting heart rate", "measurement_resting_hr", _metric("resting_hr", "bpm", 1.0), None, 0
    ),
)

# Appointments on Health's calendar: provider (user-lane) events that are timed, not
# cancelled, not butler-generated and not declined by the owner. A recurring series
# contributes its instances. ``event_id`` is the stable key the brief is recorded under.
_APPOINTMENTS_SQL = """
    WITH appts AS (
        SELECT ce.id::text AS event_id, ce.title AS title,
               ce.starts_at AS starts_at, ce.ends_at AS ends_at
        FROM calendar_events ce
        JOIN calendar_sources cs ON cs.id = ce.source_id
        WHERE ce.recurrence_rule IS NULL
          AND ce.status <> 'cancelled' AND ce.all_day = false AND cs.lane = 'user'
          AND COALESCE(ce.metadata->>'butler_generated', 'false') <> 'true'
          AND NOT EXISTS (
              SELECT 1 FROM jsonb_array_elements(
                  CASE WHEN jsonb_typeof(ce.metadata->'attendees') = 'array'
                       THEN ce.metadata->'attendees' ELSE '[]'::jsonb END) a
              WHERE a->>'self' = 'true'
                AND lower(COALESCE(a->>'response_status', a->>'responseStatus', '')) = 'declined')
        UNION ALL
        SELECT ce.id::text || ':' || ci.origin_instance_ref, ce.title, ci.starts_at, ci.ends_at
        FROM calendar_event_instances ci
        JOIN calendar_events ce ON ce.id = ci.event_id
        JOIN calendar_sources cs ON cs.id = ce.source_id
        WHERE ce.recurrence_rule IS NOT NULL
          AND ce.status <> 'cancelled' AND ci.status <> 'cancelled'
          AND ce.all_day = false AND cs.lane = 'user'
          AND COALESCE(ce.metadata->>'butler_generated', 'false') <> 'true'
          AND NOT EXISTS (
              SELECT 1 FROM jsonb_array_elements(
                  CASE WHEN jsonb_typeof(ce.metadata->'attendees') = 'array'
                       THEN ce.metadata->'attendees' ELSE '[]'::jsonb END) a
              WHERE a->>'self' = 'true'
                AND lower(COALESCE(a->>'response_status', a->>'responseStatus', '')) = 'declined')
    )
"""

_UPCOMING_SQL = (
    _APPOINTMENTS_SQL
    + """
    SELECT event_id, title, starts_at, ends_at FROM appts
    WHERE starts_at > $1 AND starts_at <= $2
    ORDER BY starts_at, event_id
"""
)

_PRIOR_VISIT_SQL = (
    _APPOINTMENTS_SQL
    + """
    SELECT event_id, title, starts_at, ends_at FROM appts
    WHERE event_id <> $1 AND ends_at <= $2 AND ends_at <= $3
    ORDER BY ends_at DESC, event_id
    LIMIT 1
"""
)


@dataclass
class _Figures:
    """The digest: every figure in the brief with the fact ids it rests on."""

    figures: list[dict[str, Any]] = field(default_factory=list)

    def add(self, section: str, label: str, value: Any, fact_ids: list[str]) -> None:
        self.figures.append(
            {"section": section, "label": label, "value": value, "fact_ids": sorted(fact_ids)}
        )


async def owner_zone(pool: asyncpg.Pool) -> tzinfo:
    """The owner's saved timezone from ``public.state``, else SGT.

    ``public.state`` is read explicitly: the Health pool's ``search_path`` puts
    ``health.state`` first, so an unqualified ``state`` read would miss the shared
    ``settings.general`` row (bu-ko02so).
    """
    try:
        raw = await pool.fetchval(
            "SELECT value FROM public.state WHERE key = $1", _GENERAL_SETTINGS_KEY
        )
        stored = decode_jsonb(raw) if raw is not None else None
        name = stored.get("timezone") if isinstance(stored, dict) else None
        if isinstance(name, str) and name.strip():
            return ZoneInfo(name.strip())
    except Exception:
        logger.warning("Could not resolve owner timezone for visit brief; using SGT", exc_info=True)
    return _SGT


def _schedule_list(raw: Any) -> list[str]:
    value = decode_jsonb(raw) if isinstance(raw, str) else raw
    return [str(item) for item in value] if isinstance(value, list) else []


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _component(value: Any, component: str | None) -> float | None:
    value = decode_jsonb(value) if isinstance(value, str) else value
    if component is None:
        if isinstance(value, dict):
            value = value.get("value")
        return _number(value)
    return _number(value.get(component)) if isinstance(value, dict) else None


def _clean_title(title: Any) -> str:
    text = " ".join(str(title or "").split())
    return text[:_MAX_TITLE_CHARS] if text else "your appointment"


def _day(moment: datetime, zone: tzinfo) -> date:
    return moment.astimezone(zone).date()


def _n(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else plural or singular + 's'}"


def _fmt_day(day: date) -> str:
    return day.strftime("%b %-d")


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


async def _symptom_lines(
    pool: asyncpg.Pool, start: datetime, end: datetime, figures: _Figures
) -> list[str]:
    rows = await pool.fetch(
        """
        SELECT id::text AS id, content, metadata->>'severity' AS severity
        FROM facts
        WHERE predicate = 'symptom' AND scope = 'health' AND validity = 'active'
          AND valid_at >= $1 AND valid_at <= $2
        ORDER BY valid_at
        """,
        start,
        end,
    )
    if not rows:
        return ["Symptoms: none logged in this period."]
    by_name: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        by_name[str(row["content"]).strip().lower()].append(row)
    lines = [f"Symptoms ({_n(len(rows), 'entry', 'entries')}):"]
    for name, group in sorted(by_name.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        severities = [s for s in (r["severity"] for r in group) if s]
        numeric = [n for n in (_number(s) for s in severities) if n is not None]
        if numeric:
            detail = f"severity {min(numeric):g} to {max(numeric):g}, highest {max(numeric):g}"
        elif severities:
            counts: dict[str, int] = defaultdict(int)
            for s in severities:
                counts[s] += 1
            detail = ", ".join(f"{n} {s}" for s, n in sorted(counts.items()))
        else:
            detail = "no severity recorded"
        lines.append(f"- {name}: {_n(len(group), 'entry', 'entries')}, {detail}")
        figures.add(
            "symptoms",
            name,
            {"entries": len(group), "severities": severities},
            [r["id"] for r in group],
        )
    return lines


async def _adherence_lines(
    pool: asyncpg.Pool,
    start: datetime,
    end: datetime,
    zone: tzinfo,
    figures: _Figures,
) -> list[str]:
    from butlers.tools.health._medication_utils import dose_status_today, is_as_needed

    meds = await pool.fetch(
        """
        SELECT m.id::text AS id, m.subject AS subject,
               m.metadata->>'name' AS name, m.metadata->>'frequency' AS frequency,
               m.metadata->'schedule' AS schedule,
               (SELECT min(f.created_at) FROM facts f
                 WHERE f.scope = 'health' AND f.predicate = 'medication'
                   AND f.subject = m.subject) AS tracked_since
        FROM facts m
        WHERE m.predicate = 'medication' AND m.scope = 'health' AND m.validity = 'active'
          AND COALESCE((m.metadata->>'active')::boolean, false) = true
        ORDER BY m.metadata->>'name'
        """
    )
    if not meds:
        return ["Medications: no active medications recorded."]
    doses = await pool.fetch(
        """
        SELECT d.id::text AS id, d.valid_at AS taken_at, ml.subject AS subject
        FROM facts d
        JOIN facts ml ON ml.id::text = d.metadata->>'medication_id'
        WHERE d.predicate = 'took_dose' AND d.scope = 'health' AND d.validity = 'active'
          AND d.valid_at >= $1 AND d.valid_at <= $2
          AND COALESCE((d.metadata->>'skipped')::boolean, false) = false
        """,
        start,
        end,
    )
    taken: dict[tuple[str, date], list[str]] = defaultdict(list)
    for dose in doses:
        taken[(dose["subject"], _day(dose["taken_at"], zone))].append(dose["id"])

    now_local = end.astimezone(zone)
    lines = ["Medications:"]
    for med in meds:
        frequency = med["frequency"]
        if is_as_needed(frequency):
            ids = [i for (subj, _d), v in taken.items() if subj == med["subject"] for i in v]
            lines.append(f"- {med['name']}: as needed, {_n(len(ids), 'dose')} logged")
            figures.add("medications", med["name"], {"as_needed_doses": len(ids)}, ids)
            continue
        first_day = max(_day(start, zone), _day(med["tracked_since"], zone))
        schedule = _schedule_list(med["schedule"])
        scheduled = missed = took = 0
        ids: list[str] = []
        day = first_day
        while day <= now_local.date():
            at = (
                now_local
                if day == now_local.date()
                else datetime.combine(day, datetime.max.time().replace(microsecond=0), tzinfo=zone)
            )
            day_ids = taken.get((med["subject"], day), [])
            owed = dose_status_today(
                frequency=frequency, schedule=schedule, now_local=at, doses_taken=0
            ).missed
            late = dose_status_today(
                frequency=frequency, schedule=schedule, now_local=at, doses_taken=len(day_ids)
            ).missed
            scheduled += owed
            missed += late
            took += len(day_ids)
            ids.extend(day_ids)
            day += timedelta(days=1)
        if scheduled == 0 and took == 0:
            lines.append(f"- {med['name']}: no scheduled doses derivable from its frequency")
            figures.add("medications", med["name"], {"scheduled": 0, "taken": 0}, [])
            continue
        lines.append(
            f"- {med['name']}: {_n(took, 'dose')} logged against {scheduled} scheduled, "
            f"{missed} missed (tracked since {_fmt_day(first_day)})"
        )
        figures.add(
            "medications",
            med["name"],
            {"scheduled": scheduled, "taken": took, "missed": missed},
            ids,
        )
    return lines


async def _trend_lines(
    pool: asyncpg.Pool, start: datetime, end: datetime, zone: tzinfo, figures: _Figures
) -> list[str]:
    lines = ["Measurements against your own usual range:"]
    window = timedelta(days=60)
    for series in _SERIES:
        rows = await pool.fetch(
            """
            SELECT id::text AS id, valid_at, metadata->'value' AS value
            FROM facts
            WHERE predicate = $1 AND scope = 'health' AND validity = 'active'
              AND valid_at >= $2 AND valid_at <= $3
            ORDER BY valid_at
            """,
            series.predicate,
            start - window,
            end,
        )
        points = [(r["id"], r["valid_at"], _component(r["value"], series.component)) for r in rows]
        points = [(i, t, v) for i, t, v in points if v is not None]
        period = [(i, t, v) for i, t, v in points if t >= start]
        prior_days: dict[date, float] = {}
        for _i, moment, value in points:
            if moment < start:
                prior_days[_day(moment, zone)] = value
        unit = series.metric.unit
        if not period:
            last = await pool.fetchval(
                "SELECT max(valid_at) FROM facts WHERE predicate = $1 AND scope = 'health' "
                "AND validity = 'active' AND valid_at <= $2",
                series.predicate,
                end,
            )
            text = f"no data since {_fmt_day(_day(last, zone))}" if last else "no data recorded"
            lines.append(f"- {series.label}: {text}")
            figures.add("measurements", series.label, {"readings": 0, "note": text}, [])
            continue
        values = [v for _i, _t, v in period]
        latest_id, latest_at, latest = period[-1]
        baseline_text = "no baseline yet"
        baseline_info: dict[str, Any] = {"state": "insufficient_history"}
        baselines = compute_baselines(series.metric, prior_days, as_of=_day(start, zone), now=end)
        if baselines:
            baseline = baselines[0]
            deviation = score(series.metric, latest, baseline, now=end)
            if deviation.state is BaselineState.INSUFFICIENT_HISTORY:
                baseline_text = (
                    f"no baseline yet ({baseline.n_observed} days of readings before this period)"
                )
            elif baseline.center is not None and baseline.dispersion is not None:
                baseline_text = (
                    f"usual {baseline.center:.{series.decimals}f} {unit} "
                    f"(+/- {baseline.dispersion:.{series.decimals}f}), latest is "
                    f"{deviation.state.value} it"
                )
            baseline_info = {
                "state": deviation.state.value,
                "center": baseline.center,
                "dispersion": baseline.dispersion,
                "n_observed": baseline.n_observed,
                "n_expected": baseline.n_expected,
            }
        lines.append(
            f"- {series.label}: {_n(len(values), 'reading')}, latest "
            f"{latest:.{series.decimals}f} {unit} on {_fmt_day(_day(latest_at, zone))}, "
            f"average {statistics.fmean(values):.{series.decimals}f}; {baseline_text}"
        )
        figures.add(
            "measurements",
            series.label,
            {
                "readings": len(values),
                "latest": latest,
                "latest_id": latest_id,
                "mean": statistics.fmean(values),
                "baseline": baseline_info,
            },
            [i for i, _t, _v in period],
        )
    return lines


async def _condition_lines(
    pool: asyncpg.Pool, start: datetime, end: datetime, figures: _Figures
) -> list[str]:
    rows = await pool.fetch(
        """
        SELECT c.id::text AS id, c.metadata->>'name' AS name,
               c.metadata->>'status' AS status, p.metadata->>'status' AS previous
        FROM facts c
        LEFT JOIN facts p ON p.id = c.supersedes_id
        WHERE c.predicate = 'condition' AND c.scope = 'health' AND c.validity = 'active'
          AND c.created_at >= $1 AND c.created_at <= $2
        ORDER BY c.metadata->>'name'
        """,
        start,
        end,
    )
    if not rows:
        return ["Conditions: no changes recorded in this period."]
    lines = ["Conditions changed:"]
    for row in rows:
        change = (
            f"{row['previous']} to {row['status']}" if row["previous"] else f"new, {row['status']}"
        )
        lines.append(f"- {row['name']}: {change}")
        figures.add(
            "conditions",
            row["name"],
            {"status": row["status"], "previous": row["previous"]},
            [row["id"]],
        )
    return lines


async def compose_brief(
    pool: asyncpg.Pool,
    *,
    title: str,
    event_start: datetime,
    period_start: datetime,
    since_visit: datetime | None,
    now: datetime,
    zone: tzinfo,
) -> tuple[str, dict[str, Any]]:
    """Build the brief text and its digest for one appointment."""
    figures = _Figures()
    days = max((now - period_start).days, 1)
    if since_visit is not None:
        covers = (
            f"Covers the {days} days since your last visit on {_fmt_day(_day(since_visit, zone))}."
        )
    else:
        covers = (
            f"No earlier visit found on your Health calendar, so this covers the last "
            f"{DEFAULT_LOOKBACK_DAYS} days."
        )
    when = event_start.astimezone(zone).strftime("%a %b %-d, %H:%M")
    parts = [
        f"Health brief for {_clean_title(title)}, {when}",
        covers,
        "\n".join(await _symptom_lines(pool, period_start, now, figures)),
        "\n".join(await _adherence_lines(pool, period_start, now, zone, figures)),
        "\n".join(await _trend_lines(pool, period_start, now, zone, figures)),
        "\n".join(await _condition_lines(pool, period_start, now, figures)),
        DISCLAIMER,
    ]
    digest = {
        "period_start": period_start.isoformat(),
        "period_end": now.isoformat(),
        "default_lookback": since_visit is None,
        "figures": figures.figures,
    }
    return "\n\n".join(parts), digest


# ---------------------------------------------------------------------------
# Delivery (owner only)
# ---------------------------------------------------------------------------


async def send_owner_brief(pool: asyncpg.Pool, message: str) -> str:
    """Send ``message`` to the owner's Telegram chat; return the outcome string.

    The same owner-notify boundary the Home jobs use: quiet-hours / context-bus suppression
    applies, the recipient is resolved from the owner entity (never from event text), and
    every outcome is recorded in ``public.attention_ledger``. The message is a plain
    ``notify.v1`` send to that one recipient: there is no communicate effect.
    """

    async def _record(outcome: str, reason: str | None = None, ref: Any = None) -> None:
        await record_attention_event(
            pool,
            origin_butler=_ORIGIN,
            source="notify",
            outcome=outcome,
            channel="telegram",
            intent="send",
            priority="medium",
            reason=reason,
            notification_ref=ref,
        )

    reason = await check_owner_notify_suppression(pool, log_context="health_visit_brief")
    if reason is not None:
        await _record("suppressed", reason)
        return SUPPRESSED
    recipient = await resolve_owner_telegram_recipient(pool)
    if not recipient:
        await _record("failed", "no_recipient_configured")
        return FAILED
    client = get_current_switchboard_client()
    if client is None:
        await _record("failed", "switchboard_client_unavailable")
        return FAILED
    notify_request = {
        "schema_version": "notify.v1",
        "origin_butler": _ORIGIN,
        "delivery": {
            "intent": "send",
            "channel": "telegram",
            "message": message,
            "recipient": recipient,
        },
    }
    try:
        result = await asyncio.wait_for(
            client.call_tool(
                "deliver", {"source_butler": _ORIGIN, "notify_request": notify_request}
            ),
            timeout=_NOTIFY_TIMEOUT_S,
        )
    except Exception as exc:
        logger.warning("health_visit_brief: deliver() call failed: %s", type(exc).__name__)
        await _record("failed", f"delivery_error:{type(exc).__name__}")
        return FAILED
    data = result.data
    if result.is_error or (isinstance(data, dict) and data.get("status") == "failed"):
        await _record("failed", "delivery_error")
        return FAILED
    await _record("delivered", ref=data.get("notification_id") if isinstance(data, dict) else None)
    return DELIVERED


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------


async def run_visit_brief(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None = None,
    *,
    notify_fn: NotifyFn | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Brief the owner before each unbriefed appointment starting within the lead window."""
    del job_args
    now = now or datetime.now(UTC)
    send = notify_fn or (lambda message: send_owner_brief(pool, message))
    zone = await owner_zone(pool)
    result: dict[str, Any] = {"considered": 0, "sent": 0, "skipped": 0, "retry": 0}

    for appt in await pool.fetch(_UPCOMING_SQL, now, now + LEAD):
        result["considered"] += 1
        event_id: str = appt["event_id"]
        existing = await pool.fetchrow(
            "SELECT delivered_at FROM visit_briefs WHERE event_id = $1", event_id
        )
        if existing is not None and existing["delivered_at"] is not None:
            # Already briefed; a reschedule only re-anchors the record.
            await pool.execute(
                "UPDATE visit_briefs SET event_start = $2, updated_at = now() "
                "WHERE event_id = $1 AND event_start <> $2",
                event_id,
                appt["starts_at"],
            )
            result["skipped"] += 1
            continue
        prior = await pool.fetchrow(_PRIOR_VISIT_SQL, event_id, now, appt["starts_at"])
        since = prior["ends_at"] if prior is not None else None
        period_start = since if since is not None else now - timedelta(days=DEFAULT_LOOKBACK_DAYS)
        message, digest = await compose_brief(
            pool,
            title=appt["title"],
            event_start=appt["starts_at"],
            period_start=period_start,
            since_visit=since,
            now=now,
            zone=zone,
        )
        await pool.execute(
            """
            INSERT INTO visit_briefs (event_id, event_start, period_start, digest)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (event_id) DO UPDATE
                SET event_start = EXCLUDED.event_start, period_start = EXCLUDED.period_start,
                    digest = EXCLUDED.digest, updated_at = now()
            """,
            event_id,
            appt["starts_at"],
            period_start,
            digest,
        )
        outcome = await send(message)
        if outcome == DELIVERED:
            await pool.execute(
                "UPDATE visit_briefs SET delivered_at = now(), updated_at = now() "
                "WHERE event_id = $1",
                event_id,
            )
            result["sent"] += 1
        else:
            result["retry"] += 1
    return result
