"""Deterministic owner-day narration seeds, separate from source diagnostics.

This projection consumes only the bundle assembler's normal-privacy rows. It
never changes producer classifications, reconciliation, aggregate time or
memory admission. A source record is evidence of its declared observation,
not automatically an owner conversation, attendance or attentive listening.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from butlers.chronicler.aggregations import category_for

MAX_OBSERVATIONS = 12
_CHANNEL_NAMES = {
    "email": "email",
    "telegram_bot": "Telegram",
    "telegram_user_client": "Telegram",
    "whatsapp_user_client": "WhatsApp",
    "discord": "Discord",
}


def _window(
    row: dict[str, Any], start: datetime, end: datetime, *, event: bool = False
) -> tuple[datetime, datetime] | None:
    # Corrected values are effective. Only reconciled merge envelopes use the
    # canonical bounds that the existing reconciler deliberately expands.
    merged = bool(row.get("merged_source_refs"))
    left = row.get("occurred_at" if event else "canonical_start_at" if merged else "start_at")
    right = left if event else row.get("canonical_end_at" if merged else "end_at")
    if not isinstance(left, datetime) or left.utcoffset() is None:
        return None
    if right is None:
        right = left  # Unknown duration is never expanded into an invented day.
    if not isinstance(right, datetime) or right.utcoffset() is None:
        return None
    left, right = left.astimezone(UTC), right.astimezone(UTC)
    if right < left or left >= end or right < start or (right == start and left < start):
        return None
    return max(left, start), min(right, end)


def _sleep_overlap(
    window: tuple[datetime, datetime], sleep_windows: list[tuple[datetime, datetime, list[str]]]
) -> int:
    """Union clipped sleep spans so duplicate sleep records never inflate overlap."""
    segments = sorted((max(window[0], a), min(window[1], b)) for a, b, _ in sleep_windows)
    total = 0.0
    previous = window[0]
    for left, right in segments:
        left = max(left, previous)
        if right > left:
            total += (right - left).total_seconds()
            previous = right
    return int(total)


def _observation(
    row: dict[str, Any],
    window: tuple[datetime, datetime],
    *,
    timezone: ZoneInfo,
    sleep_windows: list[tuple[datetime, datetime, list[str]]],
) -> dict[str, Any] | None:
    source = row.get("source_name")
    kind = row.get("episode_type", row.get("event_type"))
    payload = row.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    channel_key = payload.get("channel")
    channel = (
        _CHANNEL_NAMES.get(channel_key, "an unresolved channel")
        if isinstance(channel_key, str)
        else "an unresolved channel"
    )
    # Routed sessions also process inbound feeds; a "conversation" title is
    # not evidence that the owner participated. Keep every record in diagnostics.
    if source == "core.sessions":
        return None
    if source == "comms.message_bursts" and payload.get("channel") == "email":
        return None  # This producer contains inbound metadata, never reply evidence.

    overlap = None
    if source == "google_health.measurements" and kind == "sleep_episode":
        category, text = "sleep", "Sleep was recorded."
    elif source in ("owntracks.ssid_presence", "owntracks.place_cluster"):
        place = payload.get("place", payload.get("label"))
        if place in (None, "", "place_unknown"):
            category, text = "place", "At an unlabelled place; its name is unknown."
        else:
            category, text = "place", f"At {str(place)[:100]}."
    elif source == "owntracks.points" and kind == "movement_episode":
        category, text = (
            "movement",
            "Location evidence suggests movement; destination and mode are unknown.",
        )
    elif source == "owner_outbound.messages" and kind == "owner_outbound_message":
        category, text = (
            "outbound_message",
            f"Sent a message via {channel}; recipient and any two-way exchange are unknown.",
        )
    elif source == "comms.message_bursts":
        category, text = (
            "communication_received",
            f"Messages arrived via {channel}; owner participation and any exchange are unknown.",
        )
    elif source == "spotify.session_summary":
        overlap = _sleep_overlap(window, sleep_windows)
        category = "playback"
        text = (
            "Spotify playback overlapped recorded sleep: background or uncertain playback, "
            "not confirmed listening."
            if overlap
            else "Spotify playback was recorded; attentive listening is not established."
        )
    elif str(row.get("layer")) == "intent":
        category, text = (
            "intent",
            f"Calendar had {str(row.get('title') or 'an appointment')[:100]} scheduled; "
            "attendance is unknown.",
        )
    else:
        lane = category_for(str(source), str(kind), trigger_source=payload.get("trigger_source"))
        if lane in ("other", "ambient"):
            return None  # A raw point or unfamiliar producer is not a fabricated activity.
        category, text = (
            "activity",
            str(
                row.get("title") or row.get("canonical_title") or "Activity evidence was recorded."
            )[:160],
        )

    confidence = str(row.get("confidence", "low"))
    if confidence not in {"low", "medium", "high"}:
        confidence = "low"
    precision = str(row.get("precision", "unknown"))
    if confidence == "low":
        text = "Tentative: " + text
    local_start, local_end = (value.astimezone(timezone) for value in window)
    prefix = "Around " if precision != "exact" else ""
    text = f"{prefix}{local_start:%H:%M}–{local_end:%H:%M}: {text}"
    if "episode_type" in row and row.get("end_at") is None:
        text += " Duration is unknown."
    refs = [row.get("source_ref"), *row.get("merged_source_refs", [])]
    if overlap:
        for left, right, sleep_refs in sleep_windows:
            if min(window[1], right) > max(window[0], left):
                refs.extend(sleep_refs)
    result = {
        "kind": category,
        "text": text,
        "start_at": local_start.isoformat(),
        "end_at": local_end.isoformat(),
        "confidence": confidence,
        "precision": precision,
        "source_refs": list(dict.fromkeys(ref for ref in refs if isinstance(ref, str) and ref)),
    }
    if overlap is not None:
        result["sleep_overlap_seconds"] = overlap
    return result


def _representative(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Bound each input population without letting one prolific kind dominate."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen = set()
    for item in sorted(items, key=lambda item: (item["start_at"], item["kind"])):
        identity = (item["kind"], item["start_at"], tuple(item["source_refs"]))
        if identity not in seen:
            seen.add(identity)
            groups[item["kind"]].append(item)
    result = []
    for index in range(2):
        for kind in (
            "sleep",
            "place",
            "movement",
            "outbound_message",
            "communication_received",
            "playback",
            "intent",
            "activity",
        ):
            if index < len(groups[kind]) and len(result) < max(0, limit):
                result.append(groups[kind][index])
    return result


def compose_owner_day(
    *,
    date_label: str,
    timezone: str,
    episodes: Sequence[dict[str, Any]],
    events: Sequence[dict[str, Any]],
    max_episodes: int,
    max_events: int,
) -> dict[str, Any]:
    """Bounded, representative observations with explicit uncertainty and provenance.

    Call only after the canonical privacy partition. Source inventories remain
    in the original bundle for diagnostics; this is the narration view, not a
    replacement location, communication, sleep or aggregate producer.
    """
    day = date.fromisoformat(date_label)
    zone = ZoneInfo(timezone)
    start = datetime(day.year, day.month, day.day, tzinfo=zone)
    end = start + timedelta(days=1)
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    sleep_windows = [
        (*window, [row["source_ref"]] if isinstance(row.get("source_ref"), str) else [])
        for row in episodes
        if row.get("source_name") == "google_health.measurements"
        and row.get("episode_type") == "sleep_episode"
        and (window := _window(row, start, end)) is not None
    ]
    candidates = []
    for rows, cap, is_event in ((episodes, max_episodes, False), (events, max_events, True)):
        group = []
        for row in rows:
            window = _window(row, start, end, event=is_event)
            if window is not None:
                observation = _observation(row, window, timezone=zone, sleep_windows=sleep_windows)
                if observation is not None:
                    group.append(observation)
        candidates.extend(_representative(group, min(cap, MAX_OBSERVATIONS)))
    observations = _representative(candidates, MAX_OBSERVATIONS)
    return {
        "observations": observations,
        "unknowns": ["Evidence is partial; gaps do not prove inactivity or a quiet day."],
    }
