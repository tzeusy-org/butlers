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


def test_day_close_bundle_includes_local_display_timestamps() -> None:
    """Configured timezone produces local timestamp mirrors for day-close prose."""
    start_at = datetime(2026, 5, 12, 2, 5, tzinfo=UTC)
    event_at = datetime(2026, 5, 12, 10, 47, tzinfo=UTC)

    bundle = assemble_day_close_bundle(
        date_label="2026-05-12",
        timezone="Asia/Singapore",
        episodes=[_episode(source_ref="spotify:session-1", start_at=start_at)],
        events=[_event(source_ref="spotify:event-1", occurred_at=event_at)],
    )

    episode = bundle.bundle["episodes"][0]
    event = bundle.bundle["events"][0]
    assert bundle.bundle["timezone"] == "Asia/Singapore"
    assert episode["local_start_at"] == "2026-05-12T10:05:00+08:00"
    assert episode["local_canonical_end_at"] == "2026-05-12T11:05:00+08:00"
    assert event["local_occurred_at"] == "2026-05-12T18:47:00+08:00"


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


@pytest.mark.parametrize("privacy", ["sensitive", "restricted", None, "unrecognized"])
def test_effective_privacy_cannot_fall_back_to_normal_payload(privacy) -> None:
    row = _episode(
        source_name="google_health.measurements",
        source_ref="PRIVATE_REF_CANARY",
        title="PRIVATE_TITLE_CANARY",
        payload={"context_name": "PRIVATE_CONTEXT_CANARY", "category": "PRIVATE_CATEGORY_CANARY"},
    )
    row.update(privacy=privacy, episode_type="workout_episode", lat=1, lon=2)
    bundle = assemble_day_close_bundle(date_label="2026-04-25", episodes=[row], events=[])
    assert bundle.bundle["episodes"] == []
    assert bundle.citations == []
    if privacy == "sensitive":
        assert bundle.bundle["sensitive_envelopes"] == [
            {
                "start_at": "2026-04-25T09:00:00+00:00",
                "end_at": "2026-04-25T10:00:00+00:00",
                "category": "workout",
                "duration": 3600.0,
            }
        ]
    else:
        assert bundle.bundle["sensitive_envelopes"] == []
    assert "CANARY" not in json.dumps(bundle.bundle)


def test_sensitive_envelope_uses_corrected_window_and_elapsed_dst_duration() -> None:
    from zoneinfo import ZoneInfo

    zone = ZoneInfo("America/New_York")
    row = _episode(privacy="sensitive")
    row.update(
        start_at=datetime(2026, 3, 8, 1, 30, tzinfo=zone),
        end_at=datetime(2026, 3, 8, 3, 30, tzinfo=zone),
    )
    bundle = assemble_day_close_bundle(
        date_label="2026-03-08", timezone="America/New_York", episodes=[row], events=[]
    )
    assert bundle.bundle["sensitive_envelopes"] == [
        {
            "start_at": "2026-03-08T01:30:00-05:00",
            "end_at": "2026-03-08T03:30:00-04:00",
            "category": "tasks",
            "duration": 3600.0,
        }
    ]
    assert bundle.citations == []


@pytest.mark.parametrize("invalid", ["open", "reversed", "naive", "missing", "string"])
def test_sensitive_envelope_never_invents_an_unknown_window(invalid) -> None:
    row = _episode(privacy="sensitive")
    if invalid == "open":
        row["end_at"] = None
    elif invalid == "reversed":
        row["end_at"] = row["start_at"] - timedelta(seconds=1)
    elif invalid == "naive":
        row["start_at"] = row["start_at"].replace(tzinfo=None)
    elif invalid == "missing":
        del row["start_at"]
    else:
        row["start_at"] = row["start_at"].isoformat()
    bundle = assemble_day_close_bundle(date_label="2026-04-25", episodes=[row], events=[])
    assert bundle.bundle["sensitive_envelopes"] == []
    assert bundle.bundle["episodes"] == []
    assert bundle.citations == []


def test_sensitive_envelopes_share_existing_caps_and_complete_tier_two_budget() -> None:
    from butlers.chronicler.interpretation import MAX_TIER_2_INPUT_BYTES

    episodes = [_episode()] + [_episode(privacy="sensitive") for _ in range(20)]
    events = [_event()] + [_event(privacy="sensitive") for _ in range(20)]
    bundle = assemble_day_close_bundle(
        date_label="2026-04-25",
        episodes=episodes,
        events=events,
        config=BundleConfig(max_episodes=2, max_events=3),
    )
    assert len(bundle.bundle["episodes"]) == 1
    assert len(bundle.bundle["events"]) == 1
    assert len(bundle.bundle["sensitive_envelopes"]) == 3
    assert bundle.bundle["sensitive_envelopes_truncated"] is True
    assert bundle.citations == ["core.sessions:ep-1", "owntracks.points:pt-1"]
    oversized = assemble_day_close_bundle(
        date_label="2026-04-25",
        episodes=[_episode(privacy="sensitive") for _ in range(200)],
        events=[_event(privacy="sensitive") for _ in range(500)],
    )
    assert 0 < len(oversized.bundle["sensitive_envelopes"]) < 150
    assert oversized.bundle["sensitive_envelopes_truncated"] is True
    assert len(json.dumps(oversized.bundle)) <= BundleConfig().max_total_chars
    oversized.assert_within_budget(max_bytes=MAX_TIER_2_INPUT_BYTES)
    assert oversized.citations == []


def test_impossible_day_close_budget_refuses_instead_of_exceeding_the_bound() -> None:
    with pytest.raises(ValueError, match="configured input budget"):
        assemble_day_close_bundle(
            date_label="2026-04-25",
            episodes=[_episode(privacy="sensitive")],
            events=[],
            config=BundleConfig(max_total_chars=1),
        )


def _owner_day_fixture():
    """Synthetic local day: no owner data, geocoder, model or provider."""
    from zoneinfo import ZoneInfo

    day = datetime(2026, 10, 15, tzinfo=ZoneInfo("Asia/Singapore"))
    specs = [
        ("google_health.measurements", "sleep_episode", 1, 7, "Sleep", {}, "medium"),
        ("spotify.session_summary", "listening_episode", 0, 8, "Overnight playlist", {}, "medium"),
        (
            "owntracks.ssid_presence",
            "presence_episode",
            7,
            9,
            "At home",
            {"place": "home"},
            "medium",
        ),
        ("owntracks.points", "movement_episode", 9, 9.5, "Movement (71 points)", {}, "low"),
        (
            "owntracks.place_cluster",
            "place_episode",
            9.5,
            12,
            "Place: place_unknown",
            {"label": "place_unknown"},
            "low",
        ),
        (
            "comms.message_bursts",
            "social_episode",
            13,
            14,
            "Messages with Mail Sender",
            {"channel": "email", "participant_status": "resolved"},
            "medium",
        ),
        (
            "comms.message_bursts",
            "social_episode",
            17,
            17.1,
            "Messages via WhatsApp",
            {"channel": "whatsapp_user_client"},
            "low",
        ),
    ]
    episodes = []
    for index, (source, kind, start, end, title, payload, confidence) in enumerate(specs):
        row = _episode(
            source_name=source,
            source_ref=f"fixture:episode:{index}",
            title=title,
            start_at=day + timedelta(hours=start),
            payload=payload,
        )
        row.update(
            episode_type=kind,
            end_at=day + timedelta(hours=end),
            canonical_end_at=day + timedelta(hours=end),
            layer="activity",
            confidence=confidence,
        )
        episodes.append(row)
    for index in range(19):
        episodes.append(
            _episode(
                source_ref=f"fixture:automation:{index}",
                title="Conversation with Mail Sender",
                start_at=day + timedelta(hours=13),
                payload={"trigger_source": "route", "schema": "mail"},
            )
        )
    outgoing = _event(
        source_name="owner_outbound.messages",
        source_ref="fixture:outbound:1",
        occurred_at=day + timedelta(hours=17, minutes=2),
        title="Outbound message via WhatsApp",
        payload={"channel": "whatsapp_user_client"},
    )
    outgoing["event_type"] = "owner_outbound_message"
    return episodes, [outgoing]


async def test_synthetic_day_composes_supported_owner_activity_not_source_inventory() -> None:
    from butlers.chronicler.interpretation import TierTwoOutput, interpret

    episodes, events = _owner_day_fixture()
    bundle = assemble_day_close_bundle(
        date_label="2026-10-15", timezone="Asia/Singapore", episodes=episodes, events=events
    )
    observations = bundle.bundle["owner_day"]["observations"]
    kinds = {row["kind"] for row in observations}
    assert {"sleep", "place", "movement", "outbound_message", "playback"} <= kinds
    text = "\n".join(row["text"] for row in observations)
    assert "Conversation with Mail Sender" not in text
    assert "Mail Sender" not in text
    assert "71 points" not in text
    assert "background" in text and "unknown" in text
    assert any("home" in row["text"] for row in observations if row["kind"] == "place")
    assert bundle.bundle["diagnostics"]["automated_session_records"] == 19
    assert bundle.bundle["diagnostics"]["inbound_email_records"] == 1
    assert any(row.get("episode_count") == 19 for row in bundle.bundle["episodes"])
    assert bundle.bundle["owner_day"]["unknowns"]
    assert all("+08:00" in row["start_at"] for row in observations)
    observed_refs = {ref for row in observations for ref in row["source_refs"]}
    assert observed_refs <= set(bundle.citations)
    assert "fixture:episode:5" not in observed_refs  # Inbound email is not owner exchange.
    assert "fixture:outbound:1" in observed_refs  # Actual owner action remains expressible.
    assert next(row for row in observations if row["kind"] == "movement")["confidence"] == "low"

    calls = []

    async def synthetic_narrator(actual_bundle):
        calls.append(actual_bundle)
        # Exercise the deterministic narrative seed, not a fabricated model-quality verdict.
        return TierTwoOutput(
            path=actual_bundle.path,
            summary="\n".join(
                row["text"] for row in actual_bundle.bundle["owner_day"]["observations"]
            ),
        )

    result = await interpret(synthetic_narrator, bundle)
    assert calls == [bundle]
    assert result.summary == text
    assert observed_refs <= set(result.cited_source_refs)
    bundle.assert_within_budget()

    processing_only = assemble_day_close_bundle(
        date_label="2026-10-15",
        timezone="Asia/Singapore",
        episodes=[row for row in episodes if row["source_name"] == "core.sessions"] + [episodes[5]],
        events=[],
    )
    assert processing_only.bundle["episodes"]  # Real records remain diagnostic evidence.
    assert processing_only.bundle["owner_day"]["observations"] == []
    assert processing_only.bundle["owner_day"]["unknowns"]
    assert processing_only.bundle["diagnostics"] == {
        "automated_session_records": 19,
        "inbound_email_records": 1,
    }


def test_owner_day_projection_cannot_derive_private_payload_or_sleep() -> None:
    for privacy in ("sensitive", "restricted", "invalid"):
        episodes, events = _owner_day_fixture()
        private = episodes[0]
        private.update(privacy=privacy, title="PRIVATE SECRET", canonical_title="PRIVATE SECRET")
        bundle = assemble_day_close_bundle(
            date_label="2026-10-15",
            timezone="Asia/Singapore",
            episodes=[private, episodes[1]],
            events=[],
        )
        observations = bundle.bundle["owner_day"]["observations"]
        assert not any(row["kind"] == "sleep" for row in observations)
        assert "PRIVATE SECRET" not in json.dumps(bundle.bundle["owner_day"])
        playback = next(row for row in observations if row["kind"] == "playback")
        assert playback["sleep_overlap_seconds"] == 0
        assert "not established" in playback["text"]
        assert "fixture:episode:0" not in bundle.citations


@pytest.mark.parametrize(
    ("start", "end", "overlap"),
    [(1, 7, 21600), (7, 9, 3600), (8, 9, 0), (10, 11, 0)],
    ids=["overnight", "partial", "touching-boundary", "awake-window"],
)
def test_playback_sleep_overlap_is_background_not_attentive_listening(start, end, overlap) -> None:
    episodes, _ = _owner_day_fixture()
    sleep, playback = episodes[:2]
    midnight = playback["start_at"]
    sleep.update(start_at=midnight + timedelta(hours=start), end_at=midnight + timedelta(hours=end))
    bundle = assemble_day_close_bundle(
        date_label="2026-10-15", timezone="Asia/Singapore", episodes=[sleep, playback], events=[]
    )
    actual = next(
        row for row in bundle.bundle["owner_day"]["observations"] if row["kind"] == "playback"
    )
    assert actual["sleep_overlap_seconds"] == overlap
    assert ("background" in actual["text"]) == bool(overlap)
    assert "not established" in actual["text"] or "not confirmed" in actual["text"]


def test_owner_day_projection_shares_budget_and_never_invents_sources() -> None:
    episodes, events = _owner_day_fixture()
    bundle = assemble_day_close_bundle(
        date_label="2026-10-15",
        timezone="Asia/Singapore",
        episodes=episodes * 20,
        events=events * 20,
        config=BundleConfig(max_total_chars=1400),
    )
    assert len(json.dumps(bundle.bundle)) <= 1400
    bundle.assert_within_budget()
    observations = bundle.bundle["owner_day"]["observations"]
    assert len(observations) <= 12
    # Derived observations never turn an empty canonical capture into evidence.
    assert not observations or bundle.bundle["episodes"] or bundle.bundle["events"]
    assert {ref for row in observations for ref in row["source_refs"]} <= set(bundle.citations)
    zero = assemble_day_close_bundle(
        date_label="2026-10-15",
        episodes=episodes,
        events=events,
        config=BundleConfig(max_episodes=0, max_events=0),
    )
    assert zero.bundle["owner_day"]["observations"] == []
    assert zero.citations == []


def test_owner_day_clips_effective_windows_and_keeps_missing_evidence_unknown() -> None:
    episodes, events = _owner_day_fixture()
    sleep, playback, place = episodes[:3]
    midnight = playback["start_at"]
    sleep.update(start_at=midnight - timedelta(hours=1))
    duplicate = dict(sleep, source_ref="fixture:sleep-duplicate")
    place.update(
        start_at=midnight + timedelta(hours=9),
        end_at=None,
        canonical_start_at=midnight - timedelta(days=1),
        confidence="malformed",
    )
    # A malformed channel is unresolved, never a fabricated exchange or crash.
    events[0]["payload"] = {"channel": ["invalid"]}
    outside = dict(place, source_ref="fixture:outside", start_at=midnight + timedelta(days=1))
    invalid = dict(place, source_ref="fixture:naive", start_at=datetime(2026, 10, 15, 12))
    bundle = assemble_day_close_bundle(
        date_label="2026-10-15",
        timezone="Asia/Singapore",
        episodes=[sleep, duplicate, playback, place, outside, invalid],
        events=events,
    )
    observations = bundle.bundle["owner_day"]["observations"]
    actual_playback = next(row for row in observations if row["kind"] == "playback")
    assert actual_playback["sleep_overlap_seconds"] == 7 * 3600  # Union, not duplicate sum.
    assert {"fixture:episode:0", "fixture:sleep-duplicate"} <= set(actual_playback["source_refs"])
    actual_place = next(row for row in observations if row["kind"] == "place")
    assert actual_place["start_at"].startswith("2026-10-15T09:00")
    assert actual_place["confidence"] == "low" and "Duration is unknown" in actual_place["text"]
    assert "unresolved channel" in next(
        row["text"] for row in observations if row["kind"] == "outbound_message"
    )
    refs = {ref for row in observations for ref in row["source_refs"]}
    assert "fixture:outside" not in refs and "fixture:naive" not in refs
