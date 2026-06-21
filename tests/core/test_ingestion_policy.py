"""Unit tests for the unified IngestionPolicyEvaluator — condensed.

Covers:
- All 8 rule_type matchers (sender_domain, sender_address, header_condition,
  mime_type, substring, chat_id, channel_id, mic_id)
- First-match-wins evaluation order, all action types
- No-match returns pass_through
- Wildcard conditions, unknown rule types skipped
- Fail-open on DB error (retains stale cache)
- TTL-based background refresh scheduling, cache invalidation
- IngestionEnvelope and PolicyDecision dataclasses
- Scope-aware DB loading, invalid rows skipped
- Telemetry metrics integration: rule_matched, rule_pass_through, evaluation_latency_ms

Issue: bu-r55.3, bu-r55.4
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from opentelemetry import metrics as otel_metrics
from opentelemetry.metrics import _internal as _metrics_internal
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.util._once import Once

from butlers.ingestion_policy import (
    IngestionEnvelope,
    IngestionPolicyEvaluator,
    PolicyDecision,
    _match_channel_id,
    _match_chat_id,
    _match_header_condition,
    _match_mime_type,
    _match_sender_address,
    _match_sender_domain,
    _match_source_channel,
    _match_substring,
)
from butlers.ingestion_policy_metrics import (
    IngestionPolicyMetrics,
    _safe_action,
    _scope_type,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rule(
    *,
    id: str = "00000000-0000-0000-0000-000000000001",
    rule_type: str = "sender_domain",
    condition: dict | None = None,
    action: str = "pass_through",
    priority: int = 10,
    name: str | None = None,
    created_at: str = "2026-02-01T00:00:00Z",
) -> dict[str, Any]:
    return {
        "id": id,
        "rule_type": rule_type,
        "condition": condition or {},
        "action": action,
        "priority": priority,
        "name": name,
        "created_at": created_at,
    }


def _email_envelope(
    *,
    sender: str = "user@example.com",
    headers: dict[str, str] | None = None,
    mime_parts: list[str] | None = None,
    raw_key: str = "",
) -> IngestionEnvelope:
    return IngestionEnvelope(
        sender_address=sender,
        source_channel="email",
        headers=headers or {},
        mime_parts=mime_parts or [],
        raw_key=raw_key,
    )


def _telegram_envelope(*, chat_id: str = "12345") -> IngestionEnvelope:
    return IngestionEnvelope(
        sender_address="",
        source_channel="telegram_bot",
        raw_key=chat_id,
    )


def _discord_envelope(*, channel_id: str = "987654321098765432") -> IngestionEnvelope:
    return IngestionEnvelope(
        sender_address="",
        source_channel="discord",
        raw_key=channel_id,
    )


def _voice_envelope(*, mic_id: str = "kitchen") -> IngestionEnvelope:
    return IngestionEnvelope(source_channel="voice", raw_key=mic_id)


# ---------------------------------------------------------------------------
# Dataclass contracts
# ---------------------------------------------------------------------------


def test_ingestion_envelope_and_policy_decision_contracts() -> None:
    """IngestionEnvelope is frozen with correct defaults; PolicyDecision.bypasses_llm
    and .allowed correct."""
    env = IngestionEnvelope(sender_address="a@b.com", source_channel="email")
    with pytest.raises(AttributeError):
        env.sender_address = "c@d.com"  # type: ignore[misc]
    env2 = IngestionEnvelope()
    assert env2.sender_address == "" and env2.source_channel == ""
    assert env2.headers == {} and env2.mime_parts == [] and env2.raw_key == ""

    assert PolicyDecision(action="block").bypasses_llm is True
    assert PolicyDecision(action="pass_through").bypasses_llm is False
    assert PolicyDecision(action="skip").bypasses_llm is True
    assert PolicyDecision(action="pass_through").allowed is True
    assert PolicyDecision(action="block").allowed is False
    assert PolicyDecision(action="skip").allowed is True


# ---------------------------------------------------------------------------
# All matchers — all 8 rule types in one test
# ---------------------------------------------------------------------------


def test_all_matchers() -> None:
    """All 8 matcher functions: sender_domain, sender_address, header_condition, mime_type,
    substring, chat_id, channel_id, mic_id — match, no match, wildcard, edge cases."""
    # sender_domain: exact, suffix, case-insensitive, rfc2822, wildcard, no-suffix-mismatch
    assert _match_sender_domain(
        _email_envelope(sender="alerts@chase.com"), {"domain": "chase.com", "match": "exact"}
    )
    assert not _match_sender_domain(
        _email_envelope(sender="alerts@mail.chase.com"), {"domain": "chase.com", "match": "exact"}
    )
    assert _match_sender_domain(
        _email_envelope(sender="alerts@mail.chase.com"), {"domain": "chase.com", "match": "suffix"}
    )
    assert not _match_sender_domain(
        _email_envelope(sender="user@notchase.com"), {"domain": "chase.com", "match": "suffix"}
    )
    assert _match_sender_domain(
        _email_envelope(sender="ALERTS@CHASE.COM"), {"domain": "chase.com", "match": "exact"}
    )
    assert _match_sender_domain(
        _email_envelope(sender="GitHub <no-reply@github.com>"),
        {"domain": "github.com", "match": "exact"},
    )
    assert _match_sender_domain(
        _email_envelope(sender="anyone@anything.com"), {"domain": "*", "match": "any"}
    )

    # sender_address: exact, case-insensitive, wildcard, local_part_prefix, rfc2822, no-match
    assert _match_sender_address(
        _email_envelope(sender="alerts@chase.com"), {"address": "alerts@chase.com"}
    )
    assert not _match_sender_address(
        _email_envelope(sender="other@chase.com"), {"address": "alerts@chase.com"}
    )
    assert _match_sender_address(
        _email_envelope(sender="ALERTS@CHASE.COM"), {"address": "alerts@chase.com"}
    )
    assert _match_sender_address(_email_envelope(sender="anyone@anything.com"), {"address": "*"})
    assert _match_sender_address(
        _email_envelope(sender="noreply@grab.com"),
        {"address": "noreply", "match": "local_part_prefix"},
    )
    assert not _match_sender_address(
        _email_envelope(sender="hello@example.com"),
        {"address": "noreply", "match": "local_part_prefix"},
    )
    assert _match_sender_address(
        _email_envelope(sender="GitHub <no-reply@github.com>"), {"address": "no-reply@github.com"}
    )

    # header_condition: present, equals, contains, case-insensitive key, missing op→False
    assert _match_header_condition(
        _email_envelope(headers={"List-Unsubscribe": "<mailto:unsub@x.com>"}),
        {"header": "List-Unsubscribe", "op": "present"},
    )
    assert not _match_header_condition(
        _email_envelope(headers={}), {"header": "List-Unsubscribe", "op": "present"}
    )
    assert _match_header_condition(
        _email_envelope(headers={"List-Unsubscribe": "yes"}),
        {"header": "list-unsubscribe", "op": "present"},
    )
    assert _match_header_condition(
        _email_envelope(headers={"Precedence": "bulk"}),
        {"header": "Precedence", "op": "equals", "value": "bulk"},
    )
    assert _match_header_condition(
        _email_envelope(headers={"X-Spam-Status": "YES, score=8.0"}),
        {"header": "X-Spam-Status", "op": "contains", "value": "YES"},
    )
    assert not _match_header_condition(_email_envelope(headers={"Foo": "bar"}), {"header": "Foo"})

    # mime_type: exact, glob, no match, empty, case-insensitive
    assert _match_mime_type(
        _email_envelope(mime_parts=["text/plain", "text/calendar"]), {"type": "text/calendar"}
    )
    assert _match_mime_type(_email_envelope(mime_parts=["image/jpeg"]), {"type": "image/*"})
    assert not _match_mime_type(_email_envelope(mime_parts=["text/plain"]), {"type": "image/*"})
    assert not _match_mime_type(_email_envelope(mime_parts=[]), {"type": "text/calendar"})
    assert _match_mime_type(
        _email_envelope(mime_parts=["TEXT/CALENDAR"]), {"type": "text/calendar"}
    )

    # substring: match, no match, wildcard
    assert _match_substring(
        IngestionEnvelope(raw_key="Hello World from alice@EXAMPLE.COM"),
        {"pattern": "alice@example.com"},
    )
    assert not _match_substring(IngestionEnvelope(raw_key="hello world"), {"pattern": "foobar"})
    assert _match_substring(IngestionEnvelope(raw_key="anything"), {"pattern": "*"})

    # chat_id: match, no match, group, wildcard
    assert _match_chat_id(_telegram_envelope(chat_id="12345"), {"chat_id": "12345"})
    assert not _match_chat_id(_telegram_envelope(chat_id="12345"), {"chat_id": "99999"})
    assert _match_chat_id(_telegram_envelope(chat_id="-100987654321"), {"chat_id": "-100987654321"})
    assert _match_chat_id(_telegram_envelope(chat_id="12345"), {"chat_id": "*"})

    # channel_id: match, no match, wildcard
    assert _match_channel_id(
        _discord_envelope(channel_id="987654321098765432"), {"channel_id": "987654321098765432"}
    )
    assert not _match_channel_id(
        _discord_envelope(channel_id="987654321098765432"), {"channel_id": "111111111111111111"}
    )
    assert _match_channel_id(
        _discord_envelope(channel_id="987654321098765432"), {"channel_id": "*"}
    )

    # mic_id: in _KNOWN_RULE_TYPES, matches device name, no match on different device
    from butlers.ingestion_policy import _KNOWN_RULE_TYPES

    assert "mic_id" in _KNOWN_RULE_TYPES
    ev_mic = IngestionPolicyEvaluator(scope="connector:live-listener:mic:kitchen", db_pool=None)
    ev_mic._last_loaded_at = time.monotonic()
    ev_mic._rules = [
        _rule(
            id="id-mic",
            rule_type="mic_id",
            condition={"mic_id": "kitchen"},
            action="block",
            priority=1,
        )
    ]
    assert ev_mic.evaluate(_voice_envelope(mic_id="kitchen")).action == "block"
    assert ev_mic.evaluate(_voice_envelope(mic_id="bedroom")).action == "pass_through"

    # source_channel: match, no match, wildcard, empty-channel wildcard rejection
    assert "source_channel" in _KNOWN_RULE_TYPES
    owntracks_env = IngestionEnvelope(source_channel="owntracks")
    email_env = IngestionEnvelope(source_channel="email")
    empty_env = IngestionEnvelope(source_channel="")
    assert _match_source_channel(owntracks_env, {"source_channel": "owntracks"})
    assert not _match_source_channel(email_env, {"source_channel": "owntracks"})
    assert _match_source_channel(owntracks_env, {"source_channel": "*"})
    assert not _match_source_channel(empty_env, {"source_channel": "*"})
    assert not _match_source_channel(owntracks_env, {"source_channel": ""})


# ---------------------------------------------------------------------------
# Evaluator behavioral contract
# ---------------------------------------------------------------------------


def test_evaluate_behavioral_contracts() -> None:
    """No rules → pass_through; first-match-wins; all action types; unknown rule skipped;
    mixed types."""
    # No rules → pass_through
    ev = IngestionPolicyEvaluator(scope="global", db_pool=None)
    ev._last_loaded_at = time.monotonic()
    ev._rules = []
    d = ev.evaluate(_email_envelope())
    assert d.action == "pass_through" and d.matched_rule_id is None and not d.bypasses_llm

    # First-match-wins by priority
    ev2 = IngestionPolicyEvaluator(scope="global", db_pool=None)
    ev2._last_loaded_at = time.monotonic()
    ev2._rules = [
        _rule(
            id="id-a",
            rule_type="sender_domain",
            condition={"domain": "chase.com", "match": "exact"},
            action="skip",
            priority=5,
        ),
        _rule(
            id="id-b",
            rule_type="sender_domain",
            condition={"domain": "chase.com", "match": "suffix"},
            action="route_to:finance",
            priority=10,
        ),
    ]
    d2 = ev2.evaluate(_email_envelope(sender="alerts@chase.com"))
    assert d2.action == "skip" and d2.matched_rule_id == "id-a"

    # route_to parsed correctly
    ev3 = IngestionPolicyEvaluator(scope="global", db_pool=None)
    ev3._last_loaded_at = time.monotonic()
    ev3._rules = [
        _rule(
            rule_type="sender_domain",
            condition={"domain": "paypal.com", "match": "suffix"},
            action="route_to:finance",
        )
    ]
    d3 = ev3.evaluate(_email_envelope(sender="service@paypal.com"))
    assert d3.action == "route_to" and d3.target_butler == "finance" and d3.bypasses_llm

    # block, metadata_only, low_priority_queue actions
    ev4 = IngestionPolicyEvaluator(scope="global", db_pool=None)
    ev4._last_loaded_at = time.monotonic()
    ev4._rules = [
        _rule(
            rule_type="sender_domain",
            condition={"domain": "spam.com", "match": "exact"},
            action="block",
        )
    ]
    assert ev4.evaluate(_email_envelope(sender="bad@spam.com")).action == "block"

    ev5 = IngestionPolicyEvaluator(scope="global", db_pool=None)
    ev5._last_loaded_at = time.monotonic()
    ev5._rules = [
        _rule(
            id="r1",
            rule_type="header_condition",
            condition={"header": "List-Unsubscribe", "op": "present"},
            action="metadata_only",
            priority=1,
        ),
        _rule(
            id="r2",
            rule_type="header_condition",
            condition={"header": "Precedence", "op": "equals", "value": "bulk"},
            action="low_priority_queue",
            priority=2,
        ),
    ]
    assert (
        ev5.evaluate(_email_envelope(headers={"List-Unsubscribe": "yes"})).action == "metadata_only"
    )
    assert (
        ev5.evaluate(_email_envelope(headers={"Precedence": "bulk"})).action == "low_priority_queue"
    )

    # Unknown rule type skipped; matcher exception skips rule
    ev6 = IngestionPolicyEvaluator(scope="global", db_pool=None)
    ev6._last_loaded_at = time.monotonic()
    ev6._rules = [
        _rule(
            id="id-bad",
            rule_type="totally_unknown",
            condition={"foo": "bar"},
            action="skip",
            priority=1,
        ),
        _rule(
            id="id-good",
            rule_type="sender_domain",
            condition={"domain": "example.com", "match": "exact"},
            action="route_to:general",
            priority=2,
        ),
    ]
    assert ev6.evaluate(_email_envelope(sender="user@example.com")).matched_rule_id == "id-good"

    # Mixed rule types: header > domain > mime type ordering
    ev7 = IngestionPolicyEvaluator(scope="global", db_pool=None)
    ev7._last_loaded_at = time.monotonic()
    ev7._rules = [
        _rule(
            id="r1",
            rule_type="header_condition",
            condition={"header": "X-Priority", "op": "equals", "value": "1"},
            action="skip",
            priority=1,
        ),
        _rule(
            id="r2",
            rule_type="sender_domain",
            condition={"domain": "bank.com", "match": "exact"},
            action="route_to:finance",
            priority=5,
        ),
        _rule(
            id="r3",
            rule_type="mime_type",
            condition={"type": "text/calendar"},
            action="route_to:calendar",
            priority=10,
        ),
    ]
    assert (
        ev7.evaluate(
            _email_envelope(
                sender="x@bank.com", headers={"X-Priority": "1"}, mime_parts=["text/calendar"]
            )
        ).matched_rule_id
        == "r1"
    )
    assert (
        ev7.evaluate(
            _email_envelope(sender="alerts@bank.com", mime_parts=["text/calendar"])
        ).matched_rule_id
        == "r2"
    )
    assert (
        ev7.evaluate(
            _email_envelope(sender="user@other.com", mime_parts=["text/calendar"])
        ).matched_rule_id
        == "r3"
    )
    assert (
        ev7.evaluate(_email_envelope(sender="user@other.com", mime_parts=["text/plain"])).action
        == "pass_through"
    )


# ---------------------------------------------------------------------------
# DB loading, TTL, and cache invalidation
# ---------------------------------------------------------------------------


async def test_evaluator_db_loading_ttl_and_invalidation() -> None:
    """ensure_loaded: calls DB once; idempotent; no-pool → empty; DB error retains stale cache;
    invalid rows skipped; stale triggers refresh; stacking blocked; invalidate resets timestamp."""
    # ensure_loaded calls DB once, scope passed correctly
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=[])
    ev = IngestionPolicyEvaluator(scope="global", db_pool=mock_pool)
    await ev.ensure_loaded()
    await ev.ensure_loaded()
    assert mock_pool.fetch.call_count == 1
    assert mock_pool.fetch.call_args[0][1] == "global"

    # No pool: loaded with empty rules
    ev2 = IngestionPolicyEvaluator(scope="global", db_pool=None)
    await ev2.ensure_loaded()
    assert ev2._last_loaded_at is not None and ev2._rules == []

    # DB error retains stale cache
    stale_rules = [
        _rule(
            rule_type="sender_domain",
            condition={"domain": "stale.com", "match": "exact"},
            action="skip",
        )
    ]
    mock_pool2 = AsyncMock()
    ev3 = IngestionPolicyEvaluator(scope="global", db_pool=mock_pool2)
    ev3._rules = stale_rules
    ev3._last_loaded_at = 0.0
    mock_pool2.fetch = AsyncMock(side_effect=Exception("connection refused"))
    await ev3._load_rules()
    assert ev3._rules == stale_rules and ev3._last_loaded_at > 0.0

    # Invalid rows skipped during load
    mock_pool3 = AsyncMock()
    mock_pool3.fetch = AsyncMock(
        return_value=[
            {
                "id": "valid-1",
                "rule_type": "sender_domain",
                "condition": {"domain": "good.com", "match": "exact"},
                "action": "skip",
                "priority": 1,
                "name": None,
                "created_at": "2026-01-01T00:00:00Z",
            },
            {
                "id": "invalid-1",
                "rule_type": "sender_domain",
                "condition": "not-a-dict",
                "action": "skip",
                "priority": 2,
                "name": None,
                "created_at": "2026-01-01T00:00:00Z",
            },
            {
                "id": "invalid-2",
                "rule_type": "totally_bogus",
                "condition": {"foo": "bar"},
                "action": "skip",
                "priority": 3,
                "name": None,
                "created_at": "2026-01-01T00:00:00Z",
            },
        ]
    )
    ev4 = IngestionPolicyEvaluator(scope="global", db_pool=mock_pool3)
    await ev4._load_rules()
    assert len(ev4._rules) == 1 and ev4._rules[0]["id"] == "valid-1"

    # TTL: no refresh when fresh
    def _consume_coro(coro):
        coro.close()
        t = MagicMock()
        t.done.return_value = False
        return t

    ev5 = IngestionPolicyEvaluator(scope="global", db_pool=None, refresh_interval_s=60)
    ev5._last_loaded_at = time.monotonic()
    ev5._rules = []
    with patch.object(asyncio, "create_task") as mock_ct:
        ev5.evaluate(_email_envelope())
        mock_ct.assert_not_called()

    # TTL: refresh scheduled when stale
    mock_pool4 = MagicMock()
    ev6 = IngestionPolicyEvaluator(scope="global", db_pool=mock_pool4, refresh_interval_s=60)
    ev6._last_loaded_at = time.monotonic() - 120
    ev6._rules = []
    with patch.object(asyncio, "create_task", side_effect=_consume_coro) as mock_ct2:
        ev6.evaluate(_email_envelope())
        mock_ct2.assert_called_once()

    # No stacking: existing running task → no new task
    ev7 = IngestionPolicyEvaluator(scope="global", db_pool=mock_pool4, refresh_interval_s=60)
    ev7._last_loaded_at = time.monotonic() - 120
    mock_task = MagicMock()
    mock_task.done.return_value = False
    ev7._background_refresh_task = mock_task
    with patch.object(asyncio, "create_task") as mock_ct3:
        ev7.evaluate(_email_envelope())
        mock_ct3.assert_not_called()

    # invalidate resets timestamp and triggers next evaluate
    ev8 = IngestionPolicyEvaluator(scope="global", db_pool=mock_pool4, refresh_interval_s=60)
    ev8._last_loaded_at = time.monotonic()
    ev8._rules = []
    ev8.invalidate()
    assert ev8._last_loaded_at == 0.0
    with patch.object(asyncio, "create_task", side_effect=_consume_coro) as mock_ct4:
        ev8.evaluate(_email_envelope())
        mock_ct4.assert_called_once()


# ---------------------------------------------------------------------------
# Telemetry helpers and metrics integration
# ---------------------------------------------------------------------------


def _reset_metrics_global_state() -> None:
    _metrics_internal._METER_PROVIDER_SET_ONCE = Once()
    _metrics_internal._METER_PROVIDER = None


def _make_in_memory_provider() -> tuple[MeterProvider, InMemoryMetricReader]:
    _reset_metrics_global_state()
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    otel_metrics.set_meter_provider(provider)
    return provider, reader


def _collect_metrics(reader: InMemoryMetricReader) -> dict[str, Any]:
    result: dict[str, Any] = {}
    data = reader.get_metrics_data()
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.data.data_points:
                    result[metric.name] = metric.data.data_points
    return result


def test_metrics_helpers_and_telemetry_integration() -> None:
    """_scope_type/_safe_action cardinality helpers; IngestionPolicyMetrics records
    correctly; evaluator wires metrics."""
    # Cardinality helpers
    assert _scope_type("global") == "global"
    assert _scope_type("connector:gmail:gmail:user:dev") == "connector:gmail"
    assert _scope_type("connector:telegram-bot:telegram-bot:my-bot") == "connector:telegram-bot"
    assert _scope_type("something_else") == "something_else"
    assert _safe_action("route_to:finance") == "route_to"
    assert _safe_action("skip") == "skip"
    assert _safe_action("block") == "block"

    # IngestionPolicyMetrics records match/pass_through with correct attributes
    _provider, reader = _make_in_memory_provider()
    try:
        m = IngestionPolicyMetrics(scope="global")
        m.record_match(
            rule_type="sender_domain", action="skip", source_channel="email", latency_ms=0.5
        )
        m.record_match(
            rule_type="sender_domain",
            action="route_to:finance",
            source_channel="email",
            latency_ms=0.3,
        )
        m.record_pass_through(source_channel="email", reason="no rule matched", latency_ms=0.2)

        data = _collect_metrics(reader)
        route_dps = [
            dp
            for dp in data["butlers.ingestion.rule_matched"]
            if dp.attributes.get("action") == "route_to"
        ]
        skip_dps = [
            dp
            for dp in data["butlers.ingestion.rule_matched"]
            if dp.attributes.get("action") == "skip"
        ]
        assert len(route_dps) == 1 and len(skip_dps) == 1
        assert all(
            dp.attributes["scope_type"] == "global" for dp in data["butlers.ingestion.rule_matched"]
        )
        dp_pt = data["butlers.ingestion.rule_pass_through"][0]
        assert dp_pt.value == 1 and dp_pt.attributes["reason"] == "no rule matched"
        assert "butlers.ingestion.evaluation_latency_ms" in data

        # Evaluator end-to-end: match and pass_through both recorded
        _reset_metrics_global_state()
        reader2 = InMemoryMetricReader()
        provider2 = MeterProvider(metric_readers=[reader2])
        otel_metrics.set_meter_provider(provider2)

        ev = IngestionPolicyEvaluator(scope="global", db_pool=None)
        ev._last_loaded_at = time.monotonic()
        ev._rules = [
            _rule(
                rule_type="sender_domain",
                condition={"domain": "example.com", "match": "exact"},
                action="skip",
            )
        ]
        ev.evaluate(_email_envelope(sender="user@example.com"))
        ev.evaluate(_email_envelope(sender="user@other.com"))
        ev.evaluate(_email_envelope(sender="admin@example.com"))

        data2 = _collect_metrics(reader2)
        assert data2["butlers.ingestion.rule_matched"][0].value == 2
        assert data2["butlers.ingestion.rule_pass_through"][0].value == 1
    finally:
        _reset_metrics_global_state()
