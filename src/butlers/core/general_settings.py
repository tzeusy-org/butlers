"""Shared dashboard-level general settings."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import re
from typing import TypedDict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import asyncpg

from butlers.core.state import state_get, state_set

GENERAL_SETTINGS_STATE_KEY = "settings.general"
DEFAULT_GENERAL_TIMEZONE = "UTC"
DEFAULT_GENERAL_LANGUAGE = "en-US"
DEFAULT_GENERAL_DATE_FORMAT = "YYYY-mm-dd"
DEFAULT_GENERAL_TIME_FORMAT = "HH:MM"
DEFAULT_GENERAL_WEEK_STARTS_ON = "Monday"
DEFAULT_GENERAL_CURRENCY = "USD"
GENERAL_MEASUREMENT_SYSTEM = "metric"
ALLOWED_DATE_FORMATS = frozenset({"YYYY-mm-dd", "MM/dd/YYYY", "dd/MM/YYYY"})
ALLOWED_TIME_FORMATS = frozenset({"HH:MM", "hh:mm A"})
ALLOWED_WEEK_STARTS_ON = frozenset({"Monday", "Sunday"})
_LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


class StoredGeneralSettings(TypedDict):
    timezone: str
    language: str
    date_format: str
    time_format: str
    week_starts_on: str
    currency: str


class GeneralSettings(TypedDict):
    timezone: str
    timezone_label: str
    language: str
    date_format: str
    time_format: str
    week_starts_on: str
    currency: str
    measurement_system: str


def normalize_general_timezone(value: str | None) -> str:
    """Validate and normalize a configured general timezone."""
    candidate = (value or "").strip() or DEFAULT_GENERAL_TIMEZONE
    try:
        zone = ZoneInfo(candidate)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {candidate!r}") from exc
    return getattr(zone, "key", candidate)


def normalize_general_language(value: str | None) -> str:
    """Validate and normalize a configured general language/locale."""
    candidate = (value or "").strip() or DEFAULT_GENERAL_LANGUAGE
    if not _LANGUAGE_RE.fullmatch(candidate):
        raise ValueError(f"Invalid language/locale: {candidate!r}")
    return candidate


def normalize_general_date_format(value: str | None) -> str:
    """Validate the configured general date format."""
    candidate = (value or "").strip() or DEFAULT_GENERAL_DATE_FORMAT
    if candidate not in ALLOWED_DATE_FORMATS:
        raise ValueError(
            f"Invalid date_format: {candidate!r}. "
            f"Allowed values: {', '.join(sorted(ALLOWED_DATE_FORMATS))}"
        )
    return candidate


def normalize_general_time_format(value: str | None) -> str:
    """Validate the configured general time format."""
    candidate = (value or "").strip() or DEFAULT_GENERAL_TIME_FORMAT
    if candidate not in ALLOWED_TIME_FORMATS:
        raise ValueError(
            f"Invalid time_format: {candidate!r}. "
            f"Allowed values: {', '.join(sorted(ALLOWED_TIME_FORMATS))}"
        )
    return candidate


def normalize_general_week_starts_on(value: str | None) -> str:
    """Validate the configured general week start day."""
    candidate = (value or "").strip() or DEFAULT_GENERAL_WEEK_STARTS_ON
    if candidate not in ALLOWED_WEEK_STARTS_ON:
        raise ValueError(
            f"Invalid week_starts_on: {candidate!r}. "
            f"Allowed values: {', '.join(sorted(ALLOWED_WEEK_STARTS_ON))}"
        )
    return candidate


def normalize_general_currency(value: str | None) -> str:
    """Validate and normalize the configured general currency code."""
    candidate = (value or "").strip().upper() or DEFAULT_GENERAL_CURRENCY
    if not _CURRENCY_RE.fullmatch(candidate):
        raise ValueError(f"Invalid currency code: {candidate!r}")
    return candidate


def format_timezone_label(
    timezone: str,
    *,
    now: datetime | None = None,
) -> str:
    """Return ``<IANA name> (GMT±HH:MM)`` for display and prompt injection."""
    normalized = normalize_general_timezone(timezone)
    current = now or datetime.now(UTC)
    offset = current.astimezone(ZoneInfo(normalized)).utcoffset() or timedelta(0)
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    hours, minutes = divmod(total_minutes, 60)
    return f"{normalized} (GMT{sign}{hours:02d}:{minutes:02d})"


def build_general_timezone_instruction(settings: GeneralSettings) -> str:
    """Build the runtime instruction block for general settings."""
    return "\n".join(
        [
            "Unless otherwise stated, assume times and timezones are in "
            f"{settings['timezone_label']}.",
            f"Default language/locale: {settings['language']}.",
            f"Default date format: {settings['date_format']}.",
            f"Default time format: {settings['time_format']}.",
            f"Week starts on: {settings['week_starts_on']}.",
            f"Default currency: {settings['currency']}.",
            "Use metric measurements.",
        ]
    )


def _normalize_stored_settings(stored: object) -> StoredGeneralSettings:
    """Normalize a raw stored settings payload to the persisted shape."""
    raw = stored if isinstance(stored, dict) else {}
    raw_timezone = raw.get("timezone") if isinstance(raw, dict) else None
    raw_language = raw.get("language") if isinstance(raw, dict) else None
    raw_date_format = raw.get("date_format") if isinstance(raw, dict) else None
    raw_time_format = raw.get("time_format") if isinstance(raw, dict) else None
    raw_week_starts_on = raw.get("week_starts_on") if isinstance(raw, dict) else None
    raw_currency = raw.get("currency") if isinstance(raw, dict) else None
    return {
        "timezone": normalize_general_timezone(
            raw_timezone if isinstance(raw_timezone, str) else None
        ),
        "language": normalize_general_language(
            raw_language if isinstance(raw_language, str) else None
        ),
        "date_format": normalize_general_date_format(
            raw_date_format if isinstance(raw_date_format, str) else None
        ),
        "time_format": normalize_general_time_format(
            raw_time_format if isinstance(raw_time_format, str) else None
        ),
        "week_starts_on": normalize_general_week_starts_on(
            raw_week_starts_on if isinstance(raw_week_starts_on, str) else None
        ),
        "currency": normalize_general_currency(
            raw_currency if isinstance(raw_currency, str) else None
        ),
    }


async def load_general_settings(pool: asyncpg.Pool) -> GeneralSettings:
    """Load general settings from the shared state table, defaulting safely."""
    stored = await state_get(pool, GENERAL_SETTINGS_STATE_KEY)
    normalized = _normalize_stored_settings(stored)
    return {
        "timezone": normalized["timezone"],
        "timezone_label": format_timezone_label(normalized["timezone"]),
        "language": normalized["language"],
        "date_format": normalized["date_format"],
        "time_format": normalized["time_format"],
        "week_starts_on": normalized["week_starts_on"],
        "currency": normalized["currency"],
        "measurement_system": GENERAL_MEASUREMENT_SYSTEM,
    }


async def save_general_settings(
    pool: asyncpg.Pool,
    *,
    timezone: str,
    language: str,
    date_format: str,
    time_format: str,
    week_starts_on: str,
    currency: str,
) -> GeneralSettings:
    """Persist general settings into the shared state table."""
    normalized: StoredGeneralSettings = {
        "timezone": normalize_general_timezone(timezone),
        "language": normalize_general_language(language),
        "date_format": normalize_general_date_format(date_format),
        "time_format": normalize_general_time_format(time_format),
        "week_starts_on": normalize_general_week_starts_on(week_starts_on),
        "currency": normalize_general_currency(currency),
    }
    await state_set(pool, GENERAL_SETTINGS_STATE_KEY, normalized)
    return await load_general_settings(pool)
