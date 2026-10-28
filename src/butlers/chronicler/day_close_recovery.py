"""Finite, content-blind planning and exact-provenance day-close cache recovery.

Only Chronicler-owned session/deferred records and their existing notify ledger
links are consumed. No time-based message matching, transport replay, coverage
witness, model invocation or operational scheduling is performed here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

from butlers.chronicler.day_close_cache import (
    day_close_cache_key,
    lock_day_close_cache_tuple,
    resolve_day_close_timezone,
)
from butlers.chronicler.day_close_writer import (
    DAY_CLOSE_TASK_NAME,
    _extract_provenance_refs,
    _validated_canonical_bundle_capture,
    select_day_close_narration,
)
from butlers.chronicler.prose_admission import classify_day_close_candidate
from butlers.chronicler.storage import upsert_tier2_cache

MAX_DAYS = 31


@dataclass(frozen=True)
class RecoveryEntry:
    """Safe dry-run record: no message, payload, recipient or raw exception."""

    day: date
    reason: str
    fingerprint: str | None = None


@dataclass(frozen=True)
class RecoveryPlan:
    timezone: str
    entries: tuple[RecoveryEntry, ...]

    def safe_dict(self) -> dict[str, Any]:
        return {
            "timezone": self.timezone,
            "entries": [{**asdict(e), "day": e.day.isoformat()} for e in self.entries],
        }


@dataclass(frozen=True)
class _Candidate:
    prose: str
    refs: tuple[str, ...]
    fingerprint: str


class RecoveryRefused(ValueError):
    """A closed reason, never an underlying database or payload exception."""


def _range(start: date, end: date, timezone: str, now: datetime) -> tuple[date, ...]:
    _, zone = resolve_day_close_timezone(timezone)
    if type(start) is not date or type(end) is not date or now.tzinfo is None:
        raise RecoveryRefused("invalid_range")
    count = (end - start).days + 1
    if not 1 <= count <= MAX_DAYS or end >= now.astimezone(zone).date():
        raise RecoveryRefused("invalid_range")
    return tuple(start + timedelta(days=i) for i in range(count))


def _object(value: Any) -> Any:
    # DB JSONB may be decoded by the owning pool or returned as JSON text by
    # asyncpg's default codec. Neither representation supplies extra authority.
    return json.loads(value) if isinstance(value, str) else value


def _fingerprint(value: Any) -> str:
    def encode(item):
        if isinstance(item, (UUID, datetime)):
            return str(item)
        raise TypeError()

    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=encode,
        ).encode()
    ).hexdigest()


async def _candidate(conn: Any, day: date, timezone: str, *, lock: bool = False):
    suffix = " FOR SHARE" if lock else ""
    sessions = await conn.fetch(
        """SELECT id, success, trigger_source, completed_at, tool_calls
             FROM sessions
            WHERE success IS TRUE AND trigger_source = $1
              AND EXISTS (
                  -- CASE, not an AND guard: SQL does not order predicate evaluation,
                  -- and legacy rows hold a double-encoded string here.
                  SELECT 1 FROM jsonb_array_elements(
                      CASE WHEN jsonb_typeof(tool_calls) = 'array'
                           THEN tool_calls ELSE '[]'::jsonb END) AS b
                   WHERE b->>'name' = 'chronicler_day_close_bundle'
                     AND b->'input'->>'date_label' = $2
                     AND b->'input'->>'timezone' = $3)
            ORDER BY id LIMIT 2"""
        + suffix,
        f"schedule:{DAY_CLOSE_TASK_NAME}",
        day.isoformat(),
        timezone,
    )
    if len(sessions) != 1:
        return None, "missing_source" if not sessions else "ambiguous_session"
    session = dict(sessions[0])
    try:
        session_id = str(UUID(str(session["id"])))
        calls = _object(session["tool_calls"])
        if not isinstance(calls, list) or session["completed_at"] is None:
            return None, "incomplete_session"
        selected = select_day_close_narration(
            calls,
            output=None,
            session_id=session_id,
            target_date=day.isoformat(),
            timezone=timezone,
        )
        if (
            selected.invalid
            or not selected.prose
            or not selected.notification_id
            or not selected.deferred
        ):
            return None, "unproven_notification"
        bundle = _validated_canonical_bundle_capture(
            calls, target_date_iso=day.isoformat(), target_timezone=timezone
        )
        if bundle is None:
            return None, "unproven_notification"
        notification_id = UUID(selected.notification_id)
        deferred = await conn.fetchrow(
            "SELECT id, butler_name, channel, message, envelope, status"
            " FROM deferred_notifications WHERE id = $1" + suffix,
            notification_id,
        )
        ledger = await conn.fetch(
            "SELECT id, origin_butler, source, outcome, channel, intent, notification_ref, metadata"
            " FROM public.attention_ledger WHERE origin_butler = 'chronicler'"
            " AND source = 'notify' AND notification_ref = $1"
            " AND metadata->>'session_id' = $2 ORDER BY id LIMIT 2" + suffix,
            str(notification_id),
            session_id,
        )
        if deferred is None or len(ledger) != 1:
            return None, "missing_or_ambiguous_link"
        deferred, attention = dict(deferred), dict(ledger[0])
        envelope = _object(deferred["envelope"])
        metadata = _object(attention["metadata"])
        # The selected channel comes from fingerprint-bound request values,
        # not untrusted input inherited beside an executed record by merge.
        channel = selected.channel
        delivery = envelope.get("delivery") if isinstance(envelope, dict) else None
        if (
            deferred["butler_name"] != "chronicler"
            or deferred["status"] not in {"pending", "delivered"}
            or deferred["channel"] != channel
            or deferred["message"] != selected.prose
            or not isinstance(delivery, dict)
            or envelope.get("schema_version") != "notify.v1"
            or envelope.get("origin_butler") != "chronicler"
            or envelope.get("request_context") is not None
            or delivery.get("message") != selected.prose
            or delivery.get("channel") != channel
            or delivery.get("intent") != "send"
            or attention["origin_butler"] != "chronicler"
            or attention["source"] != "notify"
            or attention["outcome"] != "deferred"
            or attention["intent"] != "send"
            or attention["channel"] != channel
            or attention["notification_ref"] != str(notification_id)
            or not isinstance(metadata, dict)
            or metadata.get("session_id") != session_id
        ):
            return None, "source_mismatch"

        # Include every authority-bearing selected source value. Date/zone are
        # inputs, not derived from a timestamp. Completion time is provenance
        # only, never a matching criterion.
        fingerprint = _fingerprint(
            {
                "day": day.isoformat(),
                "timezone": timezone,
                "session": session,
                "deferred": deferred,
                "attention": attention,
            }
        )
        return _Candidate(
            # Parser-only/other-target results are not this cache's sources.
            # Keep the legacy writer extractor unchanged, but give recovery
            # only the executed bundle already admitted for this exact tuple.
            selected.prose.strip(),
            tuple(_extract_provenance_refs([bundle])),
            fingerprint,
        ), "eligible"
    except (ValueError, TypeError, KeyError, StopIteration):
        return None, "malformed_source"


async def plan_recovery(
    pool: asyncpg.Pool,
    *,
    start: date,
    end: date,
    timezone: str,
    now: datetime | None = None,
) -> RecoveryPlan:
    """Read at most31 explicit settled tuples; return only safe decisions/digests.

    Database failures propagate to the content-blind operator boundary. A
    refusal is not recovery success and must remain in the per-day accounting.
    """
    days = _range(start, end, timezone, now or datetime.now(UTC))
    entries = []
    async with pool.acquire() as conn:
        for day in days:
            candidate, reason = await _candidate(conn, day, timezone)
            entries.append(RecoveryEntry(day, reason, candidate.fingerprint if candidate else None))
    return RecoveryPlan(timezone, tuple(entries))


async def apply_recovery(pool: asyncpg.Pool, plan: RecoveryPlan) -> tuple[RecoveryEntry, ...]:
    """Revalidate an approved dry-run plan in one serializable transaction.

    All tuple locks are acquired in sorted order. Source rows are read-locked,
    every fingerprint is rechecked, then writes share the transaction. Drift,
    cancellation or database failure rolls back this invocation's replacements.
    Already-valid rows are preserved, including a newer normal refresh; repeated
    application never stamps a new build time onto an existing valid result.
    No coverage witness or delivery/transport state is created or updated.
    """
    if not plan.entries:
        raise RecoveryRefused("invalid_plan")
    days = _range(plan.entries[0].day, plan.entries[-1].day, plan.timezone, datetime.now(UTC))
    if tuple(e.day for e in plan.entries) != days:
        raise RecoveryRefused("invalid_plan")
    outcomes = []
    async with pool.acquire() as conn:
        async with conn.transaction(isolation="serializable"):
            for entry in plan.entries:
                if entry.reason != "eligible":
                    outcomes.append(entry)
                    continue
                if not isinstance(entry.fingerprint, str) or len(entry.fingerprint) != 64:
                    raise RecoveryRefused("invalid_plan")
                await lock_day_close_cache_tuple(conn, entry.day, plan.timezone)
                candidate, reason = await _candidate(conn, entry.day, plan.timezone, lock=True)
                if candidate is None or candidate.fingerprint != entry.fingerprint:
                    raise RecoveryRefused("source_drift")
                key = day_close_cache_key(entry.day, plan.timezone)
                _, zone = resolve_day_close_timezone(plan.timezone)
                start = datetime.combine(entry.day, datetime.min.time(), zone).astimezone(UTC)
                end = datetime.combine(
                    entry.day + timedelta(days=1), datetime.min.time(), zone
                ).astimezone(UTC)
                existing = await conn.fetchrow(
                    "SELECT prose, date_label, invalid_reason, provenance_refs, start_at, end_at,"
                    " cache_built_at"
                    " FROM tier2_cache WHERE cache_key = $1 AND superseded_at IS NULL"
                    " FOR UPDATE",
                    key,
                )
                marker = "chronicler.day_close_recovery:" + candidate.fingerprint
                if (
                    existing is not None
                    and existing.get("invalid_reason") is None
                    and existing.get("start_at") == start
                    and existing.get("end_at") == end
                    and (
                        classify_day_close_candidate(
                            existing.get("prose"),
                            date_label=existing.get("date_label"),
                            expected_date_iso=entry.day.isoformat(),
                        )
                        is None
                    )
                ):
                    refs = _object(existing.get("provenance_refs") or [])
                    outcomes.append(
                        RecoveryEntry(
                            entry.day,
                            "already_applied" if marker in refs else "preserved_valid",
                            entry.fingerprint,
                        )
                    )
                    continue
                refs = [*candidate.refs, marker]
                if existing is not None:
                    refs.append(
                        "chronicler.day_close_recovery_previous:" + _fingerprint(dict(existing))
                    )
                await upsert_tier2_cache(
                    conn,
                    cache_key=key,
                    start_at=start,
                    end_at=end,
                    prose=candidate.prose,
                    provenance_refs=refs,
                    date_label=entry.day.isoformat(),
                    invalid_reason=None,
                )
                outcomes.append(RecoveryEntry(entry.day, "applied", entry.fingerprint))
    return tuple(outcomes)
