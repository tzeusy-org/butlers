"""Wellness envelope ingest — translates ingest.v1 wellness envelopes into Health butler facts.

Receives a ``wellness/google_health`` ingest.v1 envelope (delivered by the connector
via the Switchboard → ``route.execute`` path) and deterministically converts it into
one or more facts stored via ``memory_store_fact``.

Predicate taxonomy (mem_003):
  sleep_session, sleep_stage_summary,
  measurement_resting_hr, measurement_hrv, measurement_spo2,
  measurement_breathing_rate, measurement_steps, measurement_active_minutes,
  measurement_vo2_max

Fan-out: the ``activity`` resource emits two facts per envelope —
  ``measurement_steps`` and ``measurement_active_minutes`` — with distinct
  idempotency keys suffixed ``:steps`` and ``:active_minutes``.

Fan-out: the ``sleep_session`` resource emits up to two facts per envelope —
  ``sleep_session`` (always) and ``sleep_stage_summary`` (only when stage data is
  present in ``payload.raw`` under ``stages`` or ``stageSummary``) — with distinct
  idempotency keys suffixed ``:session`` and ``:stage_summary``.

Prometheus counter ``health_wellness_ingest_total`` is incremented once per
emitted fact with labels ``predicate`` and ``outcome``
(success | error | skipped_* | rejected_*).
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any

from butlers.connectors.metrics import health_wellness_ingest_total
from butlers.credential_store import resolve_owner_entity_info
from butlers.google_account_registry import list_health_scoped_accounts
from butlers.modules.memory.tools.writing import memory_store_fact

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-session cache — recognised owner identity set
#
# Queried once at first invocation per butler-session and cached for the
# lifetime of the process.  Re-query on every daemon restart.
# ---------------------------------------------------------------------------

_recognised_owner_identities: frozenset[str] | None = None

# ---------------------------------------------------------------------------
# Predicate routing table
#
# Maps the resource segment of ``event.external_event_id`` to a tuple of
# canonical mem_003 predicate names.  Most resources map to a single predicate;
# ``activity`` fans out to two facts; ``sleep_session`` fans out to up to two.
#
# The connector emits external_event_id in these forms:
#   google_health:sleep_session:<session_id>   (sleep)
#   google_health:<resource>:<date>            (daily summaries)
#
# Keys MUST match the second colon-segment of the external_event_id exactly.
# Dead-code keys that no connector emits ("sleep", "sleep_stage", "steps",
# "active_minutes") have been removed.
# ---------------------------------------------------------------------------

_RESOURCE_TO_PREDICATES: dict[str, tuple[str, ...]] = {
    "sleep_session": ("sleep_session", "sleep_stage_summary"),
    "activity": ("measurement_steps", "measurement_active_minutes"),
    "resting_hr": ("measurement_resting_hr",),
    "hrv": ("measurement_hrv",),
    "spo2": ("measurement_spo2",),
    "breathing_rate": ("measurement_breathing_rate",),
    "vo2_max": ("measurement_vo2_max",),
}

# Predicates that are derived from embedded sub-data within a larger envelope.
# When their metadata extractor returns empty (data absent), the predicate is
# silently skipped rather than aborting the whole envelope.  The primary
# predicate for the same resource is still written.
_OPTIONAL_PREDICATES: frozenset[str] = frozenset({"sleep_stage_summary"})

# ---------------------------------------------------------------------------
# Metadata extractors per predicate
# ---------------------------------------------------------------------------


def _extract_sleep_session_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    """Extract structured metadata for a sleep session record.

    Requires at least a nonzero durationMillis (or duration_ms) field to be
    considered well-formed. Returns an empty dict when the duration is absent
    or zero, signalling that the record is malformed and should be skipped.

    Fields stored:
    - ``duration_ms``: session duration in milliseconds (required, non-zero)
    - ``efficiency``: sleep efficiency percentage when available
    - ``stages``: stage breakdown dict when available
    - ``end_time``: ISO-8601 end timestamp for the session (used by the
      Chronicler adapter to derive ``end_at`` on projected episodes)
    - ``session_id``: stable session identifier (used by the Chronicler
      adapter for cross-batch stitching); falls back to ``sessionId``; stored
      as None when absent or blank
    - ``minutes_asleep``: minutes of actual sleep when available
    - ``minutes_awake``: minutes awake during the session when available
    """
    duration_ms = int(raw.get("durationMillis") or raw.get("duration_ms") or 0)
    if duration_ms == 0:
        # No usable duration — treat as malformed.
        return {}

    efficiency = raw.get("efficiency") or raw.get("efficiencyPercent")
    stages: dict[str, Any] = {}
    if "stages" in raw:
        stages = raw["stages"]
    elif "stageSummary" in raw:
        stages = raw["stageSummary"]

    meta: dict[str, Any] = {"duration_ms": duration_ms}
    if efficiency is not None:
        meta["efficiency"] = efficiency
    if stages:
        meta["stages"] = stages

    # end_time: the Chronicler adapter reads metadata.end_time to derive end_at.
    # The connector normalises the Google Health endTime field to "endTime" in raw.
    end_time = raw.get("endTime") or raw.get("end_time")
    if end_time is not None:
        meta["end_time"] = str(end_time)

    # session_id: used by the Chronicler adapter for cross-batch stitching.
    # Treat blank strings as absent so we never store an empty session_id.
    session_id = raw.get("session_id")
    if session_id in (None, ""):
        session_id = raw.get("sessionId")
    meta["session_id"] = str(session_id) if session_id not in (None, "") else None

    # minutes_asleep / minutes_awake: optional enrichment fields.
    # Use explicit None/"" checks — 0 is a valid value and must not be dropped.
    minutes_asleep = raw.get("minutesAsleep")
    if minutes_asleep in (None, ""):
        minutes_asleep = raw.get("minutes_asleep")
    if minutes_asleep not in (None, ""):
        meta["minutes_asleep"] = int(minutes_asleep)
    minutes_awake = raw.get("minutesAwake")
    if minutes_awake in (None, ""):
        minutes_awake = raw.get("minutes_awake")
    if minutes_awake not in (None, ""):
        meta["minutes_awake"] = int(minutes_awake)

    return meta


def _extract_sleep_stage_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    """Extract structured metadata for a sleep stage summary record."""
    meta: dict[str, Any] = {}
    for key in ("stages", "stageSummary", "stage_summary"):
        val = raw.get(key)
        if val is not None:
            meta["stages"] = val
            break
    if "date" in raw:
        meta["date"] = raw["date"]
    return meta


def _extract_scalar_metadata(raw: dict[str, Any], unit: str) -> dict[str, Any]:
    """Extract a scalar value + unit from a daily-summary record."""
    value = None
    for key in ("value", "count", "avg", "average", "midpoint"):
        val = raw.get(key)
        if val is not None:
            value = val
            break
    meta: dict[str, Any] = {"unit": unit}
    if value is not None:
        meta["value"] = value
    return meta


_PREDICATE_UNIT: dict[str, str] = {
    "measurement_resting_hr": "bpm",
    "measurement_hrv": "ms",
    "measurement_spo2": "%",
    "measurement_breathing_rate": "breaths/min",
    "measurement_steps": "steps",
    "measurement_active_minutes": "min",
    "measurement_vo2_max": "ml/kg/min",
    "sleep_stage_summary": "",
}


def _extract_steps_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    """Extract step-count metadata from an activity daily-summary record."""
    value = None
    for key in ("steps", "step_count", "stepCount", "value", "count"):
        val = raw.get(key)
        if val is not None:
            value = val
            break
    meta: dict[str, Any] = {"unit": "steps"}
    if value is not None:
        meta["value"] = value
    return meta


def _extract_active_minutes_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    """Extract active-minutes metadata from an activity daily-summary record."""
    value = None
    for key in ("active_minutes", "activeMinutes", "activeDurationMinutes", "duration_min"):
        val = raw.get(key)
        if val is not None:
            value = val
            break
    meta: dict[str, Any] = {"unit": "min"}
    if value is not None:
        meta["value"] = value
    return meta


def _extract_metadata(predicate: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Dispatch metadata extraction to the appropriate extractor for *predicate*."""
    if predicate == "sleep_session":
        return _extract_sleep_session_metadata(raw)
    if predicate == "sleep_stage_summary":
        return _extract_sleep_stage_metadata(raw)
    if predicate == "measurement_steps":
        return _extract_steps_metadata(raw)
    if predicate == "measurement_active_minutes":
        return _extract_active_minutes_metadata(raw)
    unit = _PREDICATE_UNIT.get(predicate, "")
    return _extract_scalar_metadata(raw, unit)


# ---------------------------------------------------------------------------
# valid_at extraction
# ---------------------------------------------------------------------------


def _extract_valid_at(predicate: str, raw: dict[str, Any], observed_at: str) -> str:
    """Extract the most appropriate valid_at ISO string for a record.

    For sleep sessions: use session start time.
    For daily summaries: use the date field.
    Falls back to observed_at when no date field is present.
    """
    if predicate in ("sleep_session", "sleep_stage_summary"):
        for key in ("startTime", "start_time", "startAt", "start"):
            val = raw.get(key)
            if val:
                return str(val)
    else:
        # Daily summary — prefer the date the record applies to.
        for key in ("date", "startTime", "start_time"):
            val = raw.get(key)
            if val:
                val_str = str(val)
                # Normalise to ISO: if it's just YYYY-MM-DD append T00:00:00Z
                if "T" not in val_str and len(val_str) == 10:
                    return val_str + "T00:00:00Z"
                return val_str

    return observed_at


# ---------------------------------------------------------------------------
# Owner identity set — queried once per butler-session and cached
# ---------------------------------------------------------------------------


async def _get_recognised_owner_identities(pool: Any) -> frozenset[str]:
    """Return the set of email addresses for all active, health-scoped owner accounts.

    Results are cached for the lifetime of the butler session (process-level
    singleton).  This means a newly-added or revoked account is only reflected
    after a daemon restart, which is acceptable given the spec's "queried once
    per butler-session" requirement.

    Returns an empty frozenset when no qualifying accounts are found.  Callers
    should treat an empty set as a configuration problem and reject ingest.
    """
    global _recognised_owner_identities  # noqa: PLW0603
    if _recognised_owner_identities is not None:
        return _recognised_owner_identities

    try:
        accounts = await list_health_scoped_accounts(pool)
        emails = frozenset(a.email for a in accounts if a.email)
        _recognised_owner_identities = emails
        return emails
    except Exception as exc:  # noqa: BLE001
        logger.warning("wellness_ingest: failed to query health-scoped owner accounts: %s", exc)
        return frozenset()


# ---------------------------------------------------------------------------
# Main translation function
# ---------------------------------------------------------------------------


async def translate_wellness_envelope(
    pool: Any,
    embedding_engine: Any,
    envelope: dict[str, Any],
) -> dict[str, Any]:
    """Translate a wellness ingest.v1 envelope into one or more stored memory facts.

    Dispatches on ``source.provider`` (design ADR-4):

    - ``google_health`` — resource-segment translation of Google Health daily
      summaries / sleep sessions (unchanged historical behaviour).
    - ``home_assistant`` — normalized ``wellness_measurement`` payload → a single
      ``measurement_{metric}`` fact with provider-agnostic idempotency.

    Unknown providers are rejected with a labelled
    ``health_wellness_ingest_total`` outcome.

    Parameters
    ----------
    pool:
        asyncpg connection pool (health butler DB, with access to ``public`` schema).
    embedding_engine:
        Shared EmbeddingEngine instance (from memory module).
    envelope:
        An ingest.v1 wellness envelope dict.

    Returns
    -------
    dict with ``status`` and, on success, ``facts_written`` (int) and ``facts``
    (list of per-predicate result dicts).  Single-predicate resources also include
    top-level ``fact_id`` and ``predicate`` for backwards compatibility.
    """
    provider: str = envelope.get("source", {}).get("provider", "")
    if provider == "google_health":
        return await _translate_google_health_envelope(pool, embedding_engine, envelope)
    if provider == "home_assistant":
        return await _translate_home_assistant_envelope(pool, embedding_engine, envelope)

    logger.warning("wellness_ingest: unknown source.provider %r; rejecting envelope", provider)
    health_wellness_ingest_total.labels(
        predicate="unknown", outcome="rejected_unknown_provider"
    ).inc()
    return {"status": "rejected_unknown_provider"}


async def _translate_google_health_envelope(
    pool: Any,
    embedding_engine: Any,
    envelope: dict[str, Any],
) -> dict[str, Any]:
    """Translate a ``wellness/google_health`` envelope (resource-segment path).

    Possible statuses: ``ok``, ``rejected_non_owner_sender``,
    ``skipped_unknown_predicate``, ``skipped_malformed_payload``, ``error``.
    """
    # ------------------------------------------------------------------
    # Step 1: Validate sender against recognised owner identity set
    #
    # Accept any active, health-scoped account owned by the butler's owner
    # entity.  The identity set is queried once per butler-session and cached.
    # ------------------------------------------------------------------
    sender_identity: str = envelope.get("sender", {}).get("identity", "")
    recognised = await _get_recognised_owner_identities(pool)
    if not recognised:
        logger.warning(
            "wellness_ingest: no health-scoped owner accounts found; dropping envelope sender=%r",
            sender_identity,
        )
        health_wellness_ingest_total.labels(
            predicate="unknown", outcome="rejected_non_owner_sender"
        ).inc()
        return {"status": "rejected_non_owner_sender"}

    if sender_identity and sender_identity not in recognised:
        logger.warning(
            "wellness_ingest: sender %r is not a recognised owner identity %r; dropping",
            sender_identity,
            sorted(recognised),
        )
        health_wellness_ingest_total.labels(
            predicate="unknown", outcome="rejected_non_owner_sender"
        ).inc()
        return {"status": "rejected_non_owner_sender"}

    # ------------------------------------------------------------------
    # Step 2: Resolve owner entity UUID
    # ------------------------------------------------------------------
    owner_entity_id_str = await resolve_owner_entity_info(pool, "owner")
    if owner_entity_id_str is None:
        # Also try querying public.entities directly (same as measurements.py)
        try:
            row = await pool.fetchrow(
                "SELECT id FROM public.entities WHERE 'owner' = ANY(roles) LIMIT 1"
            )
            if row is not None:
                owner_entity_id_str = str(row["id"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("wellness_ingest: owner entity fallback query failed: %s", exc)

    if owner_entity_id_str is None:
        logger.warning("wellness_ingest: owner entity not found; dropping envelope")
        health_wellness_ingest_total.labels(predicate="unknown", outcome="error").inc()
        return {"status": "error", "reason": "owner_entity_not_found"}

    # ------------------------------------------------------------------
    # Step 3: Derive predicates from external_event_id
    # ------------------------------------------------------------------
    external_event_id: str = envelope.get("event", {}).get("external_event_id", "")
    # Format: "google_health:<account_email>:<resource>:<date_or_id>"
    # (bu-91zdb.4 added the account_email segment, making this 4-segment)
    parts = external_event_id.split(":", 3)
    resource_segment = parts[2] if len(parts) >= 3 else ""

    predicates = _RESOURCE_TO_PREDICATES.get(resource_segment)
    if predicates is None:
        logger.warning(
            "wellness_ingest: unknown resource segment %r in external_event_id %r; skipping",
            resource_segment,
            external_event_id,
        )
        health_wellness_ingest_total.labels(
            predicate=resource_segment or "unknown", outcome="skipped_unknown_predicate"
        ).inc()
        return {"status": "skipped_unknown_predicate"}

    # ------------------------------------------------------------------
    # Step 4: Extract shared envelope fields
    # ------------------------------------------------------------------
    payload = envelope.get("payload", {})
    raw: dict[str, Any] = payload.get("raw") or {}
    observed_at: str = envelope.get("event", {}).get("observed_at", "")
    normalized_text: str = payload.get("normalized_text", "")
    base_idempotency_key: str = envelope.get("control", {}).get("idempotency_key", "")

    # ------------------------------------------------------------------
    # Step 5: Fan-out — write one fact per predicate
    # ------------------------------------------------------------------
    fan_out = len(predicates) > 1
    written_facts: list[dict[str, Any]] = []

    for predicate in predicates:
        # Idempotency key — suffix with predicate suffix for fan-out cases so
        # each fact gets its own unique key while replay still deduplicates.
        if fan_out and base_idempotency_key:
            # Use a stable short suffix derived from the predicate name:
            # measurement_steps → :steps, measurement_active_minutes → :active_minutes
            suffix = predicate.split("_", 1)[-1] if "_" in predicate else predicate
            ikey: str | None = f"{base_idempotency_key}:{suffix}"
        else:
            ikey = base_idempotency_key or None

        valid_at = _extract_valid_at(predicate, raw, observed_at)

        try:
            metadata = _extract_metadata(predicate, raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "wellness_ingest: metadata extraction failed for predicate=%r: %s",
                predicate,
                exc,
            )
            health_wellness_ingest_total.labels(
                predicate=predicate, outcome="skipped_malformed_payload"
            ).inc()
            if predicate in _OPTIONAL_PREDICATES:
                continue
            return {"status": "skipped_malformed_payload"}

        if not metadata:
            logger.warning(
                "wellness_ingest: empty metadata for predicate=%r; skipping",
                predicate,
            )
            health_wellness_ingest_total.labels(
                predicate=predicate, outcome="skipped_malformed_payload"
            ).inc()
            if predicate in _OPTIONAL_PREDICATES:
                continue
            return {"status": "skipped_malformed_payload"}

        content = normalized_text or f"wellness:{predicate}:{valid_at}"

        try:
            result = await memory_store_fact(
                pool,
                embedding_engine,
                subject="owner",
                predicate=predicate,
                content=content,
                scope="health",
                permanence="standard",
                valid_at=valid_at,
                metadata=metadata if metadata else None,
                entity_id=owner_entity_id_str,
                idempotency_key=ikey,
                retention_class="operational",
                sensitivity="normal",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "wellness_ingest: memory_store_fact failed for predicate=%r: %s",
                predicate,
                exc,
            )
            health_wellness_ingest_total.labels(predicate=predicate, outcome="error").inc()
            return {"status": "error", "reason": str(exc)}

        health_wellness_ingest_total.labels(predicate=predicate, outcome="success").inc()
        written_facts.append({"fact_id": result.get("id"), "predicate": predicate})

    # ------------------------------------------------------------------
    # Step 6: Return summary (backwards-compat: single-predicate path keeps
    # top-level fact_id/predicate fields alongside the new facts list).
    # ------------------------------------------------------------------
    response: dict[str, Any] = {
        "status": "ok",
        "facts_written": len(written_facts),
        "facts": written_facts,
    }
    if len(written_facts) == 1:
        response["fact_id"] = written_facts[0]["fact_id"]
        response["predicate"] = written_facts[0]["predicate"]
    return response


# ---------------------------------------------------------------------------
# Home Assistant provider arm (design ADR-4/5)
# ---------------------------------------------------------------------------


def _agnostic_idempotency_key(
    owner_entity_id: str,
    scope: str,
    predicate: str,
    valid_at_iso: str,
) -> str:
    """Provider-agnostic temporal-fact idempotency key (design ADR-5).

    ``sha256("wellness|{owner_entity_id}|{scope}|{predicate}|{valid_at_iso}")[:32]``

    Deliberately omits the provider and the source episode id so that the same
    physical reading delivered through two providers (or replayed) at the same
    ``valid_at`` resolves to one fact, first-writer-wins, via the storage
    layer's ``(tenant_id, idempotency_key)`` no-op check.
    """
    parts = f"wellness|{owner_entity_id}|{scope}|{predicate}|{valid_at_iso}"
    return hashlib.sha256(parts.encode()).hexdigest()[:32]


async def _translate_home_assistant_envelope(
    pool: Any,
    embedding_engine: Any,
    envelope: dict[str, Any],
) -> dict[str, Any]:
    """Translate a ``wellness/home_assistant`` envelope into one measurement fact.

    Sender validation (design ADR-4): the HA arm does NOT consult
    ``list_health_scoped_accounts``.  Under the single-owner federation rule,
    any HA endpoint configured in this instance is the owner's; acceptance pins
    on ``source.provider == "home_assistant"`` (already dispatched here) plus a
    well-formed ``payload.raw.wellness_measurement`` payload.

    Possible statuses: ``ok``, ``rejected_malformed_payload``, ``error``.
    """
    # ------------------------------------------------------------------
    # Step 1: Validate the normalized measurement payload shape
    # ------------------------------------------------------------------
    payload = envelope.get("payload", {})
    raw: dict[str, Any] = payload.get("raw") or {}
    measurement = raw.get("wellness_measurement")

    def _reject(reason: str) -> dict[str, Any]:
        logger.warning("wellness_ingest[home_assistant]: %s; rejecting envelope", reason)
        health_wellness_ingest_total.labels(
            predicate="unknown", outcome="rejected_malformed_payload"
        ).inc()
        return {"status": "rejected_malformed_payload", "reason": reason}

    if not isinstance(measurement, dict):
        return _reject("missing wellness_measurement payload")

    metric = measurement.get("metric")
    if not metric or not isinstance(metric, str):
        return _reject("missing or invalid metric")

    valid_at = measurement.get("valid_at")
    if not valid_at or not isinstance(valid_at, str):
        return _reject("missing or invalid valid_at")

    source_entity_id = measurement.get("source_entity_id")
    if not source_entity_id or not isinstance(source_entity_id, str):
        return _reject("missing or invalid source_entity_id")

    raw_value = measurement.get("value")
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
        return _reject("missing or non-numeric value")
    value: int | float = raw_value

    # ------------------------------------------------------------------
    # Step 2: Resolve owner entity UUID (same path as the google_health arm)
    # ------------------------------------------------------------------
    owner_entity_id_str = await resolve_owner_entity_info(pool, "owner")
    if owner_entity_id_str is None:
        try:
            row = await pool.fetchrow(
                "SELECT id FROM public.entities WHERE 'owner' = ANY(roles) LIMIT 1"
            )
            if row is not None:
                owner_entity_id_str = str(row["id"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("wellness_ingest[home_assistant]: owner entity fallback failed: %s", exc)

    if owner_entity_id_str is None:
        logger.warning("wellness_ingest[home_assistant]: owner entity not found; dropping envelope")
        health_wellness_ingest_total.labels(predicate="unknown", outcome="error").inc()
        return {"status": "error", "reason": "owner_entity_not_found"}

    # ------------------------------------------------------------------
    # Step 3: Derive predicate, valid_at ISO, metadata, idempotency key
    # ------------------------------------------------------------------
    predicate = f"measurement_{metric}"
    scope = "health"
    unit = measurement.get("unit")

    # Normalise valid_at to an ISO string for a stable idempotency key.  Reuse
    # the payload string verbatim when it cannot be parsed (caller-supplied ISO).
    try:
        valid_at_iso = datetime.fromisoformat(valid_at).isoformat()
    except ValueError:
        valid_at_iso = valid_at

    idempotency_key = _agnostic_idempotency_key(owner_entity_id_str, scope, predicate, valid_at_iso)

    metadata: dict[str, Any] = {
        "provider": "home_assistant",
        "source_entity_id": source_entity_id,
        "unit": unit,
        "value": value,
    }

    content = payload.get("normalized_text") or f"wellness:{predicate}:{valid_at}"

    # ------------------------------------------------------------------
    # Step 4: Store the fact
    # ------------------------------------------------------------------
    try:
        result = await memory_store_fact(
            pool,
            embedding_engine,
            subject="owner",
            predicate=predicate,
            content=content,
            scope=scope,
            permanence="standard",
            valid_at=valid_at,
            metadata=metadata,
            entity_id=owner_entity_id_str,
            idempotency_key=idempotency_key,
            retention_class="operational",
            sensitivity="normal",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "wellness_ingest[home_assistant]: memory_store_fact failed for predicate=%r: %s",
            predicate,
            exc,
        )
        health_wellness_ingest_total.labels(predicate=predicate, outcome="error").inc()
        return {"status": "error", "reason": str(exc)}

    health_wellness_ingest_total.labels(predicate=predicate, outcome="success").inc()
    fact_id = result.get("id")
    return {
        "status": "ok",
        "facts_written": 1,
        "facts": [{"fact_id": fact_id, "predicate": predicate}],
        "fact_id": fact_id,
        "predicate": predicate,
    }
