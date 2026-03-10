"""Unit tests for the unified IngestionPolicyEvaluator.

Covers:
- All 7 rule_type matchers (sender_domain, sender_address, header_condition,
  mime_type, substring, chat_id, channel_id)
- First-match-wins evaluation order
- No-match returns pass_through
- Catch-all wildcard conditions (from whitelist migration)
- Fail-open on DB error (retains stale cache)
- TTL-based background refresh scheduling
- Cache invalidation
- IngestionEnvelope and PolicyDecision dataclasses
- Scope-aware loading
- Telemetry metrics integration (D11): rule_matched, rule_pass_through,
  evaluation_latency_ms

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
        source_channel="telegram",
        raw_key=chat_id,
    )


def _discord_envelope(*, channel_id: str = "987654321098765432") -> IngestionEnvelope:
    return IngestionEnvelope(
        sender_address="",
        source_channel="discord",
        raw_key=channel_id,
    )


# ---------------------------------------------------------------------------
# IngestionEnvelope
# ---------------------------------------------------------------------------


class TestIngestionEnvelope:
    def test_frozen_dataclass(self) -> None:
        env = IngestionEnvelope(sender_address="a@b.com", source_channel="email")
        with pytest.raises(AttributeError):
            env.sender_address = "c@d.com"  # type: ignore[misc]

    def test_defaults(self) -> None:
        env = IngestionEnvelope()
        assert env.sender_address == ""
        assert env.source_channel == ""
        assert env.headers == {}
        assert env.mime_parts == []
        assert env.thread_id is None
        assert env.raw_key == ""


# ---------------------------------------------------------------------------
# PolicyDecision
# ---------------------------------------------------------------------------


class TestPolicyDecision:
    def test_bypasses_llm_for_block(self) -> None:
        d = PolicyDecision(action="block")
        assert d.bypasses_llm is True

    def test_bypasses_llm_false_for_pass_through(self) -> None:
        d = PolicyDecision(action="pass_through")
        assert d.bypasses_llm is False

    def test_bypasses_llm_for_skip(self) -> None:
        d = PolicyDecision(action="skip")
        assert d.bypasses_llm is True

    def test_allowed_true_for_pass_through(self) -> None:
        d = PolicyDecision(action="pass_through")
        assert d.allowed is True

    def test_allowed_false_for_block(self) -> None:
        d = PolicyDecision(action="block")
        assert d.allowed is False

    def test_allowed_true_for_skip(self) -> None:
        d = PolicyDecision(action="skip")
        assert d.allowed is True


# ---------------------------------------------------------------------------
# sender_domain matcher
# ---------------------------------------------------------------------------


class TestMatchSenderDomain:
    def test_exact_match(self) -> None:
        env = _email_envelope(sender="alerts@chase.com")
        assert _match_sender_domain(env, {"domain": "chase.com", "match": "exact"})

    def test_exact_no_match_subdomain(self) -> None:
        env = _email_envelope(sender="alerts@mail.chase.com")
        assert not _match_sender_domain(env, {"domain": "chase.com", "match": "exact"})

    def test_suffix_matches_exact(self) -> None:
        env = _email_envelope(sender="alerts@chase.com")
        assert _match_sender_domain(env, {"domain": "chase.com", "match": "suffix"})

    def test_suffix_matches_subdomain(self) -> None:
        env = _email_envelope(sender="alerts@mail.chase.com")
        assert _match_sender_domain(env, {"domain": "chase.com", "match": "suffix"})

    def test_suffix_no_false_positive(self) -> None:
        env = _email_envelope(sender="user@notchase.com")
        assert not _match_sender_domain(env, {"domain": "chase.com", "match": "suffix"})

    def test_case_insensitive(self) -> None:
        env = _email_envelope(sender="Alerts@CHASE.COM")
        assert _match_sender_domain(env, {"domain": "chase.com", "match": "exact"})

    def test_empty_domain_no_match(self) -> None:
        env = _email_envelope(sender="alerts@chase.com")
        assert not _match_sender_domain(env, {"domain": "", "match": "exact"})

    def test_catchall_any_match(self) -> None:
        env = _email_envelope(sender="anyone@anything.com")
        assert _match_sender_domain(env, {"domain": "*", "match": "any"})

    def test_catchall_wildcard_domain(self) -> None:
        env = _email_envelope(sender="anyone@anything.com")
        assert _match_sender_domain(env, {"domain": "*", "match": "exact"})


# ---------------------------------------------------------------------------
# sender_address matcher
# ---------------------------------------------------------------------------


class TestMatchSenderAddress:
    def test_exact_match(self) -> None:
        env = _email_envelope(sender="alerts@chase.com")
        assert _match_sender_address(env, {"address": "alerts@chase.com"})

    def test_case_insensitive(self) -> None:
        env = _email_envelope(sender="ALERTS@CHASE.COM")
        assert _match_sender_address(env, {"address": "alerts@chase.com"})

    def test_no_match(self) -> None:
        env = _email_envelope(sender="other@chase.com")
        assert not _match_sender_address(env, {"address": "alerts@chase.com"})

    def test_empty_target_no_match(self) -> None:
        env = _email_envelope(sender="alerts@chase.com")
        assert not _match_sender_address(env, {"address": ""})

    def test_catchall_wildcard(self) -> None:
        env = _email_envelope(sender="anyone@anything.com")
        assert _match_sender_address(env, {"address": "*"})

    def test_local_part_prefix_noreply(self) -> None:
        env = _email_envelope(sender="noreply@grab.com")
        assert _match_sender_address(env, {"address": "noreply", "match": "local_part_prefix"})

    def test_local_part_prefix_no_reply_hyphen(self) -> None:
        env = _email_envelope(sender="no-reply@uber.com")
        assert _match_sender_address(env, {"address": "no-reply", "match": "local_part_prefix"})

    def test_local_part_prefix_case_insensitive(self) -> None:
        env = _email_envelope(sender="NoReply@Example.com")
        assert _match_sender_address(env, {"address": "noreply", "match": "local_part_prefix"})

    def test_local_part_prefix_no_match(self) -> None:
        env = _email_envelope(sender="hello@example.com")
        assert not _match_sender_address(env, {"address": "noreply", "match": "local_part_prefix"})

    def test_local_part_prefix_partial(self) -> None:
        """noreply-abc@x.com should match prefix 'noreply'."""
        env = _email_envelope(sender="noreply-abc@x.com")
        assert _match_sender_address(env, {"address": "noreply", "match": "local_part_prefix"})


# ---------------------------------------------------------------------------
# header_condition matcher
# ---------------------------------------------------------------------------


class TestMatchHeaderCondition:
    def test_present_op_matches(self) -> None:
        env = _email_envelope(headers={"List-Unsubscribe": "<mailto:unsub@x.com>"})
        assert _match_header_condition(env, {"header": "List-Unsubscribe", "op": "present"})

    def test_present_op_no_match(self) -> None:
        env = _email_envelope(headers={})
        assert not _match_header_condition(env, {"header": "List-Unsubscribe", "op": "present"})

    def test_header_key_case_insensitive(self) -> None:
        env = _email_envelope(headers={"List-Unsubscribe": "yes"})
        assert _match_header_condition(env, {"header": "list-unsubscribe", "op": "present"})

    def test_equals_op(self) -> None:
        env = _email_envelope(headers={"Precedence": "bulk"})
        assert _match_header_condition(
            env, {"header": "Precedence", "op": "equals", "value": "bulk"}
        )

    def test_equals_op_no_match(self) -> None:
        env = _email_envelope(headers={"Precedence": "list"})
        assert not _match_header_condition(
            env, {"header": "Precedence", "op": "equals", "value": "bulk"}
        )

    def test_equals_with_whitespace_trimming(self) -> None:
        env = _email_envelope(headers={"Precedence": " bulk "})
        assert _match_header_condition(
            env, {"header": "Precedence", "op": "equals", "value": "bulk"}
        )

    def test_contains_op(self) -> None:
        env = _email_envelope(headers={"X-Spam-Status": "YES, score=8.0"})
        assert _match_header_condition(
            env, {"header": "X-Spam-Status", "op": "contains", "value": "YES"}
        )

    def test_contains_op_no_match(self) -> None:
        env = _email_envelope(headers={"X-Spam-Status": "NO, score=1.0"})
        assert not _match_header_condition(
            env, {"header": "X-Spam-Status", "op": "contains", "value": "YES"}
        )

    def test_missing_op_no_match(self) -> None:
        env = _email_envelope(headers={"Foo": "bar"})
        assert not _match_header_condition(env, {"header": "Foo"})

    def test_equals_with_none_value(self) -> None:
        env = _email_envelope(headers={"Foo": "bar"})
        assert not _match_header_condition(env, {"header": "Foo", "op": "equals", "value": None})


# ---------------------------------------------------------------------------
# mime_type matcher
# ---------------------------------------------------------------------------


class TestMatchMimeType:
    def test_exact_match(self) -> None:
        env = _email_envelope(mime_parts=["text/plain", "text/calendar"])
        assert _match_mime_type(env, {"type": "text/calendar"})

    def test_wildcard_subtype(self) -> None:
        env = _email_envelope(mime_parts=["image/jpeg"])
        assert _match_mime_type(env, {"type": "image/*"})

    def test_wildcard_no_match_different_type(self) -> None:
        env = _email_envelope(mime_parts=["text/plain"])
        assert not _match_mime_type(env, {"type": "image/*"})

    def test_no_match_empty_parts(self) -> None:
        env = _email_envelope(mime_parts=[])
        assert not _match_mime_type(env, {"type": "text/calendar"})

    def test_case_insensitive(self) -> None:
        env = _email_envelope(mime_parts=["TEXT/CALENDAR"])
        assert _match_mime_type(env, {"type": "text/calendar"})

    def test_empty_pattern_no_match(self) -> None:
        env = _email_envelope(mime_parts=["text/plain"])
        assert not _match_mime_type(env, {"type": ""})


# ---------------------------------------------------------------------------
# substring matcher
# ---------------------------------------------------------------------------


class TestMatchSubstring:
    def test_case_insensitive_match(self) -> None:
        env = IngestionEnvelope(raw_key="Hello World from alice@EXAMPLE.COM")
        assert _match_substring(env, {"pattern": "alice@example.com"})

    def test_no_match(self) -> None:
        env = IngestionEnvelope(raw_key="hello world")
        assert not _match_substring(env, {"pattern": "foobar"})

    def test_empty_pattern_no_match(self) -> None:
        env = IngestionEnvelope(raw_key="hello")
        assert not _match_substring(env, {"pattern": ""})

    def test_catchall_wildcard(self) -> None:
        env = IngestionEnvelope(raw_key="anything")
        assert _match_substring(env, {"pattern": "*"})


# ---------------------------------------------------------------------------
# chat_id matcher
# ---------------------------------------------------------------------------


class TestMatchChatId:
    def test_exact_match(self) -> None:
        env = _telegram_envelope(chat_id="12345")
        assert _match_chat_id(env, {"chat_id": "12345"})

    def test_no_match(self) -> None:
        env = _telegram_envelope(chat_id="12345")
        assert not _match_chat_id(env, {"chat_id": "99999"})

    def test_negative_chat_id(self) -> None:
        env = _telegram_envelope(chat_id="-100987654321")
        assert _match_chat_id(env, {"chat_id": "-100987654321"})

    def test_empty_target_no_match(self) -> None:
        env = _telegram_envelope(chat_id="12345")
        assert not _match_chat_id(env, {"chat_id": ""})

    def test_catchall_wildcard(self) -> None:
        env = _telegram_envelope(chat_id="12345")
        assert _match_chat_id(env, {"chat_id": "*"})


# ---------------------------------------------------------------------------
# channel_id matcher
# ---------------------------------------------------------------------------


class TestMatchChannelId:
    def test_exact_match(self) -> None:
        env = _discord_envelope(channel_id="987654321098765432")
        assert _match_channel_id(env, {"channel_id": "987654321098765432"})

    def test_no_match(self) -> None:
        env = _discord_envelope(channel_id="987654321098765432")
        assert not _match_channel_id(env, {"channel_id": "111111111111111111"})

    def test_empty_target_no_match(self) -> None:
        env = _discord_envelope(channel_id="987654321098765432")
        assert not _match_channel_id(env, {"channel_id": ""})

    def test_catchall_wildcard(self) -> None:
        env = _discord_envelope(channel_id="987654321098765432")
        assert _match_channel_id(env, {"channel_id": "*"})


# ---------------------------------------------------------------------------
# IngestionPolicyEvaluator.evaluate() — no rules
# ---------------------------------------------------------------------------


class TestEvaluateNoRules:
    def test_no_rules_returns_pass_through(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        # Simulate loaded (empty rules)
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = []

        decision = evaluator.evaluate(_email_envelope())
        assert decision.action == "pass_through"
        assert decision.matched_rule_id is None
        assert decision.matched_rule_type is None

    def test_pass_through_bypasses_llm_is_false(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()
        decision = evaluator.evaluate(_email_envelope())
        assert decision.bypasses_llm is False

    def test_pass_through_allowed_is_true(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()
        decision = evaluator.evaluate(_email_envelope())
        assert decision.allowed is True


# ---------------------------------------------------------------------------
# IngestionPolicyEvaluator.evaluate() — first-match-wins
# ---------------------------------------------------------------------------


class TestEvaluateFirstMatchWins:
    def test_lower_priority_rule_wins(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
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
        decision = evaluator.evaluate(_email_envelope(sender="alerts@chase.com"))
        assert decision.action == "skip"
        assert decision.matched_rule_id == "id-a"

    def test_first_matching_rule_wins(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                id="id-first",
                rule_type="sender_domain",
                condition={"domain": "example.com", "match": "exact"},
                action="metadata_only",
                priority=1,
            ),
            _rule(
                id="id-second",
                rule_type="sender_domain",
                condition={"domain": "example.com", "match": "exact"},
                action="route_to:general",
                priority=2,
            ),
        ]
        decision = evaluator.evaluate(_email_envelope(sender="user@example.com"))
        assert decision.matched_rule_id == "id-first"
        assert decision.action == "metadata_only"

    def test_non_matching_rule_skipped(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                id="id-a",
                rule_type="sender_address",
                condition={"address": "specific@example.com"},
                action="skip",
                priority=1,
            ),
            _rule(
                id="id-b",
                rule_type="sender_domain",
                condition={"domain": "example.com", "match": "exact"},
                action="route_to:general",
                priority=2,
            ),
        ]
        decision = evaluator.evaluate(_email_envelope(sender="other@example.com"))
        assert decision.matched_rule_id == "id-b"
        assert decision.action == "route_to"
        assert decision.target_butler == "general"


# ---------------------------------------------------------------------------
# IngestionPolicyEvaluator.evaluate() — all action types
# ---------------------------------------------------------------------------


class TestEvaluateActionTypes:
    def test_route_to_parses_target(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                rule_type="sender_domain",
                condition={"domain": "paypal.com", "match": "suffix"},
                action="route_to:finance",
            )
        ]
        decision = evaluator.evaluate(_email_envelope(sender="service@paypal.com"))
        assert decision.action == "route_to"
        assert decision.target_butler == "finance"
        assert decision.bypasses_llm is True

    def test_block_action(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="connector:gmail:gmail:user:dev", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                rule_type="sender_domain",
                condition={"domain": "spam.com", "match": "exact"},
                action="block",
            )
        ]
        decision = evaluator.evaluate(_email_envelope(sender="bad@spam.com"))
        assert decision.action == "block"
        assert decision.allowed is False
        assert decision.bypasses_llm is True

    def test_skip_action(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                rule_type="header_condition",
                condition={"header": "Auto-Submitted", "op": "equals", "value": "auto-generated"},
                action="skip",
            )
        ]
        decision = evaluator.evaluate(_email_envelope(headers={"Auto-Submitted": "auto-generated"}))
        assert decision.action == "skip"
        assert decision.bypasses_llm is True

    def test_metadata_only_action(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                rule_type="header_condition",
                condition={"header": "List-Unsubscribe", "op": "present"},
                action="metadata_only",
            )
        ]
        decision = evaluator.evaluate(_email_envelope(headers={"List-Unsubscribe": "yes"}))
        assert decision.action == "metadata_only"

    def test_low_priority_queue_action(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                rule_type="header_condition",
                condition={"header": "Precedence", "op": "equals", "value": "bulk"},
                action="low_priority_queue",
            )
        ]
        decision = evaluator.evaluate(_email_envelope(headers={"Precedence": "bulk"}))
        assert decision.action == "low_priority_queue"

    def test_explicit_pass_through_action(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                rule_type="sender_address",
                condition={"address": "vip@example.com"},
                action="pass_through",
            )
        ]
        decision = evaluator.evaluate(_email_envelope(sender="vip@example.com"))
        assert decision.action == "pass_through"
        assert decision.bypasses_llm is False


# ---------------------------------------------------------------------------
# IngestionPolicyEvaluator.evaluate() — unknown rule_type handling
# ---------------------------------------------------------------------------


class TestEvaluateUnknownRuleType:
    def test_unknown_rule_type_skipped(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
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
        decision = evaluator.evaluate(_email_envelope(sender="user@example.com"))
        assert decision.matched_rule_id == "id-good"
        assert decision.action == "route_to"


# ---------------------------------------------------------------------------
# IngestionPolicyEvaluator.evaluate() — error handling in rule evaluation
# ---------------------------------------------------------------------------


class TestEvaluateErrorHandling:
    def test_exception_in_matcher_skips_rule(self) -> None:
        """If a matcher raises, the rule is skipped and evaluation continues."""
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                id="id-bad",
                rule_type="sender_domain",
                condition=None,  # type: ignore[arg-type]
                action="skip",
                priority=1,
            ),
            _rule(
                id="id-good",
                rule_type="sender_address",
                condition={"address": "user@example.com"},
                action="route_to:general",
                priority=2,
            ),
        ]
        # The first rule has condition=None which becomes {}, and should not crash
        decision = evaluator.evaluate(_email_envelope(sender="user@example.com"))
        assert decision.matched_rule_id == "id-good"


# ---------------------------------------------------------------------------
# IngestionPolicyEvaluator.evaluate() — connector-scoped evaluation
# ---------------------------------------------------------------------------


class TestConnectorScopedEvaluation:
    def test_connector_block_rule(self) -> None:
        evaluator = IngestionPolicyEvaluator(
            scope="connector:gmail:gmail:user:alice@example.com", db_pool=None
        )
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                rule_type="sender_domain",
                condition={"domain": "spam.com", "match": "suffix"},
                action="block",
            )
        ]
        decision = evaluator.evaluate(_email_envelope(sender="spammer@spam.com"))
        assert decision.action == "block"
        assert decision.allowed is False

    def test_connector_pass_through_allows(self) -> None:
        evaluator = IngestionPolicyEvaluator(
            scope="connector:gmail:gmail:user:alice@example.com", db_pool=None
        )
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                rule_type="sender_address",
                condition={"address": "vip@example.com"},
                action="pass_through",
                priority=1,
            ),
            _rule(
                rule_type="sender_domain",
                condition={"domain": "*", "match": "any"},
                action="block",
                priority=1000,
            ),
        ]
        # VIP passes through
        decision = evaluator.evaluate(_email_envelope(sender="vip@example.com"))
        assert decision.action == "pass_through"
        assert decision.allowed is True

    def test_connector_catchall_block(self) -> None:
        evaluator = IngestionPolicyEvaluator(
            scope="connector:gmail:gmail:user:alice@example.com", db_pool=None
        )
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                rule_type="sender_address",
                condition={"address": "vip@example.com"},
                action="pass_through",
                priority=1,
            ),
            _rule(
                rule_type="sender_domain",
                condition={"domain": "*", "match": "any"},
                action="block",
                priority=1000,
            ),
        ]
        # Non-VIP hits catch-all block
        decision = evaluator.evaluate(_email_envelope(sender="random@other.com"))
        assert decision.action == "block"
        assert decision.allowed is False

    def test_telegram_chat_id_block(self) -> None:
        evaluator = IngestionPolicyEvaluator(
            scope="connector:telegram-bot:telegram-bot:my-bot", db_pool=None
        )
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                rule_type="chat_id",
                condition={"chat_id": "-100987654321"},
                action="block",
            )
        ]
        decision = evaluator.evaluate(_telegram_envelope(chat_id="-100987654321"))
        assert decision.action == "block"
        assert decision.allowed is False

    def test_discord_channel_id_block(self) -> None:
        evaluator = IngestionPolicyEvaluator(
            scope="connector:discord:discord:my-server", db_pool=None
        )
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                rule_type="channel_id",
                condition={"channel_id": "987654321098765432"},
                action="block",
            )
        ]
        decision = evaluator.evaluate(_discord_envelope(channel_id="987654321098765432"))
        assert decision.action == "block"
        assert decision.allowed is False


# ---------------------------------------------------------------------------
# IngestionPolicyEvaluator — DB loading
# ---------------------------------------------------------------------------


class TestEvaluatorDBLoading:
    async def test_ensure_loaded_calls_load_rules(self) -> None:
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[])

        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=mock_pool)
        await evaluator.ensure_loaded()

        mock_pool.fetch.assert_called_once()
        # Verify the query includes scope filter
        call_args = mock_pool.fetch.call_args
        assert "scope = $1" in call_args[0][0]
        assert call_args[0][1] == "global"

    async def test_ensure_loaded_idempotent(self) -> None:
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[])

        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=mock_pool)
        await evaluator.ensure_loaded()
        await evaluator.ensure_loaded()

        # Only one DB call despite two ensure_loaded() calls
        assert mock_pool.fetch.call_count == 1

    async def test_no_pool_skips_load(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        await evaluator.ensure_loaded()
        assert evaluator._last_loaded_at is not None
        assert evaluator._rules == []

    async def test_db_error_retains_stale_cache(self) -> None:
        mock_pool = AsyncMock()
        stale_rules = [
            _rule(
                rule_type="sender_domain",
                condition={"domain": "stale.com", "match": "exact"},
                action="skip",
            )
        ]

        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=mock_pool)
        evaluator._rules = stale_rules
        evaluator._last_loaded_at = 0.0  # force stale

        # Make DB fetch raise
        mock_pool.fetch = AsyncMock(side_effect=Exception("connection refused"))

        await evaluator._load_rules()

        # Stale rules preserved
        assert evaluator._rules == stale_rules
        # Timestamp updated to prevent hammering
        assert evaluator._last_loaded_at is not None
        assert evaluator._last_loaded_at > 0.0

    async def test_invalid_rows_skipped_during_load(self) -> None:
        """Rules with non-dict condition or unknown rule_type are skipped."""
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(
            return_value=[
                # Valid rule
                {
                    "id": "valid-1",
                    "rule_type": "sender_domain",
                    "condition": {"domain": "good.com", "match": "exact"},
                    "action": "skip",
                    "priority": 1,
                    "name": None,
                    "created_at": "2026-01-01T00:00:00Z",
                },
                # Invalid: bad condition type
                {
                    "id": "invalid-1",
                    "rule_type": "sender_domain",
                    "condition": "not-a-dict",
                    "action": "skip",
                    "priority": 2,
                    "name": None,
                    "created_at": "2026-01-01T00:00:00Z",
                },
                # Invalid: unknown rule_type
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

        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=mock_pool)
        await evaluator._load_rules()

        # Only the valid rule should be loaded
        assert len(evaluator._rules) == 1
        assert evaluator._rules[0]["id"] == "valid-1"


# ---------------------------------------------------------------------------
# IngestionPolicyEvaluator — TTL refresh
# ---------------------------------------------------------------------------


class TestEvaluatorTTLRefresh:
    def test_no_refresh_when_fresh(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None, refresh_interval_s=60)
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = []

        with patch.object(asyncio, "create_task") as mock_create_task:
            evaluator.evaluate(_email_envelope())
            mock_create_task.assert_not_called()

    def test_refresh_scheduled_when_stale(self) -> None:
        mock_pool = MagicMock()
        evaluator = IngestionPolicyEvaluator(
            scope="global", db_pool=mock_pool, refresh_interval_s=60
        )
        # Set last loaded to far in the past
        evaluator._last_loaded_at = time.monotonic() - 120
        evaluator._rules = []

        def _consume_coro_and_return_mock(coro):
            """Close the coroutine to avoid RuntimeWarning, return a mock task."""
            coro.close()
            mock_task = MagicMock()
            mock_task.done.return_value = False
            return mock_task

        with patch.object(
            asyncio, "create_task", side_effect=_consume_coro_and_return_mock
        ) as mock_create_task:
            evaluator.evaluate(_email_envelope())
            mock_create_task.assert_called_once()

    def test_no_stacking_refresh_tasks(self) -> None:
        mock_pool = MagicMock()
        evaluator = IngestionPolicyEvaluator(
            scope="global", db_pool=mock_pool, refresh_interval_s=60
        )
        evaluator._last_loaded_at = time.monotonic() - 120

        # Simulate existing running task
        mock_task = MagicMock()
        mock_task.done.return_value = False
        evaluator._background_refresh_task = mock_task

        with patch.object(asyncio, "create_task") as mock_create_task:
            evaluator.evaluate(_email_envelope())
            mock_create_task.assert_not_called()


# ---------------------------------------------------------------------------
# IngestionPolicyEvaluator — invalidate
# ---------------------------------------------------------------------------


class TestEvaluatorInvalidate:
    def test_invalidate_sets_timestamp_to_zero(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()

        evaluator.invalidate()

        assert evaluator._last_loaded_at == 0.0

    def test_invalidate_triggers_refresh_on_next_evaluate(self) -> None:
        mock_pool = MagicMock()
        evaluator = IngestionPolicyEvaluator(
            scope="global", db_pool=mock_pool, refresh_interval_s=60
        )
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = []

        evaluator.invalidate()

        def _consume_coro_and_return_mock(coro):
            coro.close()
            mock_task = MagicMock()
            mock_task.done.return_value = False
            return mock_task

        with patch.object(
            asyncio, "create_task", side_effect=_consume_coro_and_return_mock
        ) as mock_create_task:
            evaluator.evaluate(_email_envelope())
            mock_create_task.assert_called_once()


# ---------------------------------------------------------------------------
# IngestionPolicyEvaluator — scope property
# ---------------------------------------------------------------------------


class TestEvaluatorScope:
    def test_scope_property(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="connector:gmail:gmail:user:dev", db_pool=None)
        assert evaluator.scope == "connector:gmail:gmail:user:dev"

    def test_rules_property_empty_by_default(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        assert evaluator.rules == []


# ---------------------------------------------------------------------------
# IngestionPolicyEvaluator — mixed rule_types in one scope
# ---------------------------------------------------------------------------


class TestMixedRuleTypes:
    def test_global_scope_with_multiple_rule_types(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
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

        # Test: header match takes priority
        d1 = evaluator.evaluate(
            _email_envelope(
                sender="x@bank.com",
                headers={"X-Priority": "1"},
                mime_parts=["text/calendar"],
            )
        )
        assert d1.matched_rule_id == "r1"
        assert d1.action == "skip"

        # Test: no header match -> falls through to domain match
        d2 = evaluator.evaluate(
            _email_envelope(sender="alerts@bank.com", mime_parts=["text/calendar"])
        )
        assert d2.matched_rule_id == "r2"
        assert d2.action == "route_to"
        assert d2.target_butler == "finance"

        # Test: no header/domain match -> falls through to mime match
        d3 = evaluator.evaluate(
            _email_envelope(sender="user@other.com", mime_parts=["text/calendar"])
        )
        assert d3.matched_rule_id == "r3"
        assert d3.action == "route_to"
        assert d3.target_butler == "calendar"

        # Test: no match at all -> pass_through
        d4 = evaluator.evaluate(_email_envelope(sender="user@other.com", mime_parts=["text/plain"]))
        assert d4.action == "pass_through"


# ---------------------------------------------------------------------------
# Telemetry helpers (OTel InMemoryMetricReader)
# ---------------------------------------------------------------------------


def _reset_metrics_global_state() -> None:
    """Reset the OTel global MeterProvider state for test isolation."""
    _metrics_internal._METER_PROVIDER_SET_ONCE = Once()
    _metrics_internal._METER_PROVIDER = None


def _make_in_memory_provider() -> tuple[MeterProvider, InMemoryMetricReader]:
    """Create a MeterProvider with an InMemoryMetricReader for test assertions."""
    _reset_metrics_global_state()
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    otel_metrics.set_meter_provider(provider)
    return provider, reader


def _collect_metrics(reader: InMemoryMetricReader) -> dict[str, Any]:
    """Flatten metrics data into {metric_name: [data_points]} for easy assertions."""
    result: dict[str, Any] = {}
    data = reader.get_metrics_data()
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.data.data_points:
                    result[metric.name] = metric.data.data_points
    return result


# ---------------------------------------------------------------------------
# Cardinality-safe label helpers
# ---------------------------------------------------------------------------


class TestScopeType:
    def test_global(self) -> None:
        assert _scope_type("global") == "global"

    def test_connector_gmail(self) -> None:
        assert _scope_type("connector:gmail:gmail:user:dev") == "connector:gmail"

    def test_connector_telegram_bot(self) -> None:
        assert _scope_type("connector:telegram-bot:telegram-bot:my-bot") == "connector:telegram-bot"

    def test_connector_minimal(self) -> None:
        assert _scope_type("connector:discord") == "connector:discord"

    def test_unknown_fallback(self) -> None:
        assert _scope_type("something_else") == "something_else"


class TestSafeAction:
    def test_route_to_stripped(self) -> None:
        assert _safe_action("route_to:finance") == "route_to"

    def test_route_to_no_target(self) -> None:
        assert _safe_action("route_to") == "route_to"

    def test_skip_unchanged(self) -> None:
        assert _safe_action("skip") == "skip"

    def test_block_unchanged(self) -> None:
        assert _safe_action("block") == "block"

    def test_pass_through_unchanged(self) -> None:
        assert _safe_action("pass_through") == "pass_through"


# ---------------------------------------------------------------------------
# IngestionPolicyMetrics — unit tests
# ---------------------------------------------------------------------------


class TestIngestionPolicyMetrics:
    @pytest.fixture(autouse=True)
    def _install_provider(self) -> None:
        _provider, reader = _make_in_memory_provider()
        self._reader = reader
        yield
        _reset_metrics_global_state()

    def _metrics_map(self) -> dict[str, Any]:
        return _collect_metrics(self._reader)

    def test_record_match_counter(self) -> None:
        m = IngestionPolicyMetrics(scope="global")
        m.record_match(
            rule_type="sender_domain",
            action="skip",
            source_channel="email",
            latency_ms=0.5,
        )
        data = self._metrics_map()
        assert "butlers.ingestion.rule_matched" in data
        dp = data["butlers.ingestion.rule_matched"][0]
        assert dp.value == 1
        assert dp.attributes["scope_type"] == "global"
        assert dp.attributes["rule_type"] == "sender_domain"
        assert dp.attributes["action"] == "skip"
        assert dp.attributes["source_channel"] == "email"

    def test_record_match_route_to_action_sanitized(self) -> None:
        m = IngestionPolicyMetrics(scope="global")
        m.record_match(
            rule_type="sender_domain",
            action="route_to:finance",
            source_channel="email",
            latency_ms=0.3,
        )
        data = self._metrics_map()
        dp = data["butlers.ingestion.rule_matched"][0]
        assert dp.attributes["action"] == "route_to"

    def test_record_match_latency_histogram(self) -> None:
        m = IngestionPolicyMetrics(scope="global")
        m.record_match(
            rule_type="sender_domain",
            action="skip",
            source_channel="email",
            latency_ms=1.5,
        )
        data = self._metrics_map()
        assert "butlers.ingestion.evaluation_latency_ms" in data
        dp = data["butlers.ingestion.evaluation_latency_ms"][0]
        assert dp.count == 1
        assert dp.sum == pytest.approx(1.5, rel=1e-2)
        assert dp.attributes["scope_type"] == "global"
        assert dp.attributes["result"] == "matched"

    def test_record_pass_through_counter(self) -> None:
        m = IngestionPolicyMetrics(scope="connector:gmail:gmail:user:dev")
        m.record_pass_through(
            source_channel="email",
            reason="no rule matched",
            latency_ms=0.2,
        )
        data = self._metrics_map()
        assert "butlers.ingestion.rule_pass_through" in data
        dp = data["butlers.ingestion.rule_pass_through"][0]
        assert dp.value == 1
        assert dp.attributes["scope_type"] == "connector:gmail"
        assert dp.attributes["source_channel"] == "email"
        assert dp.attributes["reason"] == "no rule matched"

    def test_record_pass_through_latency_histogram(self) -> None:
        m = IngestionPolicyMetrics(scope="global")
        m.record_pass_through(
            source_channel="telegram",
            reason="no rule matched",
            latency_ms=0.8,
        )
        data = self._metrics_map()
        assert "butlers.ingestion.evaluation_latency_ms" in data
        dp = data["butlers.ingestion.evaluation_latency_ms"][0]
        assert dp.count == 1
        assert dp.sum == pytest.approx(0.8, rel=1e-2)
        assert dp.attributes["result"] == "pass_through"

    def test_connector_scope_type_cardinality(self) -> None:
        """Connector scope strips endpoint identity for cardinality safety."""
        m = IngestionPolicyMetrics(scope="connector:telegram-bot:telegram-bot:my-bot")
        m.record_match(
            rule_type="chat_id",
            action="block",
            source_channel="telegram",
            latency_ms=0.1,
        )
        data = self._metrics_map()
        dp = data["butlers.ingestion.rule_matched"][0]
        assert dp.attributes["scope_type"] == "connector:telegram-bot"


# ---------------------------------------------------------------------------
# Evaluator telemetry integration tests
# ---------------------------------------------------------------------------


class TestEvaluatorTelemetryIntegration:
    """Verify that evaluate() records OTel metrics end-to-end."""

    @pytest.fixture(autouse=True)
    def _install_provider(self) -> None:
        _provider, reader = _make_in_memory_provider()
        self._reader = reader
        yield
        _reset_metrics_global_state()

    def _metrics_map(self) -> dict[str, Any]:
        return _collect_metrics(self._reader)

    def test_match_records_rule_matched_metric(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                rule_type="sender_domain",
                condition={"domain": "chase.com", "match": "exact"},
                action="skip",
            )
        ]

        evaluator.evaluate(_email_envelope(sender="alerts@chase.com"))

        data = self._metrics_map()
        assert "butlers.ingestion.rule_matched" in data
        dp = data["butlers.ingestion.rule_matched"][0]
        assert dp.value == 1
        assert dp.attributes["scope_type"] == "global"
        assert dp.attributes["rule_type"] == "sender_domain"
        assert dp.attributes["action"] == "skip"
        assert dp.attributes["source_channel"] == "email"

    def test_match_records_latency_with_matched_result(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                rule_type="sender_address",
                condition={"address": "test@test.com"},
                action="metadata_only",
            )
        ]

        evaluator.evaluate(_email_envelope(sender="test@test.com"))

        data = self._metrics_map()
        assert "butlers.ingestion.evaluation_latency_ms" in data
        dp = data["butlers.ingestion.evaluation_latency_ms"][0]
        assert dp.count == 1
        assert dp.sum >= 0
        assert dp.attributes["result"] == "matched"

    def test_no_match_records_pass_through_metric(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = []

        evaluator.evaluate(_email_envelope(sender="user@example.com"))

        data = self._metrics_map()
        assert "butlers.ingestion.rule_pass_through" in data
        dp = data["butlers.ingestion.rule_pass_through"][0]
        assert dp.value == 1
        assert dp.attributes["scope_type"] == "global"
        assert dp.attributes["source_channel"] == "email"
        assert dp.attributes["reason"] == "no rule matched"

    def test_no_match_records_latency_with_pass_through_result(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = []

        evaluator.evaluate(_email_envelope(sender="user@example.com"))

        data = self._metrics_map()
        assert "butlers.ingestion.evaluation_latency_ms" in data
        dp = data["butlers.ingestion.evaluation_latency_ms"][0]
        assert dp.count == 1
        assert dp.attributes["result"] == "pass_through"

    def test_connector_scope_match_uses_correct_scope_type(self) -> None:
        evaluator = IngestionPolicyEvaluator(
            scope="connector:gmail:gmail:user:alice@example.com", db_pool=None
        )
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                rule_type="sender_domain",
                condition={"domain": "spam.com", "match": "exact"},
                action="block",
            )
        ]

        evaluator.evaluate(_email_envelope(sender="bad@spam.com"))

        data = self._metrics_map()
        dp = data["butlers.ingestion.rule_matched"][0]
        assert dp.attributes["scope_type"] == "connector:gmail"

    def test_route_to_action_sanitized_in_metric(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                rule_type="sender_domain",
                condition={"domain": "paypal.com", "match": "suffix"},
                action="route_to:finance",
            )
        ]

        evaluator.evaluate(_email_envelope(sender="service@paypal.com"))

        data = self._metrics_map()
        dp = data["butlers.ingestion.rule_matched"][0]
        assert dp.attributes["action"] == "route_to"

    def test_multiple_evaluations_accumulate(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                rule_type="sender_domain",
                condition={"domain": "example.com", "match": "exact"},
                action="skip",
            )
        ]

        # One match
        evaluator.evaluate(_email_envelope(sender="user@example.com"))
        # One pass-through
        evaluator.evaluate(_email_envelope(sender="user@other.com"))
        # Another match
        evaluator.evaluate(_email_envelope(sender="admin@example.com"))

        data = self._metrics_map()
        matched_dp = data["butlers.ingestion.rule_matched"][0]
        assert matched_dp.value == 2

        pt_dp = data["butlers.ingestion.rule_pass_through"][0]
        assert pt_dp.value == 1
