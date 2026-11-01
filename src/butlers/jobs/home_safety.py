"""Home safety sentinel: a deterministic, read-only owner alert for leak, smoke, CO and gas.

The owner declares which binary sensors guard against a life-safety hazard in
``home.safety_watch`` (``entity_id``, ``role`` in leak|smoke|co|gas, an owner-written
``owner_label``).  Two triggers feed the same engine, :func:`reconcile_safety`:

* the Home module's websocket ``state_changed`` hook (seconds-fast, one entity), and
* the five-minute ``home_safety_sentinel`` job over ``ha_entity_snapshot`` (the backstop that
  also resolves by omission when a complete, healthy snapshot no longer shows the alarm).

State lives in ``public.owner_conditions`` (source ``home:safety``, fingerprint
``(entity_id, role)``), so the hook and the job dedupe on the ledger rather than on each other.
An opened, reopened or escalation-due condition sends a high-priority owner message straight
through the Switchboard ``deliver`` tool: quiet hours and the context bus never hold a
life-safety alert, and the insight broker is not involved.

Hard rules (RFC 0038 D18, deterministic job outside goals):

* No model reads sensor data, and nothing here calls a Home Assistant service.  There is no
  actuation path in this module at all.
* The message names the owner's ``owner_label`` and a fixed hazard phrase, never HA's
  ``friendly_name`` (an untrusted feed field).
* A sensor must hold ``on`` for :data:`DEBOUNCE_SECONDS` before anything opens, so a flapping
  sensor opens nothing.
* An unreadable sensor (``unavailable``, ``unknown``, missing from the snapshot) and an
  unhealthy HA source never resolve a condition: failure must not impersonate "all clear".
* If delivery fails the condition stays open; the next run retries once the condition is
  older than :data:`RETRY_AFTER_SECONDS`, and the ledger's own re-escalation re-sends later.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import asyncpg

from butlers.core.attention_ledger import record_attention_event
from butlers.core.attention_urgency import home_safety_decision
from butlers.core.owner_conditions import (
    Observation,
    compute_fingerprint,
    get_active_condition,
    reconcile_snapshot,
    resolve_condition,
)
from butlers.core.tool_call_capture import get_current_switchboard_client
from butlers.credential_store import resolve_owner_telegram_recipient

logger = logging.getLogger(__name__)

SAFETY_SOURCE = "home:safety"
SAFETY_ROLES: tuple[str, ...] = ("leak", "smoke", "co", "gas")
DEBOUNCE_SECONDS = 60.0
#: First re-escalation (and so re-send) of a still-active alarm; later ones follow the ledger.
ESCALATION_GRACE_SECONDS = 900.0
#: A condition whose delivery never succeeded is retried by later runs after this long, so the
#: edge hook and the reconcile job do not both send for the one condition they just opened.
RETRY_AFTER_SECONDS = 120.0
_FINGERPRINT_VERSION = 1
_DELIVER_TIMEOUT_S = 30
_ORIGIN = "home"

_HAZARD_PHRASE: dict[str, str] = {
    "leak": "water leak detected",
    "smoke": "smoke detected",
    "co": "carbon monoxide detected",
    "gas": "gas detected",
}

Verdict = Literal["alarm", "pending", "clear", "unknown"]
NotifyFn = Callable[[str], Awaitable[bool]]


@dataclass(frozen=True)
class WatchEntry:
    """One owner-declared safety sensor."""

    entity_id: str
    role: str
    owner_label: str


@dataclass(frozen=True)
class SensorReading:
    """What the feed currently says about a sensor; ``since`` is when it last changed state."""

    state: str | None
    since: datetime | None


@dataclass(frozen=True)
class SafetyRunResult:
    alarms: int = 0
    pending: int = 0
    unknown: int = 0
    notified: int = 0
    notify_failed: int = 0
    resolved: int = 0


def safety_fingerprint(entity_id: str, role: str) -> str:
    """The condition identity: the sensor and the hazard it guards, nothing mutable."""
    return compute_fingerprint(
        SAFETY_SOURCE, _FINGERPRINT_VERSION, {"entity_id": entity_id, "role": role}
    )


def classify(entry: WatchEntry, reading: SensorReading | None, now: datetime) -> Verdict:
    """Judge one sensor.  Only a debounced ``on`` is an alarm; only an explicit ``off`` is clear."""
    if reading is None or reading.state is None:
        return "unknown"
    state = reading.state.strip().lower()
    if state == "off":
        return "clear"
    if state != "on":
        return "unknown"
    if reading.since is None:
        return "pending"
    if (now - reading.since).total_seconds() >= DEBOUNCE_SECONDS:
        return "alarm"
    return "pending"


_MARKDOWN_CHARS = re.compile(r"[*_`\[\]\x00-\x1f\x7f]")


def _clean_label(label: str) -> str:
    return _MARKDOWN_CHARS.sub("", label).strip()[:80] or "Safety sensor"


def alert_message(entry: WatchEntry, reading: SensorReading, now: datetime) -> str:
    """Owner-facing text: the owner's label and a fixed hazard phrase, never HA's own naming."""
    text = f"ALERT: {_clean_label(entry.owner_label)}: {_HAZARD_PHRASE[entry.role]}"
    if reading.since is not None:
        minutes = max(0, int((now - reading.since).total_seconds() // 60))
        text += f" ({minutes} min ago)" if minutes else " (just now)"
    return text


def unreadable_message(entry: WatchEntry) -> str:
    """Fixed text for an open alarm whose sensor has stopped reporting."""
    return (
        f"ALERT: {_clean_label(entry.owner_label)}: {_HAZARD_PHRASE[entry.role]}"
        " (sensor not reporting)"
    )


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


async def reconcile_safety(
    pool: asyncpg.Pool,
    entries: Sequence[WatchEntry],
    readings: Mapping[str, SensorReading],
    *,
    now: datetime,
    snapshot_complete: bool,
    notify: NotifyFn,
) -> SafetyRunResult:
    """Reconcile the watched sensors against the ledger and alert on what needs alerting.

    ``snapshot_complete`` is true only for a healthy, whole-snapshot read (the job): entries that
    are clear or no longer declared are then resolved by omission.  Without it (the edge hook)
    a sensor read as ``off`` is resolved explicitly and nothing else is.
    """
    verdicts: dict[str, Verdict] = {}
    prior: dict[str, dict[str, Any] | None] = {}
    observations: list[Observation] = []
    messages: dict[str, str] = {}
    counts = {"alarms": 0, "pending": 0, "unknown": 0}

    for entry in entries:
        fingerprint = safety_fingerprint(entry.entity_id, entry.role)
        reading = readings.get(entry.entity_id)
        verdict = classify(entry, reading, now)
        verdicts[fingerprint] = verdict
        if verdict == "alarm":
            assert reading is not None
            counts["alarms"] += 1
            prior[fingerprint] = await get_active_condition(
                pool, source=SAFETY_SOURCE, fingerprint=fingerprint
            )
            carried = _as_dict(_as_dict(prior[fingerprint]).get("metadata"))
            messages[fingerprint] = alert_message(entry, reading, now)
            metadata = {
                "entity_id": entry.entity_id,
                "role": entry.role,
                "owner_label": _clean_label(entry.owner_label),
                "detected_since": reading.since.isoformat() if reading.since else None,
            }
            if carried.get("notified_at"):
                metadata["notified_at"] = carried["notified_at"]
            observations.append(
                Observation(
                    fingerprint=fingerprint, summary=messages[fingerprint], metadata=metadata
                )
            )
        elif verdict in ("pending", "unknown"):
            counts[verdict] += 1
            # Carried forward, never resolved: a debouncing or unreadable sensor is not "clear".
            observations.append(Observation(fingerprint=fingerprint, confirm_only=True))
            # An already-open alarm must stay audible while its sensor is unreadable or
            # re-debouncing: queue the message so the retry/escalation logic below can fire.
            prior[fingerprint] = await get_active_condition(
                pool, source=SAFETY_SOURCE, fingerprint=fingerprint
            )
            if prior[fingerprint] is not None:
                if verdict == "pending" and reading is not None:
                    since = prior[fingerprint].get("first_detected_at") or reading.since
                    messages[fingerprint] = alert_message(entry, SensorReading("on", since), now)
                else:
                    messages[fingerprint] = unreadable_message(entry)

    transitions = await reconcile_snapshot(
        pool,
        source=SAFETY_SOURCE,
        observations=observations,
        snapshot_complete=snapshot_complete,
        initial_grace_seconds=ESCALATION_GRACE_SECONDS,
    )
    resolved = sum(1 for t in transitions if t.transition == "resolved")

    if not snapshot_complete:
        for fingerprint, verdict in verdicts.items():
            if verdict == "clear":
                done = await resolve_condition(
                    pool,
                    source=SAFETY_SOURCE,
                    fingerprint=fingerprint,
                    resolution_metadata={"cleared_by": "sensor_off"},
                )
                resolved += 1 if done is not None else 0

    by_fingerprint = {t.fingerprint: t for t in transitions}
    notified = notify_failed = 0
    for fingerprint, message in messages.items():
        transition = by_fingerprint.get(fingerprint)
        if transition is None:
            continue
        due = transition.transition in ("opened", "reopened", "escalation_due")
        if not due and transition.transition == "confirmed":
            before = prior.get(fingerprint)
            undelivered = before is not None and not _as_dict(before.get("metadata")).get(
                "notified_at"
            )
            age = (now - before["first_detected_at"]).total_seconds() if before else 0.0
            due = undelivered and age >= RETRY_AFTER_SECONDS
        if not due:
            continue
        delivered = False
        try:
            delivered = await notify(message)
        except Exception:
            logger.warning("home safety notify raised for %s", fingerprint, exc_info=True)
        if not delivered:
            notify_failed += 1
            continue
        notified += 1
        entry = next(e for e in entries if safety_fingerprint(e.entity_id, e.role) == fingerprint)
        await reconcile_snapshot(
            pool,
            source=SAFETY_SOURCE,
            observations=[
                Observation(
                    fingerprint=fingerprint,
                    summary=message,
                    metadata={
                        "entity_id": entry.entity_id,
                        "role": entry.role,
                        "owner_label": _clean_label(entry.owner_label),
                        "notified_at": now.isoformat(),
                    },
                    confirm_only=True,
                )
            ],
            snapshot_complete=False,
            initial_grace_seconds=ESCALATION_GRACE_SECONDS,
        )

    return SafetyRunResult(
        alarms=counts["alarms"],
        pending=counts["pending"],
        unknown=counts["unknown"],
        notified=notified,
        notify_failed=notify_failed,
        resolved=resolved,
    )


async def send_urgent_notify(pool: asyncpg.Pool, message: str) -> bool:
    """Deliver a high-priority owner message and report whether it was accepted.

    Unlike the routine Home job helper this never consults quiet hours or the context bus: a
    life-safety alert is not held.  Every outcome is recorded in the attention ledger.  The
    Switchboard client comes from the ambient contextvar the scheduler (or the Home module's edge
    hook) binds for the duration of the call.
    """

    cause = home_safety_decision()

    async def _record(outcome: str, reason: str | None = None, ref: str | None = None) -> None:
        await record_attention_event(
            pool,
            origin_butler=_ORIGIN,
            source="notify",
            outcome=outcome,
            channel="telegram",
            intent="send",
            priority="high",
            reason=reason,
            notification_ref=ref,
            metadata={"attention_cause": cause.cause, "producer": cause.ref},
        )

    recipient = await resolve_owner_telegram_recipient(pool)
    if not recipient:
        logger.warning("home safety: no owner telegram recipient configured")
        await _record("failed", "no_recipient_configured")
        return False
    client = get_current_switchboard_client()
    if client is None:
        logger.warning("home safety: switchboard client unavailable")
        await _record("failed", "switchboard_client_unavailable")
        return False
    request = {
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
            client.call_tool("deliver", {"source_butler": _ORIGIN, "notify_request": request}),
            timeout=_DELIVER_TIMEOUT_S,
        )
    except Exception as exc:
        logger.warning("home safety: deliver() failed: %s", type(exc).__name__)
        await _record("failed", f"delivery_error:{type(exc).__name__}")
        return False
    if result.is_error:
        await _record("failed", "delivery_error:remote_refusal")
        return False
    data = result.data
    if isinstance(data, dict) and data.get("status") == "failed":
        await _record("failed", "delivery_error:remote_refusal")
        return False
    await _record("delivered", ref=data.get("notification_id") if isinstance(data, dict) else None)
    return True


async def load_watch(pool: asyncpg.Pool) -> list[WatchEntry]:
    """The owner's declared sensors, from ``home.safety_watch``."""
    rows = await pool.fetch(
        "SELECT entity_id, role, owner_label FROM safety_watch ORDER BY entity_id, role"
    )
    return [WatchEntry(r["entity_id"], r["role"], r["owner_label"]) for r in rows]


async def edge_check(
    pool: asyncpg.Pool,
    entity_id: str,
    *,
    read_reading: Callable[[], SensorReading | None],
    notify: NotifyFn,
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    max_waits: int = 5,
) -> SafetyRunResult | None:
    """React to one sensor's state change: wait out the debounce, then reconcile that sensor.

    ``read_reading`` returns the sensor's current state from the module's live cache, so a flap
    that settles ``on`` is judged on its last change and a flap that ends ``off`` opens nothing.
    Returns ``None`` when the entity is not a declared safety sensor.  Never a complete snapshot:
    only an explicit ``off`` resolves here, everything else is left to the five-minute job.
    """
    entries = [e for e in await load_watch(pool) if e.entity_id == entity_id]
    if not entries:
        return None
    for _ in range(max_waits):
        reading = read_reading()
        now = now_fn()
        if reading is None or classify(entries[0], reading, now) != "pending":
            break
        remaining = DEBOUNCE_SECONDS - (
            (now - reading.since).total_seconds() if reading.since else 0.0
        )
        await sleep(max(remaining, 0.0) + 0.5)
    final = read_reading()
    return await reconcile_safety(
        pool,
        entries,
        {entity_id: final} if final is not None else {},
        now=now_fn(),
        snapshot_complete=False,
        notify=notify,
    )


async def run_safety_sentinel(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None = None,
    *,
    notify_fn: NotifyFn | None = None,
    _now: datetime | None = None,
) -> dict[str, Any]:
    """The five-minute reconcile: read the snapshot, reconcile the ledger, alert the owner.

    Does nothing (and so resolves nothing) when the HA source is not confirmed healthy.
    """
    del job_args
    from butlers.jobs.home import HASourceUnmeasurableError, _require_ha_source_healthy

    entries = await load_watch(pool)
    if not entries:
        return {"status": "no_watch"}
    try:
        await _require_ha_source_healthy(pool)
    except HASourceUnmeasurableError as exc:
        logger.warning("home safety sentinel: HA source unmeasurable (%s); nothing resolved", exc)
        return {"status": "source_unmeasurable"}

    rows = await pool.fetch(
        "SELECT entity_id, state, last_updated FROM ha_entity_snapshot WHERE entity_id = ANY($1)",
        [e.entity_id for e in entries],
    )
    readings = {
        r["entity_id"]: SensorReading(
            state=r["state"],
            since=r["last_updated"].astimezone(UTC) if r["last_updated"] else None,
        )
        for r in rows
    }
    result = await reconcile_safety(
        pool,
        entries,
        readings,
        now=_now or datetime.now(UTC),
        snapshot_complete=True,
        notify=notify_fn or (lambda message: send_urgent_notify(pool, message)),
    )
    return {"status": "ok", **result.__dict__}
