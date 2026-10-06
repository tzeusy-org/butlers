"""Content-free contact-query evidence, independent of provider or transport health.

Admission epochs order observations under the existing heartbeat registration
boundary. They are public ordering metadata, never authentication authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

CONTACT_TTL_S = 900
HEARTBEAT_TTL_S = 300
CLASSIFICATION_KEY = "known_contact_check"
STATES = frozenset({"loaded", "unloaded", "stale", "failed"})
REASONS = frozenset(
    {
        "none",
        "not_loaded",
        "no_pool",
        "refreshing",
        "refresh_failed",
        "refresh_cancelled",
        "ttl_expired",
    }
)
ACK_REASONS = frozenset({"none", "invalid", "instance", "epoch", "generation"})


def utc_timestamp(value: object) -> datetime | None:
    """Accept only an explicitly UTC timestamp; reject naive and non-UTC values."""
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        return None
    return parsed.astimezone(UTC)


def _uuid(value: object) -> str | None:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


@dataclass(frozen=True)
class KnownContactSnapshot:
    contacts: frozenset[str] = frozenset()
    state: str = "unloaded"
    reason: str = "not_loaded"
    generation: int = 0
    last_success_at: datetime | None = None

    def projection(self) -> dict[str, Any]:
        return {
            "version": 1,
            "state": self.state,
            "reason": self.reason,
            "generation": self.generation,
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
        }

    def historical(self, observed_at: datetime) -> dict[str, Any]:
        return {**self.projection(), "observed_at": observed_at.isoformat()}


def parse_projection(value: object) -> dict[str, Any] | None:
    """Validate fixed query fields without including rejected input in errors."""
    if not isinstance(value, dict):
        return None
    if type(value.get("version")) is not int or value["version"] != 1:
        return None
    generation = value.get("generation")
    if type(generation) is not int or generation < 0:
        return None
    state, reason = value.get("state"), value.get("reason")
    if not isinstance(state, str) or not isinstance(reason, str):
        return None
    if state not in STATES or reason not in REASONS:
        return None
    coherent = {
        "loaded": {"none"},
        "unloaded": {"not_loaded", "no_pool", "refreshing"},
        "stale": {"ttl_expired"},
        "failed": {"refresh_failed", "refresh_cancelled"},
    }
    if reason not in coherent[state]:
        return None
    raw_success = value.get("last_success_at")
    success = utc_timestamp(raw_success) if raw_success is not None else None
    if raw_success is not None and success is None:
        return None
    if state in {"loaded", "stale"} and (success is None or generation == 0):
        return None
    return {
        "version": 1,
        "state": state,
        "reason": reason,
        "generation": generation,
        "last_success_at": success.isoformat() if success else None,
    }


def parse_check(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict) or set(value) != {
        "version",
        "state",
        "reason",
        "generation",
        "last_success_at",
        "instance_id",
        "admission_epoch",
    }:
        return None
    projection = parse_projection(value)
    instance = _uuid(value.get("instance_id"))
    raw_epoch = value.get("admission_epoch")
    epoch = _uuid(raw_epoch) if raw_epoch is not None else None
    if projection is None or instance is None or (raw_epoch is not None and epoch is None):
        return None
    return {**projection, "instance_id": instance, "admission_epoch": epoch}


def historical_available(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "version",
        "state",
        "reason",
        "generation",
        "last_success_at",
        "observed_at",
    }:
        return False
    parsed = parse_projection(value)
    observed = utc_timestamp(value.get("observed_at"))
    if parsed is None or parsed["state"] != "loaded" or observed is None:
        return False
    success = utc_timestamp(parsed["last_success_at"])
    return success is not None and success <= observed <= success + timedelta(seconds=CONTACT_TTL_S)


def current_available(value: object, *, instance: object, heartbeat: object, now: datetime) -> bool:
    parsed = parse_check(value)
    hb = utc_timestamp(heartbeat)
    if (
        parsed is None
        or parsed["state"] != "loaded"
        or parsed["admission_epoch"] is None
        or parsed["instance_id"] != _uuid(instance)
        or hb is None
        or not timedelta(0) <= now - hb <= timedelta(seconds=HEARTBEAT_TTL_S)
    ):
        return False
    success = utc_timestamp(parsed["last_success_at"])
    return success is not None and timedelta(0) <= now - success <= timedelta(seconds=CONTACT_TTL_S)


def parse_ack(value: object, sent: dict[str, Any]) -> dict[str, Any] | None:
    """ACK generation is the exact request echo, including refusals."""
    if not isinstance(value, dict) or set(value) != {
        "admitted",
        "instance_id",
        "generation",
        "request_admission_epoch",
        "admission_epoch",
        "reason",
    }:
        return None
    if type(value.get("admitted")) is not bool or type(value.get("generation")) is not int:
        return None
    if not isinstance(value.get("reason"), str) or value["reason"] not in ACK_REASONS:
        return None
    if any(value.get(k) != sent[k] for k in ("instance_id", "generation")):
        return None
    if value["request_admission_epoch"] != sent["admission_epoch"]:
        return None
    epoch = _uuid(value["admission_epoch"]) if value["admission_epoch"] is not None else None
    if value["admitted"]:
        if value["reason"] != "none" or epoch is None:
            return None
        if sent["admission_epoch"] is not None and epoch != sent["admission_epoch"]:
            return None
    elif value["admission_epoch"] is not None or value["reason"] == "none":
        return None
    return {**value, "admission_epoch": epoch}
