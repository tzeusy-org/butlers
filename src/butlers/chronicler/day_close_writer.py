"""Chronicler day-close cache writer.

Post-execution hook that persists the prose output of the
``chronicler_day_close`` scheduled prompt to ``chronicler.tier2_cache``.

The hook is registered in the scheduler's ``completion_hooks`` dict for the
``chronicler_day_close`` task name.  It runs after the spawner returns
successfully. A matching canonical bundle capture records a quiet closed day
even when its output is empty; failed or unbound captures never establish
coverage. If the spawner returned an empty result or an error, the hook never
replaces stale cache prose with silence.

Window computation
------------------
``chronicler_day_close`` runs at ``01:05`` **in the owner's general timezone**
for the *previous local* day.  The scheduler evaluates the cron's hour field in
the owner timezone (``_effective_schedule_timezone``), so for an Asia/Singapore
owner the job fires at 01:05 SGT — which is 17:05 UTC on the *previous* UTC
calendar day.

The closed day therefore MUST be computed in the owner's timezone, not UTC.
Computing "yesterday" off the UTC date double-counts the offset and yields the
day *two* local days before delivery (the bug behind issue #2681: a day-close
for D surfacing on D+2 SGT instead of D+1).  The hook computes:

    today_local = run_at.astimezone(owner_tz).date()
    yesterday   = today_local - timedelta(days=1)
    start_at    = midnight(yesterday) in owner_tz, converted to UTC
    end_at      = midnight(today_local) in owner_tz, converted to UTC (exclusive)

``cache_key`` is ``day_close:{YYYY-MM-DD}:tz:{IANA-timezone}`` where the
tuple is the closed *local* day and the timezone that determined its window.

Provenance extraction
---------------------
The SpawnerResult carries ``tool_calls``.  The hook scans tool calls for the
day-close bundle result first, then legacy ``chronicler_list_episodes`` and
``chronicler_list_events`` results, extracting ``source_ref`` values for cache
staleness.  User-facing prose does not need to print these machine refs; when no
tool-call provenance is available the hook falls back to an empty list (the
prose still persists — provenance is best-effort).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import asyncpg

from butlers.chronicler.day_close_cache import day_close_cache_key, lock_day_close_cache_tuple
from butlers.chronicler.editorial import record_coverage_witness
from butlers.chronicler.prose_admission import classify_day_close_candidate
from butlers.chronicler.storage import upsert_tier2_cache

logger = logging.getLogger(__name__)

# Name of the scheduled task this writer handles.
DAY_CLOSE_TASK_NAME = "chronicler_day_close"


@dataclass(frozen=True)
class DayCloseCacheWriteOutcome:
    """Deterministic admission outcome for a completed day-close candidate."""

    invalid_reason: str | None = None
    quiet: bool = False


def _coerce_zone(tz: str | ZoneInfo | None) -> ZoneInfo:
    """Resolve a timezone to a ``ZoneInfo``, failing open to UTC.

    Mirrors the scheduler's fail-open behaviour: an unknown/typo'd IANA name is
    logged and treated as UTC so a bad timezone never wedges the day-close hook.
    """
    if isinstance(tz, ZoneInfo):
        return tz
    candidate = (tz or "").strip()
    if not candidate or candidate.upper() == "UTC":
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("day_close_writer: unknown timezone %r; closing day in UTC", tz)
        return ZoneInfo("UTC")


def _extract_provenance_refs(tool_calls: list[dict[str, Any]]) -> list[str]:
    """Extract source_ref strings from chronicler list tool-call results.

    Scans canonical runtime captures (``name``) and legacy fixtures
    (``tool``) for calls to
    ``chronicler_day_close_bundle``, ``chronicler_list_episodes``, or
    ``chronicler_list_events`` and pulls ``source_ref`` values from their
    results.  Deduplicates while preserving order.

    Returns an empty list if no provenance can be extracted.
    """
    refs: list[str] = []
    seen: set[str] = set()
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        tool_name: str = call.get("name") or call.get("tool") or ""
        if tool_name not in {
            "chronicler_day_close_bundle",
            "chronicler_list_episodes",
            "chronicler_list_events",
        }:
            continue
        result_raw = call.get("result")
        if result_raw is None:
            continue
        if isinstance(result_raw, str):
            try:
                result_raw = json.loads(result_raw)
            except (json.JSONDecodeError, TypeError):
                continue
        if not isinstance(result_raw, dict):
            continue
        if tool_name == "chronicler_day_close_bundle":
            for ref in result_raw.get("citations") or []:
                if isinstance(ref, str) and ref and ref not in seen:
                    refs.append(ref)
                    seen.add(ref)
            continue
        items = result_raw.get("data") or result_raw.get("items") or []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            ref = item.get("source_ref")
            if isinstance(ref, str) and ref and ref not in seen:
                refs.append(ref)
                seen.add(ref)
    return refs


def _canonical_bundle_calls(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return canonical-name day-close bundle records in their original order."""
    return [
        call
        for call in tool_calls
        if isinstance(call, dict) and call.get("name") == "chronicler_day_close_bundle"
    ]


def _validated_canonical_bundle_capture(
    tool_calls: list[dict[str, Any]],
    *,
    target_date_iso: str | None = None,
    target_timezone: str | None = None,
) -> dict[str, Any] | None:
    """Return the one fully bound executed bundle capture, or ``None``.

    Codex's parser can retain a parser-only record beside the daemon's executed
    capture when their full-input fingerprints differ (for example, because
    the wrapper includes handler defaults). The parser record has no
    ``outcome`` key and is not execution proof, so it is ignored only as that
    duplicate. A second outcome-bearing record, or any missing/malformed or
    mismatched execution record, fails closed.
    """
    canonical_calls = _canonical_bundle_calls(tool_calls)
    if not canonical_calls:
        return None

    executed_calls = [call for call in canonical_calls if "outcome" in call]
    if len(executed_calls) != 1:
        return None

    call = executed_calls[0]
    input_raw = call.get("input")
    result_raw = call.get("result")
    if (
        call.get("outcome") != "success"
        or not isinstance(input_raw, dict)
        or not isinstance(result_raw, dict)
    ):
        return None

    input_date = input_raw.get("date_label")
    result_date = result_raw.get("date")
    if (
        not isinstance(input_date, str)
        or not input_date
        or not isinstance(result_date, str)
        or input_date != result_date
    ):
        return None
    if target_date_iso is not None and input_date != target_date_iso:
        return None
    if target_timezone is not None and input_raw.get("timezone") != target_timezone:
        return None
    return call


def _extract_date_label(
    tool_calls: list[dict[str, Any]],
    *,
    target_date_iso: str | None = None,
    target_timezone: str | None = None,
) -> str | None:
    """Extract the structured date_label the day-close prompt bound to.

    Exactly one successful outcome-bearing canonical bundle capture is required
    when canonical records are present. Parser-only duplicates are tolerated,
    but they cannot establish proof and a second execution record fails closed.
    Legacy ``tool``/``result`` fixtures remain supported only when no canonical
    bundle capture exists. This extraction is cache-admission metadata only;
    legacy calls never establish a coverage witness.
    """
    canonical_calls = _canonical_bundle_calls(tool_calls)
    if canonical_calls:
        capture = _validated_canonical_bundle_capture(
            tool_calls,
            target_date_iso=target_date_iso,
            target_timezone=target_timezone,
        )
        if capture is None:
            return None
        return capture["input"]["date_label"]

    # Legacy fixtures predate executed tool-call capture. Keep their narrow
    # ``tool``/``result`` shape compatible while runtime captures use the
    # validated canonical form above.
    legacy_calls = [
        call
        for call in tool_calls
        if isinstance(call, dict) and call.get("tool") == "chronicler_day_close_bundle"
    ]
    if len(legacy_calls) != 1:
        return None
    result_raw = legacy_calls[0].get("result")
    if isinstance(result_raw, str):
        try:
            result_raw = json.loads(result_raw)
        except (json.JSONDecodeError, TypeError):
            return None
    if isinstance(result_raw, dict):
        date_val = result_raw.get("date")
        if isinstance(date_val, str) and date_val:
            return date_val
    return None


def _has_matching_coverage_bundle_capture(
    tool_calls: list[dict[str, Any]],
    *,
    target_date_iso: str,
    target_timezone: str,
) -> bool:
    """Return whether exactly one canonical bundle capture proves the target.

    Coverage is a stronger claim than cache admission. It therefore accepts no
    legacy ``tool`` records or decoded string payloads: the executed runtime
    capture must contain dict ``input`` and ``result`` payloads, report
    ``outcome=success``, and bind both its date and timezone to this writer's
    explicit local-day target.
    """
    return (
        _validated_canonical_bundle_capture(
            tool_calls,
            target_date_iso=target_date_iso,
            target_timezone=target_timezone,
        )
        is not None
    )


def _is_empty_bundle_result(capture: dict[str, Any]) -> bool:
    """Return whether a validated executed bundle explicitly contains no evidence."""
    result_raw = capture.get("result")
    if not isinstance(result_raw, dict):
        return False
    episodes = result_raw.get("episodes")
    events = result_raw.get("events")
    return isinstance(episodes, list) and not episodes and isinstance(events, list) and not events


def _has_list_shaped_bundle_evidence(capture: dict[str, Any]) -> bool:
    """Return whether a validated capture carries both evidence collections."""
    result_raw = capture.get("result")
    return (
        isinstance(result_raw, dict)
        and isinstance(result_raw.get("episodes"), list)
        and isinstance(result_raw.get("events"), list)
    )


def _compute_day_window(
    run_at: datetime, tz: str | ZoneInfo = "UTC"
) -> tuple[date, datetime, datetime]:
    """Return (day_date, start_at, end_at) for the day closed by run_at.

    ``chronicler_day_close`` targets *yesterday* relative to its run time, where
    "day" means a calendar day in the owner's timezone *tz* — NOT a UTC day.
    The cron fires at 01:05 local, so the closed day is the local calendar day
    immediately before the fire's local date; a day-close for D is delivered on
    D+1 local (issue #2681).

    The returned window is ``[midnight(yesterday), midnight(today))`` expressed
    in *tz* and converted to UTC for storage / querying.
    """
    zone = _coerce_zone(tz)
    aware_run = run_at if run_at.tzinfo else run_at.replace(tzinfo=UTC)
    today_local = aware_run.astimezone(zone).date()
    yesterday_local = today_local - timedelta(days=1)
    start_at = datetime(
        yesterday_local.year, yesterday_local.month, yesterday_local.day, tzinfo=zone
    ).astimezone(UTC)
    end_at = datetime(today_local.year, today_local.month, today_local.day, tzinfo=zone).astimezone(
        UTC
    )
    return yesterday_local, start_at, end_at


async def write_day_close_cache(
    pool: asyncpg.Pool,
    *,
    task_name: str,
    result: Any,
    run_at: datetime,
    tz: str | ZoneInfo = "UTC",
    target_date: date | None = None,
) -> DayCloseCacheWriteOutcome | None:
    """Post-execution hook: record coverage and persist day-close prose.

    Called by the scheduler tick after ``chronicler_day_close`` dispatches.

    Records a covered-local-day witness (``editorial.record_coverage_witness``)
    only after exactly one successful canonical ``chronicler_day_close_bundle``
    capture binds dict input/result values to the target date and timezone and
    carries list-shaped ``episodes`` and ``events`` evidence. A valid empty
    capture still records coverage before the empty-output return, so a
    genuinely quiet closed day remains distinct from one never chronicled.
    Legacy date-label extraction remains cache-admission compatibility only
    and cannot prove coverage.

    The tier2_cache prose write is separately gated by the deterministic
    day-close admission predicate (``prose_admission.classify_day_close_candidate``):
    an inadmissible-shape or date-mismatched candidate is never written over
    an existing admissible row (it would silently replace a renderable
    entry); with no existing admissible row, the invalid candidate is still
    persisted (marked ``invalid_reason``) for audit/recovery, never rendered.

    Args:
        pool: asyncpg pool for the chronicler DB (scoped to the chronicler schema).
        task_name: Scheduled task name (must be ``DAY_CLOSE_TASK_NAME``).
        result: SpawnerResult (or None) returned by the dispatch.
        run_at: Wall-clock time the tick fired (used to compute the day window).
        tz: Owner timezone the closed day is computed in (default ``UTC``).  The
            daemon binds the owner's general timezone so the closed day matches
            the local SGT calendar day (issue #2681).
        target_date: Explicit closed local date for a manual historical refresh.
            Scheduler calls omit it and derive the target from ``run_at``.
    """
    if task_name != DAY_CLOSE_TASK_NAME:
        return

    # Defensive: accept either a SpawnerResult dataclass or a plain dict.
    if result is None:
        logger.debug("day_close_writer: result is None, skipping cache write")
        return

    if hasattr(result, "success"):
        success: bool = bool(result.success)
        output: str | None = getattr(result, "output", None)
        tool_calls: list[dict[str, Any]] = list(getattr(result, "tool_calls", None) or [])
    elif isinstance(result, dict):
        success = bool(result.get("success", False))
        output = result.get("output")
        tool_calls = list(result.get("tool_calls") or [])
    else:
        logger.warning("day_close_writer: unrecognised result type %s, skipping", type(result))
        return

    if not success:
        logger.debug("day_close_writer: dispatch was not successful, skipping cache write")
        return

    day_date, start_at, end_at = _compute_day_window(run_at, tz)
    if target_date is not None:
        day_date = target_date
        zone = _coerce_zone(tz)
        start_at = datetime(day_date.year, day_date.month, day_date.day, tzinfo=zone).astimezone(
            UTC
        )
        next_local_date = day_date + timedelta(days=1)
        end_at = datetime(
            next_local_date.year,
            next_local_date.month,
            next_local_date.day,
            tzinfo=zone,
        ).astimezone(UTC)
    # Bind the witness to the same resolved timezone used to calculate the
    # local-day window (including the writer's documented UTC fail-open).
    tz_name = str(_coerce_zone(tz))
    validated_capture = _validated_canonical_bundle_capture(
        tool_calls,
        target_date_iso=day_date.isoformat(),
        target_timezone=tz_name,
    )

    if validated_capture is not None and _has_list_shaped_bundle_evidence(validated_capture):
        try:
            await record_coverage_witness(pool, day_date, tz_name)
        except Exception:
            logger.exception(
                "day_close_writer: failed to record coverage witness for %s", day_date.isoformat()
            )
    else:
        logger.debug(
            "day_close_writer: no list-shaped canonical coverage capture for %s; "
            "witness not recorded",
            day_date.isoformat(),
        )

    if not output or not output.strip():
        if validated_capture is not None and _is_empty_bundle_result(validated_capture):
            logger.debug("day_close_writer: validated empty bundle closed a quiet day")
            return DayCloseCacheWriteOutcome(quiet=True)
        logger.debug("day_close_writer: output is empty without a valid quiet bundle")
        return

    cache_key = day_close_cache_key(day_date, tz_name)
    provenance_refs = _extract_provenance_refs(tool_calls)
    date_label = _extract_date_label(
        tool_calls,
        target_date_iso=day_date.isoformat(),
        target_timezone=tz_name,
    )
    prose = output.strip()
    invalid_reason = classify_day_close_candidate(
        prose, date_label=date_label, expected_date_iso=day_date.isoformat()
    )

    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Serialize the existing-row check and all writes for this
                # exact local-day tuple. The lock registry's composite primary
                # key avoids the collision risk of a fixed-width advisory hash.
                # Otherwise an invalid candidate can inspect an absent/invalid
                # row, a concurrent valid writer can commit, and the stale
                # invalid path can overwrite that valid prose.
                await lock_day_close_cache_tuple(conn, day_date, tz_name)

                if invalid_reason is not None:
                    existing = await conn.fetchrow(
                        "SELECT prose, date_label, invalid_reason FROM tier2_cache"
                        " WHERE cache_key = $1 AND superseded_at IS NULL",
                        cache_key,
                    )
                    existing_is_admissible = (
                        existing is not None
                        and existing.get("invalid_reason") is None
                        and classify_day_close_candidate(
                            existing.get("prose"),
                            date_label=existing.get("date_label"),
                            expected_date_iso=day_date.isoformat(),
                        )
                        is None
                    )
                    if existing_is_admissible:
                        # An admissible row already renders for this date; an
                        # invalid candidate SHALL NOT replace it (design.md
                        # decision 2).
                        logger.warning(
                            "day_close_writer: rejected invalid candidate for %s (%s), "
                            "existing admissible cache row preserved",
                            cache_key,
                            invalid_reason,
                        )
                        return DayCloseCacheWriteOutcome(invalid_reason=invalid_reason)
                    # No existing admissible row: persist the invalid candidate
                    # for audit/recovery. The reader never returns its prose.
                    await upsert_tier2_cache(
                        conn,
                        cache_key=cache_key,
                        start_at=start_at,
                        end_at=end_at,
                        prose=prose,
                        provenance_refs=provenance_refs,
                        date_label=date_label,
                        invalid_reason=invalid_reason,
                    )
                    logger.warning(
                        "day_close_writer: contained invalid candidate for %s (%s)",
                        cache_key,
                        invalid_reason,
                    )
                    return DayCloseCacheWriteOutcome(invalid_reason=invalid_reason)

                await upsert_tier2_cache(
                    conn,
                    cache_key=cache_key,
                    start_at=start_at,
                    end_at=end_at,
                    prose=prose,
                    provenance_refs=provenance_refs,
                    date_label=date_label,
                    invalid_reason=None,
                )
        logger.info(
            "day_close_writer: wrote tier2_cache[%s] (%d provenance refs)",
            cache_key,
            len(provenance_refs),
        )
        return DayCloseCacheWriteOutcome()
    except Exception:
        logger.exception(
            "day_close_writer: failed to write tier2_cache[%s] — cache miss will occur",
            cache_key,
        )


def build_day_close_completion_hooks(
    pool: asyncpg.Pool,
    *,
    timezone: str | ZoneInfo = "UTC",
    store_fact_fn: Callable[..., Any] | None = None,
    propose_enrichment_fn: Callable[..., Any] | None = None,
) -> dict[str, Callable[..., Any]]:
    """Return the completion_hooks dict for the chronicler scheduler loop.

    The returned dict maps ``chronicler_day_close`` to a partial of
    :func:`write_day_close_cache` with the pool and owner *timezone* pre-bound.
    The daemon passes the owner's resolved general timezone so the closed day is
    the local calendar day, matching the timezone the cron fires in (#2681).

    When ``store_fact_fn`` is supplied (bu-93y4rt, tasks.md §8) the hook also
    runs the deterministic memory write-back loop after the prose is cached:
    it synthesizes derived insights + self-reminders from the chronicler's OWN
    ``daily_rollups`` and writes them into the chronicler's OWN memory schema,
    and — when ``propose_enrichment_fn`` is also supplied — proposes recurring
    companions to relationship over MCP. The write-back is best-effort and adds
    no owner-facing message; a failure never breaks the cache write.

    Usage::

        hooks = build_day_close_completion_hooks(db.pool, timezone=owner_tz)
        await scheduler_loop(..., completion_hooks=hooks)
    """

    async def _hook(*, task_name: str, result: Any, run_at: datetime) -> None:
        await write_day_close_cache(
            pool, task_name=task_name, result=result, run_at=run_at, tz=timezone
        )
        if task_name != DAY_CLOSE_TASK_NAME or store_fact_fn is None:
            return
        # Run the once-daily memory write-back beside the cache write. It reads
        # the chronicler's own rollups (not the prose), so it runs regardless of
        # whether the summary itself was non-empty — but never raises upward.
        day_date, _, _ = _compute_day_window(run_at, timezone)
        try:
            from butlers.chronicler.writeback import run_day_close_writeback

            wb = await run_day_close_writeback(
                pool,
                day_date=day_date,
                timezone=str(timezone),
                store_fact_fn=store_fact_fn,
                propose_enrichment_fn=propose_enrichment_fn,
            )
            logger.info(
                "day_close_writer: write-back for %s (%d insights, %d self-reminders, "
                "%d proposals, %d errors)",
                day_date.isoformat(),
                wb.insights_written,
                wb.self_reminders_written,
                wb.proposals_sent,
                wb.errors,
            )
        except Exception:
            logger.exception(
                "day_close_writer: memory write-back failed for %s", day_date.isoformat()
            )

    return {DAY_CLOSE_TASK_NAME: _hook}
