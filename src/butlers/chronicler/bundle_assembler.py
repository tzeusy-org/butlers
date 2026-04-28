"""Tier-2 bundle assembler for Chronicler day-close (and related) paths.

Enforces hard caps on bundle size before passing structured data to any
Tier-2 LLM call.  Implements RFC 0014 §D5 requirements:

1. **Hard caps** — events per day, episodes per day, total characters.
2. **Field stripping** — remove low-signal payload keys before serializing.
3. **Per-source roll-up** — collapse high-volume source bursts into summaries.
4. **Sensitive-event masking** — ``canonical_privacy='sensitive'`` entries
   NEVER appear in the bundle; enforced here, not by the agent.

Usage
-----
    from butlers.chronicler.bundle_assembler import assemble_day_close_bundle, BundleConfig

    bundle = assemble_day_close_bundle(
        date_label="2026-04-25",
        episodes=episode_rows,
        events=event_rows,
    )
    # bundle is a TierTwoInput ready to pass to ``interpret()``

Design choices
--------------
- All functions are pure (no I/O, no LLM).  Callers pass in pre-fetched rows.
- ``BundleConfig`` exposes all tuning knobs; defaults are conservative.
- Sensitive masking is unconditional at the assembler boundary — the agent
  cannot see or override it.
- Per-source roll-up fires only when a source emits > ``rollup_threshold``
  items; below that threshold individual items pass through.
- Field stripping is applied to ``payload`` dicts only; canonical top-level
  fields (title, occurred_at, etc.) are always kept.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from butlers.chronicler.interpretation import TierTwoInput, TierTwoPath

# ---------------------------------------------------------------------------
# Low-signal payload keys stripped before serialization.
# Keys often injected by adapters that add noise without insight value.
# ---------------------------------------------------------------------------
_STRIP_PAYLOAD_KEYS: frozenset[str] = frozenset(
    {
        # Raw provider blobs — typically large and redundant.
        "raw",
        "raw_blob",
        "provider_blob",
        "raw_payload",
        # Redundant ID mirrors — IDs are on the top-level row already.
        "internal_id",
        "provider_id",
        "row_id",
        "db_id",
        # Debug / internal fields.
        "_debug",
        "_raw",
        "_original",
        # OwnTracks-specific high-volume keys.
        "acc",  # GPS accuracy in metres — not human-meaningful for summaries.
        "batt",  # Battery percentage.
        "vel",  # Velocity in km/h — present on individual points.
        "conn",  # Network connection type.
        "inregions",  # Region list; preserved as rolled-up summary.
        "t",  # Trigger character ('p', 'c', etc.) — raw protocol field.
        "tid",  # Tracker ID string — redundant with source_name/source_ref.
        # Steam-specific high-volume keys.
        "appid",  # Numeric app ID — title already carries the game name.
        "rtime_last_played",  # Unix timestamp duplicate of occurred_at / start_at.
        "playtime_2weeks",  # Rolling window not relevant for a day view.
    }
)

# Top-level fields to keep from each episode / event row (allowlist model).
# Any field not in this set is omitted from the bundle's representations.
_EPISODE_KEEP_FIELDS: tuple[str, ...] = (
    "source_name",
    "source_ref",
    "episode_type",
    "start_at",
    "end_at",
    "precision",
    "title",
    "canonical_start_at",
    "canonical_end_at",
    "canonical_title",
    "canonical_privacy",
    # lat/lon kept when present — useful for location episodes.
    "lat",
    "lon",
)

_EVENT_KEEP_FIELDS: tuple[str, ...] = (
    "source_name",
    "source_ref",
    "event_type",
    "occurred_at",
    "precision",
    "title",
    "canonical_occurred_at",
    "canonical_title",
    "canonical_privacy",
    "lat",
    "lon",
)


@dataclass
class BundleConfig:
    """Tuning knobs for the day-close bundle assembler.

    Defaults produce a bundle well within the 20 KB Tier-2 budget for a
    typical day (~30 episodes, ~100 events).  Increase limits only when
    measurements show the budget is not being exhausted.

    Attributes
    ----------
    max_episodes:
        Maximum number of episode entries in the final bundle.
        Prefer episodes over raw events when over budget; they are higher
        signal.
    max_events:
        Maximum number of event entries in the final bundle.
    rollup_threshold:
        A single source emitting more than this many items within the window
        is collapsed to a per-source roll-up summary instead of emitting
        individual items.  Applies independently to episodes and events.
    max_total_chars:
        Hard limit on the serialized JSON character count for the bundle.
        Items are removed tail-first until within budget.  Set to 0 to
        disable (use MAX_TIER_2_INPUT_BYTES from interpretation.py instead).
    """

    max_episodes: int = 50
    max_events: int = 100
    rollup_threshold: int = 10
    max_total_chars: int = 15_000


def _strip_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *payload* with low-signal keys removed."""
    return {k: v for k, v in payload.items() if k not in _STRIP_PAYLOAD_KEYS}


def _serialise_row(
    row: dict[str, Any],
    *,
    keep_fields: tuple[str, ...],
) -> dict[str, Any]:
    """Project a storage row to its bundle representation.

    Keeps only the fields in *keep_fields* plus a stripped version of the
    ``payload`` dict (if present).  Converts ``datetime`` values to ISO-8601
    strings and Enum members to their ``.value`` so the result is always
    JSON-serializable without relying on a custom encoder.
    """
    import enum
    from datetime import datetime

    result: dict[str, Any] = {}
    for key in keep_fields:
        if key in row:
            val = row[key]
            if isinstance(val, datetime):
                val = val.isoformat()
            elif isinstance(val, enum.Enum):
                val = val.value
            result[key] = val

    payload = row.get("payload")
    if isinstance(payload, dict) and payload:
        stripped = _strip_payload(payload)
        if stripped:
            result["payload"] = stripped

    return result


def _make_rollup(source_name: str, items: list[dict[str, Any]], *, kind: str) -> dict[str, Any]:
    """Collapse many items from one source into a single summary entry.

    Args:
        source_name: The originating source name.
        items: Serialised items to summarize.
        kind: Either ``"episode"`` or ``"event"``.

    Returns
        A single dict describing the roll-up.
    """
    count = len(items)

    # Collect time-range from canonical fields.
    timestamps: list[str] = []
    for item in items:
        for ts_key in (
            "canonical_start_at",
            "start_at",
            "canonical_occurred_at",
            "occurred_at",
        ):
            ts = item.get(ts_key)
            if ts:
                timestamps.append(str(ts))
                break

    time_range: dict[str, str] | None = None
    if timestamps:
        time_range = {"first": min(timestamps), "last": max(timestamps)}

    # Collect distinct titles as subjects.
    subjects: list[str] = []
    seen_subjects: set[str] = set()
    for item in items:
        title = item.get("canonical_title") or item.get("title")
        if isinstance(title, str) and title and title not in seen_subjects:
            subjects.append(title)
            seen_subjects.add(title)

    rollup: dict[str, Any] = {
        "source_name": source_name,
        "rollup": True,
        f"{kind}_count": count,
    }
    if time_range:
        rollup["time_range"] = time_range
    if subjects:
        rollup["distinct_subjects"] = subjects[:20]  # cap subject list

    return rollup


def _is_sensitive(row: dict[str, Any]) -> bool:
    """Return True when the row has canonical_privacy='sensitive'.

    Accepts plain strings (most common — rows arrive via ``dataclasses.asdict``
    which converts StrEnum members to their values) and bare Enum members (for
    callers that pass model objects directly).
    """
    privacy = row.get("canonical_privacy") or row.get("privacy")
    if privacy is None:
        return False
    val = getattr(privacy, "value", privacy)
    return str(val).lower() == "sensitive"


def _serialise_items(
    rows: Sequence[dict[str, Any]],
    *,
    keep_fields: tuple[str, ...],
    max_items: int,
    rollup_threshold: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Serialise, mask, possibly roll-up, and cap a list of rows.

    Returns
    -------
    items:
        List of bundle entries (individual rows or roll-up dicts).
    citations:
        Deduplicated list of ``source_ref`` strings cited in the items.
    """
    # --- 1. Mask sensitive rows ------------------------------------------------
    visible: list[dict[str, Any]] = [r for r in rows if not _is_sensitive(r)]

    # --- 2. Serialise to stripped dicts ----------------------------------------
    serialised: list[dict[str, Any]] = [_serialise_row(r, keep_fields=keep_fields) for r in visible]

    # --- 3. Per-source roll-up for high-volume sources -------------------------
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in serialised:
        by_source[item.get("source_name", "unknown")].append(item)

    kind = "episode" if keep_fields is _EPISODE_KEEP_FIELDS else "event"
    result: list[dict[str, Any]] = []
    for source_name, source_items in sorted(by_source.items()):
        if len(source_items) > rollup_threshold:
            result.append(_make_rollup(source_name, source_items, kind=kind))
        else:
            result.extend(source_items)

    # --- 4. Apply hard cardinality cap ----------------------------------------
    result = result[:max_items]

    # --- 5. Collect citations from remaining items ----------------------------
    citations: list[str] = []
    seen_refs: set[str] = set()
    for item in result:
        ref = item.get("source_ref")
        if isinstance(ref, str) and ref and ref not in seen_refs:
            citations.append(ref)
            seen_refs.add(ref)

    return result, citations


def assemble_day_close_bundle(
    *,
    date_label: str,
    episodes: Sequence[dict[str, Any]],
    events: Sequence[dict[str, Any]],
    config: BundleConfig | None = None,
) -> TierTwoInput:
    """Assemble a token-bounded day-close bundle.

    Applies sensitive masking, field stripping, per-source roll-up, and
    cardinality caps.  The result is ready to pass directly to
    :func:`~butlers.chronicler.interpretation.interpret`.

    The function is pure: it does no I/O and never calls an LLM.

    Args:
        date_label: ISO-8601 date string (``"YYYY-MM-DD"``) identifying the
            closed day.
        episodes: Sequence of row dicts from the corrected episodes view.
            Dicts should have at minimum the keys used by
            :data:`_EPISODE_KEEP_FIELDS`.
        events: Sequence of row dicts from the corrected point-events view.
        config: Optional tuning overrides.  Defaults to ``BundleConfig()``.

    Returns
        :class:`~butlers.chronicler.interpretation.TierTwoInput` with
        ``path=TierTwoPath.DAY_CLOSE``.
    """
    if config is None:
        config = BundleConfig()

    episode_items, ep_citations = _serialise_items(
        episodes,
        keep_fields=_EPISODE_KEEP_FIELDS,
        max_items=config.max_episodes,
        rollup_threshold=config.rollup_threshold,
    )
    event_items, ev_citations = _serialise_items(
        events,
        keep_fields=_EVENT_KEEP_FIELDS,
        max_items=config.max_events,
        rollup_threshold=config.rollup_threshold,
    )

    # Deduplicate citations across both groups.
    all_citations: list[str] = []
    seen: set[str] = set()
    for ref in ep_citations + ev_citations:
        if ref not in seen:
            all_citations.append(ref)
            seen.add(ref)

    bundle_payload: dict[str, Any] = {
        "date": date_label,
        "episodes": episode_items,
        "events": event_items,
        "episodes_truncated": len([r for r in episodes if not _is_sensitive(r)])
        > config.max_episodes,
        "events_truncated": len([r for r in events if not _is_sensitive(r)]) > config.max_events,
    }

    # --- Apply max_total_chars budget by trimming tails -----------------------
    # Prefer keeping episodes; trim events first, then episodes.
    # Use a single initial serialization to measure size, then subtract
    # per-item sizes to avoid re-serializing the full bundle each iteration.
    if config.max_total_chars > 0:
        import json

        serialized = json.dumps(bundle_payload, default=str)
        current_len = len(serialized)
        while current_len > config.max_total_chars:
            if bundle_payload["events"]:
                removed = bundle_payload["events"].pop()
                # Subtract the item + its JSON separator (`, ` or surrounding brackets).
                current_len -= len(json.dumps(removed, default=str)) + 2
                bundle_payload["events_truncated"] = True
            elif bundle_payload["episodes"]:
                removed = bundle_payload["episodes"].pop()
                current_len -= len(json.dumps(removed, default=str)) + 2
                bundle_payload["episodes_truncated"] = True
            else:
                break

    return TierTwoInput(
        path=TierTwoPath.DAY_CLOSE,
        bundle=bundle_payload,
        citations=all_citations,
    )


__all__ = [
    "BundleConfig",
    "assemble_day_close_bundle",
]
