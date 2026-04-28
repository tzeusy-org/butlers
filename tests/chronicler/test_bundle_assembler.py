"""Tests for the Chronicler Tier-2 bundle assembler.

Covers:
- Sensitive events are masked unconditionally.
- Bundle size is deterministic given the same input.
- Low-signal payload keys are stripped.
- Per-source roll-up fires when threshold is exceeded.
- Hard cardinality cap is enforced.
- Character-budget trim removes tail items and marks truncated.
- Day-close bundle tool runs successfully on a high-volume synthetic day
  (validates it never overflows the budget).
- Citations are collected from non-rolled-up items.
- Empty input produces an empty bundle without errors.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from butlers.chronicler.bundle_assembler import (
    _STRIP_PAYLOAD_KEYS,
    BundleConfig,
    assemble_day_close_bundle,
)
from butlers.chronicler.interpretation import TierTwoPath

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _episode(
    source_name: str = "core.sessions",
    source_ref: str = "core.sessions:ep-1",
    title: str | None = "Work session",
    privacy: str = "normal",
    start_at: datetime | None = None,
    payload: dict | None = None,
) -> dict:
    start_at = start_at or datetime(2026, 4, 25, 9, 0, tzinfo=UTC)
    return {
        "source_name": source_name,
        "source_ref": source_ref,
        "episode_type": "work",
        "start_at": start_at,
        "end_at": start_at + timedelta(hours=1),
        "precision": "exact",
        "title": title,
        "canonical_start_at": start_at,
        "canonical_end_at": start_at + timedelta(hours=1),
        "canonical_title": title,
        "canonical_privacy": privacy,
        "payload": payload or {},
    }


def _event(
    source_name: str = "owntracks.points",
    source_ref: str = "owntracks.points:pt-1",
    title: str | None = "Location update",
    privacy: str = "normal",
    occurred_at: datetime | None = None,
    payload: dict | None = None,
) -> dict:
    occurred_at = occurred_at or datetime(2026, 4, 25, 12, 0, tzinfo=UTC)
    return {
        "source_name": source_name,
        "source_ref": source_ref,
        "event_type": "movement_point",
        "occurred_at": occurred_at,
        "precision": "exact",
        "title": title,
        "canonical_occurred_at": occurred_at,
        "canonical_title": title,
        "canonical_privacy": privacy,
        "payload": payload or {},
    }


# ---------------------------------------------------------------------------
# Sensitive masking
# ---------------------------------------------------------------------------


def test_sensitive_episodes_excluded_from_bundle() -> None:
    """Episodes with canonical_privacy='sensitive' MUST NOT appear in the bundle."""
    eps = [
        _episode(source_ref="core.sessions:ep-1", privacy="normal"),
        _episode(source_ref="core.sessions:ep-2", privacy="sensitive"),
        _episode(source_ref="core.sessions:ep-3", privacy="sensitive"),
    ]
    bundle = assemble_day_close_bundle(
        date_label="2026-04-25",
        episodes=eps,
        events=[],
    )
    refs = [e.get("source_ref") for e in bundle.bundle["episodes"]]
    assert "core.sessions:ep-2" not in refs
    assert "core.sessions:ep-3" not in refs
    assert "core.sessions:ep-1" in refs


def test_sensitive_events_excluded_from_bundle() -> None:
    """Events with canonical_privacy='sensitive' MUST NOT appear in the bundle."""
    evts = [
        _event(source_ref="owntracks.points:pt-1", privacy="normal"),
        _event(source_ref="owntracks.points:pt-2", privacy="SENSITIVE"),
        _event(source_ref="owntracks.points:pt-3", privacy="sensitive"),
    ]
    bundle = assemble_day_close_bundle(
        date_label="2026-04-25",
        episodes=[],
        events=evts,
    )
    refs = [e.get("source_ref") for e in bundle.bundle["events"]]
    assert "owntracks.points:pt-2" not in refs
    assert "owntracks.points:pt-3" not in refs
    assert "owntracks.points:pt-1" in refs


def test_sensitive_masking_works_with_enum_member() -> None:
    """Sensitive masking works when canonical_privacy is a Privacy Enum member, not a string.

    Rows coming from model objects (not via dataclasses.asdict) may carry Enum
    members.  The assembler must mask them correctly regardless.
    """
    from butlers.chronicler.models import Privacy

    # Build rows with actual Privacy Enum members, not plain strings.
    sensitive_ep = _episode(source_ref="core.sessions:ep-s", privacy="normal")
    sensitive_ep["canonical_privacy"] = Privacy.SENSITIVE
    normal_ep = _episode(source_ref="core.sessions:ep-n", privacy="normal")
    normal_ep["canonical_privacy"] = Privacy.NORMAL

    bundle = assemble_day_close_bundle(
        date_label="2026-04-25",
        episodes=[sensitive_ep, normal_ep],
        events=[],
    )
    refs = [e.get("source_ref") for e in bundle.bundle["episodes"]]
    assert "core.sessions:ep-s" not in refs
    assert "core.sessions:ep-n" in refs


def test_all_sensitive_yields_empty_bundle() -> None:
    """All-sensitive input produces empty bundle without errors."""
    eps = [_episode(privacy="sensitive") for _ in range(5)]
    evts = [_event(privacy="sensitive") for _ in range(5)]
    bundle = assemble_day_close_bundle(
        date_label="2026-04-25",
        episodes=eps,
        events=evts,
    )
    assert bundle.bundle["episodes"] == []
    assert bundle.bundle["events"] == []
    assert bundle.citations == []


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_bundle_is_deterministic_same_input() -> None:
    """Assembling the same input twice produces identical bundles."""
    eps = [_episode(source_ref=f"core.sessions:ep-{i}") for i in range(10)]
    evts = [_event(source_ref=f"owntracks.points:pt-{i}") for i in range(20)]

    b1 = assemble_day_close_bundle(date_label="2026-04-25", episodes=eps, events=evts)
    b2 = assemble_day_close_bundle(date_label="2026-04-25", episodes=eps, events=evts)

    assert json.dumps(b1.bundle, default=str) == json.dumps(b2.bundle, default=str)
    assert b1.citations == b2.citations


# ---------------------------------------------------------------------------
# Field stripping
# ---------------------------------------------------------------------------


def test_low_signal_payload_keys_stripped() -> None:
    """Payload keys in _STRIP_PAYLOAD_KEYS are removed from bundle items."""
    noisy_payload = {
        "raw": "big blob of text",
        "raw_blob": b"binary",
        "tid": "phone",
        "batt": 72,
        "acc": 15,
        "useful_key": "value to keep",
    }
    eps = [_episode(source_ref="core.sessions:ep-1", payload=noisy_payload)]
    bundle = assemble_day_close_bundle(
        date_label="2026-04-25",
        episodes=eps,
        events=[],
    )
    ep_entry = bundle.bundle["episodes"][0]
    payload = ep_entry.get("payload", {})
    for bad_key in _STRIP_PAYLOAD_KEYS:
        assert bad_key not in payload, f"Payload key '{bad_key}' should have been stripped"
    assert payload.get("useful_key") == "value to keep"


def test_empty_payload_after_stripping_omitted() -> None:
    """If all payload keys are stripped, payload is omitted from the entry."""
    noisy_payload = {k: "x" for k in ("raw", "raw_blob", "batt", "acc", "tid", "vel", "t")}
    eps = [_episode(payload=noisy_payload)]
    bundle = assemble_day_close_bundle(
        date_label="2026-04-25",
        episodes=eps,
        events=[],
    )
    ep_entry = bundle.bundle["episodes"][0]
    assert "payload" not in ep_entry


# ---------------------------------------------------------------------------
# Per-source roll-up
# ---------------------------------------------------------------------------


def test_per_source_rollup_fires_above_threshold() -> None:
    """A single source emitting > rollup_threshold events is collapsed."""
    cfg = BundleConfig(rollup_threshold=5)
    # 10 owntracks events — above threshold
    evts = [
        _event(source_ref=f"owntracks.points:pt-{i}", source_name="owntracks.points")
        for i in range(10)
    ]
    bundle = assemble_day_close_bundle(
        date_label="2026-04-25",
        episodes=[],
        events=evts,
        config=cfg,
    )
    assert len(bundle.bundle["events"]) == 1
    rollup_entry = bundle.bundle["events"][0]
    assert rollup_entry["rollup"] is True
    assert rollup_entry["event_count"] == 10
    assert rollup_entry["source_name"] == "owntracks.points"


def test_per_source_rollup_not_fired_below_threshold() -> None:
    """A source emitting <= rollup_threshold events is NOT rolled up."""
    cfg = BundleConfig(rollup_threshold=5)
    evts = [
        _event(source_ref=f"owntracks.points:pt-{i}", source_name="owntracks.points")
        for i in range(5)
    ]
    bundle = assemble_day_close_bundle(
        date_label="2026-04-25",
        episodes=[],
        events=evts,
        config=cfg,
    )
    assert len(bundle.bundle["events"]) == 5
    for entry in bundle.bundle["events"]:
        assert entry.get("rollup") is not True


def test_rollup_contains_time_range() -> None:
    """Roll-up entry carries time_range with first/last timestamps."""
    cfg = BundleConfig(rollup_threshold=3)
    base = datetime(2026, 4, 25, 10, 0, tzinfo=UTC)
    evts = [
        _event(
            source_ref=f"owntracks.points:pt-{i}",
            source_name="owntracks.points",
            occurred_at=base + timedelta(minutes=i * 10),
        )
        for i in range(5)
    ]
    bundle = assemble_day_close_bundle(
        date_label="2026-04-25",
        episodes=[],
        events=evts,
        config=cfg,
    )
    rollup = bundle.bundle["events"][0]
    assert "time_range" in rollup
    assert rollup["time_range"]["first"] < rollup["time_range"]["last"]


def test_rollup_distinct_subjects_collected() -> None:
    """Roll-up entry includes distinct subjects from canonical_title."""
    cfg = BundleConfig(rollup_threshold=2)
    base = datetime(2026, 4, 25, 10, 0, tzinfo=UTC)
    evts = [
        _event(
            source_ref=f"owntracks.points:pt-{i}",
            source_name="owntracks.points",
            title=["Home", "Office", "Gym"][i % 3],
            occurred_at=base + timedelta(minutes=i * 5),
        )
        for i in range(6)
    ]
    bundle = assemble_day_close_bundle(
        date_label="2026-04-25",
        episodes=[],
        events=evts,
        config=cfg,
    )
    rollup = bundle.bundle["events"][0]
    subjects = rollup.get("distinct_subjects", [])
    assert "Home" in subjects
    assert "Office" in subjects
    assert "Gym" in subjects


# ---------------------------------------------------------------------------
# Cardinality cap
# ---------------------------------------------------------------------------


def test_episode_cap_enforced() -> None:
    """max_episodes caps the number of episodes in the bundle."""
    cfg = BundleConfig(max_episodes=10, rollup_threshold=200)
    eps = [_episode(source_ref=f"core.sessions:ep-{i}") for i in range(50)]
    bundle = assemble_day_close_bundle(
        date_label="2026-04-25",
        episodes=eps,
        events=[],
        config=cfg,
    )
    assert len(bundle.bundle["episodes"]) <= 10
    assert bundle.bundle["episodes_truncated"] is True


def test_event_cap_enforced() -> None:
    """max_events caps the number of events in the bundle."""
    cfg = BundleConfig(max_events=15, rollup_threshold=200)
    evts = [_event(source_ref=f"owntracks.points:pt-{i}") for i in range(50)]
    bundle = assemble_day_close_bundle(
        date_label="2026-04-25",
        episodes=[],
        events=evts,
        config=cfg,
    )
    assert len(bundle.bundle["events"]) <= 15
    assert bundle.bundle["events_truncated"] is True


# ---------------------------------------------------------------------------
# Character-budget trim
# ---------------------------------------------------------------------------


def test_char_budget_trim_reduces_bundle() -> None:
    """If the bundle exceeds max_total_chars, tail items are removed."""
    # Each episode title is ~100 chars — 20 episodes should exceed a tight budget.
    eps = [
        _episode(
            source_ref=f"core.sessions:ep-{i}",
            title="A" * 100,
        )
        for i in range(20)
    ]
    cfg = BundleConfig(max_episodes=50, rollup_threshold=200, max_total_chars=500)
    bundle = assemble_day_close_bundle(
        date_label="2026-04-25",
        episodes=eps,
        events=[],
        config=cfg,
    )
    serialized = json.dumps(bundle.bundle, default=str)
    assert len(serialized) <= 500
    # At least some truncation occurred.
    assert bundle.bundle["episodes_truncated"] is True


def test_char_budget_zero_disables_trim() -> None:
    """max_total_chars=0 skips the character-budget trim entirely."""
    eps = [_episode(source_ref=f"core.sessions:ep-{i}", title="X" * 200) for i in range(30)]
    cfg = BundleConfig(max_episodes=50, rollup_threshold=200, max_total_chars=0)
    bundle = assemble_day_close_bundle(
        date_label="2026-04-25",
        episodes=eps,
        events=[],
        config=cfg,
    )
    # All 30 episodes should be present — no trim.
    assert len(bundle.bundle["episodes"]) == 30


# ---------------------------------------------------------------------------
# Citations
# ---------------------------------------------------------------------------


def test_citations_collected_from_non_rolled_items() -> None:
    """source_ref values from individual items appear in citations."""
    eps = [
        _episode(source_ref="core.sessions:ep-1"),
        _episode(source_ref="google_calendar.completed:ev-1"),
    ]
    bundle = assemble_day_close_bundle(
        date_label="2026-04-25",
        episodes=eps,
        events=[],
    )
    assert "core.sessions:ep-1" in bundle.citations
    assert "google_calendar.completed:ev-1" in bundle.citations


def test_citations_deduplicated() -> None:
    """The same source_ref appearing in episodes and events is deduped."""
    eps = [_episode(source_ref="core.sessions:ep-1")]
    evts = [_event(source_ref="core.sessions:ep-1")]
    bundle = assemble_day_close_bundle(
        date_label="2026-04-25",
        episodes=eps,
        events=evts,
    )
    assert bundle.citations.count("core.sessions:ep-1") == 1


def test_rollup_items_do_not_add_citations() -> None:
    """Roll-up summary entries carry no source_ref, so no citations from them."""
    cfg = BundleConfig(rollup_threshold=2)
    evts = [
        _event(source_ref=f"owntracks.points:pt-{i}", source_name="owntracks.points")
        for i in range(5)
    ]
    bundle = assemble_day_close_bundle(
        date_label="2026-04-25",
        episodes=[],
        events=evts,
        config=cfg,
    )
    # Roll-up entry has no source_ref; expect empty citations for this source.
    assert not any("owntracks.points:pt" in c for c in bundle.citations)


# ---------------------------------------------------------------------------
# Bundle structure
# ---------------------------------------------------------------------------


def test_bundle_has_correct_path() -> None:
    """Assembled bundle has TierTwoPath.DAY_CLOSE."""
    bundle = assemble_day_close_bundle(
        date_label="2026-04-25",
        episodes=[],
        events=[],
    )
    assert bundle.path == TierTwoPath.DAY_CLOSE


def test_bundle_date_label_preserved() -> None:
    """date_label is forwarded to the bundle payload."""
    bundle = assemble_day_close_bundle(
        date_label="2026-04-25",
        episodes=[],
        events=[],
    )
    assert bundle.bundle["date"] == "2026-04-25"


def test_empty_input_produces_valid_bundle() -> None:
    """Empty input yields a valid, zero-item bundle."""
    bundle = assemble_day_close_bundle(
        date_label="2026-04-25",
        episodes=[],
        events=[],
    )
    assert bundle.bundle["episodes"] == []
    assert bundle.bundle["events"] == []
    assert bundle.bundle["episodes_truncated"] is False
    assert bundle.bundle["events_truncated"] is False
    assert bundle.citations == []


# ---------------------------------------------------------------------------
# High-volume synthetic day — integration check
# ---------------------------------------------------------------------------


def test_high_volume_day_stays_within_tier2_budget() -> None:
    """A busy synthetic day (300 episodes + 2000 events) stays within budget.

    This is the integration check the bead specifies: day-close succeeds on
    a high-volume day without context_length_exceeded.  The bundle assembler
    must clamp both cardinality and character count.
    """
    from butlers.chronicler.interpretation import MAX_TIER_2_INPUT_BYTES

    # Simulate a busy day: lots of owntracks points, steam sessions, work episodes.
    base = datetime(2026, 4, 25, 0, 0, tzinfo=UTC)

    eps = []
    for i in range(100):
        eps.append(
            _episode(
                source_name="core.sessions",
                source_ref=f"core.sessions:ep-{i}",
                start_at=base + timedelta(hours=i % 24),
            )
        )
    for i in range(100):
        eps.append(
            _episode(
                source_name="steam.play_history",
                source_ref=f"steam.play_history:ep-{i}",
                start_at=base + timedelta(minutes=i * 5),
                payload={"appid": 12345, "rtime_last_played": 1714000000},
            )
        )
    for i in range(100):
        eps.append(
            _episode(
                source_name="google_calendar.completed",
                source_ref=f"google_calendar.completed:ev-{i}",
                start_at=base + timedelta(hours=i % 12),
            )
        )

    evts = []
    for i in range(2000):
        evts.append(
            _event(
                source_name="owntracks.points",
                source_ref=f"owntracks.points:pt-{i}",
                occurred_at=base + timedelta(minutes=i),
                payload={"acc": 10, "batt": 80, "vel": 0, "tid": "phone", "t": "p"},
            )
        )

    bundle = assemble_day_close_bundle(
        date_label="2026-04-25",
        episodes=eps,
        events=evts,
    )

    # Must pass the Tier-2 budget check.
    bundle.assert_within_budget(max_bytes=MAX_TIER_2_INPUT_BYTES)

    # Sensitive masking invariant (none in synthetic data — expect all normal).
    for ep in bundle.bundle["episodes"]:
        if ep.get("rollup"):
            continue
        assert ep.get("canonical_privacy", "normal") != "sensitive"
    for ev in bundle.bundle["events"]:
        if ev.get("rollup"):
            continue
        assert ev.get("canonical_privacy", "normal") != "sensitive"


def test_high_volume_all_sensitive_stays_within_budget() -> None:
    """Even if all sensitive rows are masked out, bundle is valid."""
    from butlers.chronicler.interpretation import MAX_TIER_2_INPUT_BYTES

    eps = [_episode(source_ref=f"ep-{i}", privacy="sensitive") for i in range(200)]
    evts = [_event(source_ref=f"ev-{i}", privacy="sensitive") for i in range(500)]
    bundle = assemble_day_close_bundle(
        date_label="2026-04-25",
        episodes=eps,
        events=evts,
    )
    bundle.assert_within_budget(max_bytes=MAX_TIER_2_INPUT_BYTES)
    assert bundle.bundle["episodes"] == []
    assert bundle.bundle["events"] == []
