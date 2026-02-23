"""Tests for Gmail connector tier assignment and label filtering.

Covers:
- LabelFilterPolicy evaluation (include/exclude/default)
- PolicyTierAssigner (high_priority/interactive/default rules)
- classify_ingestion_tier (action -> tier mapping)
- evaluate_message_policy (full pipeline: label filter + tier assignment)
- _build_ingest_envelope tier-aware envelopes (Tier 1 full, Tier 2 metadata)
- _ingest_single_message policy gating (skip/metadata/full paths)
- Helper: parse_label_list, load_known_contacts_from_file, _normalize_email
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.connectors.gmail_policy import (
    INGESTION_TIER_FULL,
    INGESTION_TIER_METADATA,
    INGESTION_TIER_SKIP,
    POLICY_TIER_DEFAULT,
    POLICY_TIER_HIGH_PRIORITY,
    POLICY_TIER_INTERACTIVE,
    RULE_DIRECT_CORRESPONDENCE,
    RULE_FALLBACK_DEFAULT,
    RULE_KNOWN_CONTACT,
    RULE_REPLY_TO_OUTBOUND,
    LabelFilterPolicy,
    MessagePolicyResult,
    PolicyTierAssigner,
    _normalize_email,
    classify_ingestion_tier,
    evaluate_message_policy,
    load_known_contacts_from_file,
    parse_label_list,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_message(
    *,
    labels: list[str] | None = None,
    from_addr: str = "sender@example.com",
    to_addr: str = "user@example.com",
    subject: str = "Hello",
    extra_headers: list[dict[str, str]] | None = None,
    message_id: str = "msg-001",
) -> dict[str, Any]:
    """Build a minimal Gmail messages.get response dict."""
    headers = [
        {"name": "From", "value": from_addr},
        {"name": "To", "value": to_addr},
        {"name": "Subject", "value": subject},
        {"name": "Message-ID", "value": f"<{message_id}@mail.example.com>"},
    ]
    if extra_headers:
        headers.extend(extra_headers)
    return {
        "id": message_id,
        "threadId": "thread-001",
        "internalDate": "1700000000000",
        "labelIds": labels or ["INBOX"],
        "payload": {
            "mimeType": "text/plain",
            "headers": headers,
            "body": {"data": ""},
        },
    }


def _make_label_filter(
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> LabelFilterPolicy:
    return LabelFilterPolicy.from_lists(include=include, exclude=exclude)


def _make_tier_assigner(
    user_email: str = "user@example.com",
    known_contacts: list[str] | None = None,
    sent_message_ids: list[str] | None = None,
) -> PolicyTierAssigner:
    return PolicyTierAssigner(
        user_email=user_email,
        known_contacts=frozenset(known_contacts or []),
        sent_message_ids=frozenset(sent_message_ids or []),
    )


# ---------------------------------------------------------------------------
# _normalize_email
# ---------------------------------------------------------------------------


class TestNormalizeEmail:
    def test_strips_whitespace(self) -> None:
        assert _normalize_email("  alice@example.com  ") == "alice@example.com"

    def test_lowercases(self) -> None:
        assert _normalize_email("ALICE@EXAMPLE.COM") == "alice@example.com"

    def test_strips_display_name(self) -> None:
        assert _normalize_email("Alice Smith <alice@example.com>") == "alice@example.com"

    def test_strips_angle_brackets_only(self) -> None:
        assert _normalize_email("<alice@example.com>") == "alice@example.com"

    def test_no_change_for_plain(self) -> None:
        assert _normalize_email("bob@example.com") == "bob@example.com"

    def test_mixed_case_display(self) -> None:
        assert _normalize_email("Bob Smith <BOB@EXAMPLE.COM>") == "bob@example.com"


# ---------------------------------------------------------------------------
# parse_label_list
# ---------------------------------------------------------------------------


class TestParseLabelList:
    def test_empty_string(self) -> None:
        assert parse_label_list("") == []

    def test_none(self) -> None:
        assert parse_label_list(None) == []

    def test_single(self) -> None:
        assert parse_label_list("INBOX") == ["INBOX"]

    def test_comma_separated(self) -> None:
        assert parse_label_list("SPAM,TRASH,INBOX") == ["SPAM", "TRASH", "INBOX"]

    def test_strips_whitespace(self) -> None:
        assert parse_label_list("SPAM , TRASH , INBOX") == ["SPAM", "TRASH", "INBOX"]

    def test_empty_segments_ignored(self) -> None:
        assert parse_label_list(",,,") == []


# ---------------------------------------------------------------------------
# load_known_contacts_from_file
# ---------------------------------------------------------------------------


class TestLoadKnownContacts:
    def test_dict_format(self, tmp_path: Path) -> None:
        p = tmp_path / "contacts.json"
        p.write_text(json.dumps({"contacts": ["alice@example.com", "BOB@EXAMPLE.COM"]}))
        result = load_known_contacts_from_file(str(p))
        assert "alice@example.com" in result
        assert "bob@example.com" in result  # normalized

    def test_list_format(self, tmp_path: Path) -> None:
        p = tmp_path / "contacts.json"
        p.write_text(json.dumps(["alice@example.com"]))
        result = load_known_contacts_from_file(str(p))
        assert "alice@example.com" in result

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        result = load_known_contacts_from_file(str(tmp_path / "nonexistent.json"))
        assert result == frozenset()

    def test_invalid_json_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("this is not json")
        result = load_known_contacts_from_file(str(p))
        assert result == frozenset()

    def test_unexpected_type_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "contacts.json"
        p.write_text(json.dumps("just a string"))
        result = load_known_contacts_from_file(str(p))
        assert result == frozenset()

    def test_display_name_addresses_normalized(self, tmp_path: Path) -> None:
        p = tmp_path / "contacts.json"
        p.write_text(json.dumps({"contacts": ["Alice <alice@example.com>"]}))
        result = load_known_contacts_from_file(str(p))
        assert "alice@example.com" in result


# ---------------------------------------------------------------------------
# LabelFilterPolicy
# ---------------------------------------------------------------------------


class TestLabelFilterPolicy:
    def test_no_include_no_exclude_allows_all(self) -> None:
        policy = _make_label_filter()
        ok, reason = policy.evaluate(["INBOX"])
        assert ok is True
        assert reason == "label_allowed"

    def test_exclude_spam_blocks(self) -> None:
        policy = _make_label_filter(exclude=["SPAM"])
        ok, reason = policy.evaluate(["INBOX", "SPAM"])
        assert ok is False
        assert "SPAM" in reason

    def test_exclude_takes_precedence_over_include(self) -> None:
        policy = _make_label_filter(include=["INBOX"], exclude=["SPAM"])
        ok, reason = policy.evaluate(["INBOX", "SPAM"])
        assert ok is False

    def test_include_list_gates_ingestion(self) -> None:
        policy = _make_label_filter(include=["INBOX"])
        ok, _ = policy.evaluate(["CATEGORY_PROMOTIONS"])
        assert ok is False

    def test_include_list_allows_matching(self) -> None:
        policy = _make_label_filter(include=["INBOX"])
        ok, _ = policy.evaluate(["INBOX"])
        assert ok is True

    def test_case_insensitive_labels(self) -> None:
        policy = _make_label_filter(exclude=["spam"])
        ok, reason = policy.evaluate(["SPAM"])
        assert ok is False

    def test_default_policy_excludes_spam_and_trash(self) -> None:
        policy = LabelFilterPolicy.default()
        ok_spam, _ = policy.evaluate(["SPAM"])
        ok_trash, _ = policy.evaluate(["TRASH"])
        ok_inbox, _ = policy.evaluate(["INBOX"])
        assert ok_spam is False
        assert ok_trash is False
        assert ok_inbox is True

    def test_empty_message_labels_with_no_include_allowed(self) -> None:
        policy = _make_label_filter()
        ok, _ = policy.evaluate([])
        assert ok is True

    def test_empty_message_labels_with_include_blocked(self) -> None:
        policy = _make_label_filter(include=["INBOX"])
        ok, _ = policy.evaluate([])
        assert ok is False

    def test_multiple_exclude_labels_first_wins(self) -> None:
        policy = _make_label_filter(exclude=["SPAM", "TRASH"])
        ok, reason = policy.evaluate(["TRASH"])
        assert ok is False
        assert "TRASH" in reason


# ---------------------------------------------------------------------------
# PolicyTierAssigner
# ---------------------------------------------------------------------------


class TestPolicyTierAssigner:
    def test_known_contact_high_priority(self) -> None:
        assigner = _make_tier_assigner(
            user_email="user@example.com",
            known_contacts=["alice@example.com"],
        )
        tier, rule = assigner.assign("alice@example.com", {})
        assert tier == POLICY_TIER_HIGH_PRIORITY
        assert rule == RULE_KNOWN_CONTACT

    def test_known_contact_display_name_normalized(self) -> None:
        assigner = _make_tier_assigner(
            user_email="user@example.com",
            known_contacts=["alice@example.com"],
        )
        tier, rule = assigner.assign("Alice Smith <alice@example.com>", {})
        assert tier == POLICY_TIER_HIGH_PRIORITY
        assert rule == RULE_KNOWN_CONTACT

    def test_reply_to_outbound_high_priority(self) -> None:
        sent_id = "<abc123@mail.example.com>"
        assigner = _make_tier_assigner(
            user_email="user@example.com",
            sent_message_ids=[sent_id],
        )
        tier, rule = assigner.assign("other@example.com", {"In-Reply-To": sent_id})
        assert tier == POLICY_TIER_HIGH_PRIORITY
        assert rule == RULE_REPLY_TO_OUTBOUND

    def test_reply_to_outbound_without_brackets(self) -> None:
        assigner = _make_tier_assigner(
            user_email="user@example.com",
            sent_message_ids=["<abc123@mail.example.com>"],
        )
        tier, rule = assigner.assign(
            "other@example.com", {"In-Reply-To": "abc123@mail.example.com"}
        )
        assert tier == POLICY_TIER_HIGH_PRIORITY
        assert rule == RULE_REPLY_TO_OUTBOUND

    def test_direct_correspondence_interactive(self) -> None:
        assigner = _make_tier_assigner(user_email="user@example.com")
        tier, rule = assigner.assign(
            "sender@example.com",
            {"To": "user@example.com", "Cc": ""},
        )
        assert tier == POLICY_TIER_INTERACTIVE
        assert rule == RULE_DIRECT_CORRESPONDENCE

    def test_list_unsubscribe_prevents_interactive(self) -> None:
        assigner = _make_tier_assigner(user_email="user@example.com")
        tier, rule = assigner.assign(
            "newsletter@example.com",
            {
                "To": "user@example.com",
                "List-Unsubscribe": "<https://example.com/unsub>",
            },
        )
        assert tier == POLICY_TIER_DEFAULT
        assert rule == RULE_FALLBACK_DEFAULT

    def test_bulk_precedence_prevents_interactive(self) -> None:
        assigner = _make_tier_assigner(user_email="user@example.com")
        tier, rule = assigner.assign(
            "list@example.com",
            {"To": "user@example.com", "Precedence": "bulk"},
        )
        assert tier == POLICY_TIER_DEFAULT
        assert rule == RULE_FALLBACK_DEFAULT

    def test_list_precedence_prevents_interactive(self) -> None:
        assigner = _make_tier_assigner(user_email="user@example.com")
        tier, rule = assigner.assign(
            "list@example.com",
            {"To": "user@example.com", "Precedence": "list"},
        )
        assert tier == POLICY_TIER_DEFAULT
        assert rule == RULE_FALLBACK_DEFAULT

    def test_user_not_in_recipients_defaults(self) -> None:
        assigner = _make_tier_assigner(user_email="user@example.com")
        tier, rule = assigner.assign(
            "sender@example.com",
            {"To": "other@example.com"},
        )
        assert tier == POLICY_TIER_DEFAULT
        assert rule == RULE_FALLBACK_DEFAULT

    def test_fallback_default_when_no_rules_match(self) -> None:
        assigner = _make_tier_assigner(user_email="user@example.com")
        tier, rule = assigner.assign("newsletter@acme.com", {})
        assert tier == POLICY_TIER_DEFAULT
        assert rule == RULE_FALLBACK_DEFAULT

    def test_known_contact_takes_precedence_over_direct(self) -> None:
        # Known contact rule has higher priority (rule 1) than direct correspondence (rule 3)
        assigner = _make_tier_assigner(
            user_email="user@example.com",
            known_contacts=["alice@example.com"],
        )
        tier, rule = assigner.assign(
            "alice@example.com",
            {"To": "user@example.com"},
        )
        assert tier == POLICY_TIER_HIGH_PRIORITY
        assert rule == RULE_KNOWN_CONTACT

    def test_cc_counts_as_recipient(self) -> None:
        assigner = _make_tier_assigner(user_email="user@example.com")
        tier, rule = assigner.assign(
            "sender@example.com",
            {"To": "other@example.com", "Cc": "user@example.com"},
        )
        assert tier == POLICY_TIER_INTERACTIVE
        assert rule == RULE_DIRECT_CORRESPONDENCE

    def test_case_insensitive_header_lookup(self) -> None:
        assigner = _make_tier_assigner(user_email="user@example.com")
        tier, rule = assigner.assign(
            "sender@example.com",
            {"to": "user@example.com"},
        )
        assert tier == POLICY_TIER_INTERACTIVE
        assert rule == RULE_DIRECT_CORRESPONDENCE

    def test_empty_user_email_no_crash(self) -> None:
        assigner = _make_tier_assigner(user_email="")
        # With empty user email, user is never in recipients -> default
        tier, rule = assigner.assign("sender@example.com", {"To": "user@example.com"})
        assert tier == POLICY_TIER_DEFAULT

    def test_no_sent_ids_reply_rule_skipped(self) -> None:
        assigner = _make_tier_assigner(
            user_email="user@example.com",
            sent_message_ids=[],
        )
        tier, rule = assigner.assign(
            "other@example.com",
            {"In-Reply-To": "<abc123@mail.example.com>"},
        )
        # Falls through to direct correspondence or default
        assert tier in (POLICY_TIER_INTERACTIVE, POLICY_TIER_DEFAULT)


# ---------------------------------------------------------------------------
# classify_ingestion_tier
# ---------------------------------------------------------------------------


class TestClassifyIngestionTier:
    def test_route_to_is_tier_1(self) -> None:
        assert classify_ingestion_tier("route_to:finance") == INGESTION_TIER_FULL

    def test_metadata_only_is_tier_2(self) -> None:
        assert classify_ingestion_tier("metadata_only") == INGESTION_TIER_METADATA

    def test_skip_is_tier_3(self) -> None:
        assert classify_ingestion_tier("skip") == INGESTION_TIER_SKIP

    def test_low_priority_queue_is_tier_1(self) -> None:
        assert classify_ingestion_tier("low_priority_queue") == INGESTION_TIER_FULL

    def test_pass_through_is_tier_1(self) -> None:
        assert classify_ingestion_tier("pass_through") == INGESTION_TIER_FULL

    def test_unknown_action_defaults_to_tier_1(self) -> None:
        assert classify_ingestion_tier("some_unknown_action") == INGESTION_TIER_FULL

    def test_bare_route_to_is_tier_1(self) -> None:
        assert classify_ingestion_tier("route_to") == INGESTION_TIER_FULL


# ---------------------------------------------------------------------------
# evaluate_message_policy (full pipeline)
# ---------------------------------------------------------------------------


class TestEvaluateMessagePolicy:
    def test_spam_label_skipped(self) -> None:
        msg = _make_message(labels=["INBOX", "SPAM"])
        result = evaluate_message_policy(
            msg,
            label_filter=LabelFilterPolicy.default(),
            tier_assigner=_make_tier_assigner(),
        )
        assert result.should_ingest is False
        assert result.ingestion_tier == INGESTION_TIER_SKIP
        assert "SPAM" in result.filter_reason

    def test_trash_label_skipped(self) -> None:
        msg = _make_message(labels=["TRASH"])
        result = evaluate_message_policy(
            msg,
            label_filter=LabelFilterPolicy.default(),
            tier_assigner=_make_tier_assigner(),
        )
        assert result.should_ingest is False

    def test_inbox_allowed_by_default(self) -> None:
        msg = _make_message(labels=["INBOX"])
        result = evaluate_message_policy(
            msg,
            label_filter=LabelFilterPolicy.default(),
            tier_assigner=_make_tier_assigner(),
        )
        assert result.should_ingest is True
        assert result.ingestion_tier == INGESTION_TIER_FULL

    def test_triage_skip_action_produces_tier3(self) -> None:
        msg = _make_message(labels=["INBOX"], from_addr="newsletter@bulk.example.com")
        skip_rule = {
            "id": "r1",
            "rule_type": "sender_domain",
            "condition": {"domain": "bulk.example.com", "match": "exact"},
            "action": "skip",
        }
        result = evaluate_message_policy(
            msg,
            label_filter=_make_label_filter(),
            tier_assigner=_make_tier_assigner(),
            triage_rules=[skip_rule],
        )
        assert result.should_ingest is False
        assert result.ingestion_tier == INGESTION_TIER_SKIP
        assert result.triage_action == "skip"

    def test_triage_metadata_only_action_produces_tier2(self) -> None:
        msg = _make_message(labels=["INBOX"], from_addr="news@example.com")
        meta_rule = {
            "id": "r2",
            "rule_type": "sender_domain",
            "condition": {"domain": "example.com", "match": "exact"},
            "action": "metadata_only",
        }
        result = evaluate_message_policy(
            msg,
            label_filter=_make_label_filter(),
            tier_assigner=_make_tier_assigner(),
            triage_rules=[meta_rule],
        )
        assert result.should_ingest is True
        assert result.ingestion_tier == INGESTION_TIER_METADATA

    def test_no_triage_rules_defaults_to_tier1(self) -> None:
        msg = _make_message(labels=["INBOX"])
        result = evaluate_message_policy(
            msg,
            label_filter=_make_label_filter(),
            tier_assigner=_make_tier_assigner(),
            triage_rules=None,
        )
        assert result.ingestion_tier == INGESTION_TIER_FULL

    def test_known_contact_gets_high_priority_policy_tier(self) -> None:
        msg = _make_message(labels=["INBOX"], from_addr="alice@example.com")
        result = evaluate_message_policy(
            msg,
            label_filter=_make_label_filter(),
            tier_assigner=_make_tier_assigner(
                user_email="user@example.com",
                known_contacts=["alice@example.com"],
            ),
        )
        assert result.policy_tier == POLICY_TIER_HIGH_PRIORITY
        assert result.assignment_rule == RULE_KNOWN_CONTACT

    def test_direct_correspondence_gets_interactive_policy_tier(self) -> None:
        msg = _make_message(
            labels=["INBOX"],
            from_addr="sender@example.com",
            to_addr="user@example.com",
        )
        result = evaluate_message_policy(
            msg,
            label_filter=_make_label_filter(),
            tier_assigner=_make_tier_assigner(user_email="user@example.com"),
        )
        assert result.policy_tier == POLICY_TIER_INTERACTIVE

    def test_label_excluded_overrides_triage_rules(self) -> None:
        # Even if a rule would match, label exclude comes first
        msg = _make_message(labels=["SPAM"], from_addr="alice@example.com")
        route_rule = {
            "id": "r3",
            "rule_type": "sender_address",
            "condition": {"address": "alice@example.com"},
            "action": "route_to:vip",
        }
        result = evaluate_message_policy(
            msg,
            label_filter=LabelFilterPolicy.default(),
            tier_assigner=_make_tier_assigner(
                known_contacts=["alice@example.com"],
            ),
            triage_rules=[route_rule],
        )
        # Label filter must run first, so SPAM label causes skip regardless
        assert result.should_ingest is False
        assert result.ingestion_tier == INGESTION_TIER_SKIP

    def test_header_condition_rule_triggers_metadata(self) -> None:
        msg = _make_message(
            labels=["INBOX"],
            extra_headers=[{"name": "List-Unsubscribe", "value": "<https://x.com/unsub>"}],
        )
        meta_rule = {
            "id": "r4",
            "rule_type": "header_condition",
            "condition": {"header": "List-Unsubscribe", "op": "present"},
            "action": "metadata_only",
        }
        result = evaluate_message_policy(
            msg,
            label_filter=_make_label_filter(),
            tier_assigner=_make_tier_assigner(),
            triage_rules=[meta_rule],
        )
        assert result.ingestion_tier == INGESTION_TIER_METADATA

    def test_label_match_rule_skips_promotions(self) -> None:
        msg = _make_message(labels=["CATEGORY_PROMOTIONS", "INBOX"])
        skip_rule = {
            "id": "r5",
            "rule_type": "label_match",
            "condition": {"label": "CATEGORY_PROMOTIONS"},
            "action": "skip",
        }
        result = evaluate_message_policy(
            msg,
            label_filter=_make_label_filter(),
            tier_assigner=_make_tier_assigner(),
            triage_rules=[skip_rule],
        )
        assert result.should_ingest is False
        assert result.ingestion_tier == INGESTION_TIER_SKIP

    def test_empty_triage_rules_allows_full_tier(self) -> None:
        msg = _make_message(labels=["INBOX"])
        result = evaluate_message_policy(
            msg,
            label_filter=_make_label_filter(),
            tier_assigner=_make_tier_assigner(),
            triage_rules=[],
        )
        assert result.ingestion_tier == INGESTION_TIER_FULL

    def test_first_matching_rule_wins(self) -> None:
        msg = _make_message(labels=["INBOX"], from_addr="news@bulk.example.com")
        skip_rule = {
            "id": "r1",
            "rule_type": "sender_domain",
            "condition": {"domain": "bulk.example.com", "match": "exact"},
            "action": "skip",
        }
        meta_rule = {
            "id": "r2",
            "rule_type": "sender_domain",
            "condition": {"domain": "bulk.example.com", "match": "exact"},
            "action": "metadata_only",
        }
        result = evaluate_message_policy(
            msg,
            label_filter=_make_label_filter(),
            tier_assigner=_make_tier_assigner(),
            triage_rules=[skip_rule, meta_rule],
        )
        # First rule (skip) wins
        assert result.ingestion_tier == INGESTION_TIER_SKIP


# ---------------------------------------------------------------------------
# GmailConnectorRuntime._build_ingest_envelope tier-aware behavior
# ---------------------------------------------------------------------------


class TestBuildIngestEnvelopeTiers:
    """Test _build_ingest_envelope produces correct tier-specific envelopes."""

    @pytest.fixture
    def runtime(self, tmp_path: Path) -> Any:
        from butlers.connectors.gmail import GmailConnectorConfig, GmailConnectorRuntime

        config = GmailConnectorConfig(
            switchboard_mcp_url="http://localhost:40100/sse",
            connector_endpoint_identity="gmail:user:test@example.com",
            connector_cursor_path=tmp_path / "cursor.json",
            gmail_client_id="x",
            gmail_client_secret="x",
            gmail_refresh_token="x",
        )
        return GmailConnectorRuntime(config)

    async def test_tier1_full_envelope(self, runtime: Any) -> None:
        msg = _make_message(labels=["INBOX"])
        policy = MessagePolicyResult(
            should_ingest=True,
            ingestion_tier=INGESTION_TIER_FULL,
            policy_tier=POLICY_TIER_HIGH_PRIORITY,
            assignment_rule=RULE_KNOWN_CONTACT,
            filter_reason="label_allowed",
            triage_action="route_to:finance",
        )
        envelope = await runtime._build_ingest_envelope(msg, policy_result=policy)

        assert envelope["schema_version"] == "ingest.v1"
        assert envelope["control"]["policy_tier"] == POLICY_TIER_HIGH_PRIORITY
        assert "ingestion_tier" not in envelope["control"]  # Tier 1 has no ingestion_tier key
        assert envelope["payload"]["raw"] is not None  # Full payload included

    async def test_tier2_metadata_envelope_structure(self, runtime: Any) -> None:
        msg = _make_message(labels=["INBOX"], subject="Test Newsletter")
        policy = MessagePolicyResult(
            should_ingest=True,
            ingestion_tier=INGESTION_TIER_METADATA,
            policy_tier=POLICY_TIER_DEFAULT,
            assignment_rule=RULE_FALLBACK_DEFAULT,
            filter_reason="label_allowed",
            triage_action="metadata_only",
        )
        envelope = await runtime._build_ingest_envelope(msg, policy_result=policy)

        # Per spec §5.2
        assert envelope["control"]["ingestion_tier"] == "metadata"
        assert envelope["control"]["policy_tier"] == POLICY_TIER_DEFAULT
        assert envelope["payload"]["raw"] is None
        # normalized_text must be subject-only
        assert "Test Newsletter" in envelope["payload"]["normalized_text"]
        # Idempotency key must be present
        assert "idempotency_key" in envelope["control"]

    async def test_tier2_no_body_in_normalized_text(self, runtime: Any) -> None:
        """Tier 2 must not include full body in normalized_text."""
        msg = _make_message(labels=["INBOX"], subject="Newsletter Subject")
        msg["payload"]["body"]["data"] = "SEKRET BODY CONTENT"
        policy = MessagePolicyResult(
            should_ingest=True,
            ingestion_tier=INGESTION_TIER_METADATA,
            policy_tier=POLICY_TIER_DEFAULT,
            assignment_rule=RULE_FALLBACK_DEFAULT,
            filter_reason="label_allowed",
            triage_action="metadata_only",
        )
        envelope = await runtime._build_ingest_envelope(msg, policy_result=policy)
        assert "SEKRET BODY CONTENT" not in envelope["payload"]["normalized_text"]

    async def test_no_policy_result_defaults_to_tier1(self, runtime: Any) -> None:
        msg = _make_message(labels=["INBOX"])
        envelope = await runtime._build_ingest_envelope(msg, policy_result=None)
        assert envelope["control"]["policy_tier"] == "default"
        assert envelope["payload"]["raw"] is not None

    async def test_tier1_policy_tier_propagated(self, runtime: Any) -> None:
        msg = _make_message(labels=["INBOX"])
        policy = MessagePolicyResult(
            should_ingest=True,
            ingestion_tier=INGESTION_TIER_FULL,
            policy_tier=POLICY_TIER_INTERACTIVE,
            assignment_rule=RULE_DIRECT_CORRESPONDENCE,
            filter_reason="label_allowed",
            triage_action="pass_through",
        )
        envelope = await runtime._build_ingest_envelope(msg, policy_result=policy)
        assert envelope["control"]["policy_tier"] == POLICY_TIER_INTERACTIVE


# ---------------------------------------------------------------------------
# GmailConnectorRuntime._ingest_single_message integration
# ---------------------------------------------------------------------------


class TestIngestSingleMessagePolicy:
    """Test _ingest_single_message applies policy gating correctly."""

    @pytest.fixture
    def runtime(self, tmp_path: Path) -> Any:
        from butlers.connectors.gmail import GmailConnectorConfig, GmailConnectorRuntime

        config = GmailConnectorConfig(
            switchboard_mcp_url="http://localhost:40100/sse",
            connector_endpoint_identity="gmail:user:test@example.com",
            connector_cursor_path=tmp_path / "cursor.json",
            gmail_client_id="x",
            gmail_client_secret="x",
            gmail_refresh_token="x",
            gmail_label_exclude=("SPAM", "TRASH"),
        )
        runtime = GmailConnectorRuntime(config)
        # Initialize semaphore-based context (normally done in start())
        return runtime

    async def test_spam_label_prevents_ingest(self, runtime: Any) -> None:
        spam_message = _make_message(labels=["SPAM"])
        runtime._fetch_message = AsyncMock(return_value=spam_message)
        runtime._submit_to_ingest_api = AsyncMock()

        await runtime._ingest_single_message("spam-msg-001")

        runtime._submit_to_ingest_api.assert_not_called()

    async def test_inbox_message_is_ingested(self, runtime: Any) -> None:
        inbox_message = _make_message(labels=["INBOX"])
        runtime._fetch_message = AsyncMock(return_value=inbox_message)
        runtime._submit_to_ingest_api = AsyncMock()
        # Mock blob store (not initialized in test)
        runtime._blob_store = None

        await runtime._ingest_single_message("inbox-msg-001")

        runtime._submit_to_ingest_api.assert_called_once()
        envelope = runtime._submit_to_ingest_api.call_args[0][0]
        assert envelope["schema_version"] == "ingest.v1"

    async def test_tier2_triage_rule_submits_slim_envelope(self, runtime: Any) -> None:
        """When a triage rule triggers metadata_only, connector submits slim envelope."""
        inbox_message = _make_message(labels=["INBOX"], from_addr="news@newsletters.example.com")
        runtime._fetch_message = AsyncMock(return_value=inbox_message)
        runtime._submit_to_ingest_api = AsyncMock()
        runtime._blob_store = None

        # Patch evaluate_message_policy to return metadata tier directly
        with patch("butlers.connectors.gmail.evaluate_message_policy") as mock_eval:
            mock_eval.return_value = MessagePolicyResult(
                should_ingest=True,
                ingestion_tier=INGESTION_TIER_METADATA,
                policy_tier=POLICY_TIER_DEFAULT,
                assignment_rule=RULE_FALLBACK_DEFAULT,
                filter_reason="label_allowed",
                triage_action="metadata_only",
            )
            await runtime._ingest_single_message("meta-msg-001")

        runtime._submit_to_ingest_api.assert_called_once()
        envelope = runtime._submit_to_ingest_api.call_args[0][0]
        assert envelope["control"]["ingestion_tier"] == "metadata"
        assert envelope["payload"]["raw"] is None

    async def test_exception_in_policy_does_not_crash(self, runtime: Any) -> None:
        """If evaluate_message_policy raises, error is logged but not re-raised from gather."""
        runtime._fetch_message = AsyncMock(side_effect=RuntimeError("fetch failed"))
        runtime._submit_to_ingest_api = AsyncMock()

        # Should not raise (errors are caught in _ingest_single_message)
        await runtime._ingest_single_message("bad-msg")
        runtime._submit_to_ingest_api.assert_not_called()


# ---------------------------------------------------------------------------
# GmailConnectorConfig: label policy env var parsing
# ---------------------------------------------------------------------------


class TestGmailConnectorConfigLabelPolicy:
    def test_default_excludes_spam_trash(self, tmp_path: Path) -> None:
        from butlers.connectors.gmail import GmailConnectorConfig

        config = GmailConnectorConfig(
            switchboard_mcp_url="http://localhost:40100/sse",
            connector_endpoint_identity="gmail:user:test@example.com",
            connector_cursor_path=tmp_path / "cursor.json",
            gmail_client_id="x",
            gmail_client_secret="x",
            gmail_refresh_token="x",
        )
        assert "SPAM" in config.gmail_label_exclude
        assert "TRASH" in config.gmail_label_exclude

    def test_custom_exclude_labels(self, tmp_path: Path) -> None:
        from butlers.connectors.gmail import GmailConnectorConfig

        config = GmailConnectorConfig(
            switchboard_mcp_url="http://localhost:40100/sse",
            connector_endpoint_identity="gmail:user:test@example.com",
            connector_cursor_path=tmp_path / "cursor.json",
            gmail_client_id="x",
            gmail_client_secret="x",
            gmail_refresh_token="x",
            gmail_label_exclude=("SPAM", "CATEGORY_PROMOTIONS"),
        )
        assert "CATEGORY_PROMOTIONS" in config.gmail_label_exclude

    def test_env_label_include_parsed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:40100/sse")
        monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "gmail:user:test@example.com")
        monkeypatch.setenv("CONNECTOR_CURSOR_PATH", str(tmp_path / "cursor.json"))
        monkeypatch.setenv("GMAIL_LABEL_INCLUDE", "INBOX,IMPORTANT")
        monkeypatch.setenv("GMAIL_LABEL_EXCLUDE", "SPAM,TRASH")

        from butlers.connectors.gmail import GmailConnectorConfig

        config = GmailConnectorConfig.from_env(
            gmail_client_id="x",
            gmail_client_secret="x",
            gmail_refresh_token="x",
        )
        assert "INBOX" in config.gmail_label_include
        assert "IMPORTANT" in config.gmail_label_include
        assert "SPAM" in config.gmail_label_exclude
        assert "TRASH" in config.gmail_label_exclude

    def test_env_user_email_parsed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:40100/sse")
        monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "gmail:user:test@example.com")
        monkeypatch.setenv("CONNECTOR_CURSOR_PATH", str(tmp_path / "cursor.json"))
        monkeypatch.setenv("GMAIL_USER_EMAIL", "test@example.com")

        from butlers.connectors.gmail import GmailConnectorConfig

        config = GmailConnectorConfig.from_env(
            gmail_client_id="x",
            gmail_client_secret="x",
            gmail_refresh_token="x",
        )
        assert config.gmail_user_email == "test@example.com"
