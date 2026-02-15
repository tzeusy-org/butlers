"""Tests for approval redaction utilities.

Validates AC for butlers-0p6.4:
1. Sensitive fields in approval payloads/summaries are redacted before persistence/logging.
2. Execution error/result payloads avoid secret leakage.
3. Tests cover representative secret-bearing payloads and retention behavior.
"""

from __future__ import annotations

import pytest

from butlers.modules.approvals.redaction import (
    REDACTION_MARKER,
    redact_execution_result,
    redact_tool_args,
    should_redact_for_presentation,
)
from butlers.modules.approvals.sensitivity import SENSITIVE_ARG_NAMES

pytestmark = pytest.mark.unit


class TestRedactToolArgs:
    """Test redaction of sensitive tool arguments."""

    def test_redacts_sensitive_args_by_heuristic(self):
        """Sensitive arguments are replaced with redaction marker."""
        tool_args = {
            "to": "alice@example.com",
            "subject": "Test email",
            "body": "Hello world",
        }

        redacted = redact_tool_args("bot_email_send", tool_args)

        assert redacted["to"] == REDACTION_MARKER
        assert redacted["subject"] == "Test email"
        assert redacted["body"] == "Hello world"

    def test_preserves_non_sensitive_args(self):
        """Non-sensitive arguments are preserved as-is."""
        tool_args = {
            "message": "Hello",
            "channel": "general",
            "priority": 1,
        }

        redacted = redact_tool_args("bot_telegram_send", tool_args)

        assert redacted == tool_args

    def test_redacts_all_sensitive_patterns(self):
        """All heuristically sensitive argument names are redacted."""
        for arg_name in SENSITIVE_ARG_NAMES:
            tool_args = {arg_name: "secret-value"}
            redacted = redact_tool_args("test_tool", tool_args)
            assert redacted[arg_name] == REDACTION_MARKER

    def test_case_insensitive_redaction(self):
        """Redaction works regardless of argument name casing."""
        tool_args = {
            "TO": "alice@example.com",
            "Email": "bob@example.com",
            "RECIPIENT": "charlie@example.com",
        }

        redacted = redact_tool_args("bot_email_send", tool_args)

        assert redacted["TO"] == REDACTION_MARKER
        assert redacted["Email"] == REDACTION_MARKER
        assert redacted["RECIPIENT"] == REDACTION_MARKER

    def test_redacts_multiple_sensitive_args(self):
        """Multiple sensitive args in one payload are all redacted."""
        tool_args = {
            "to": "alice@example.com",
            "cc": "bob@example.com",
            "amount": "100.00",
            "account": "1234-5678",
            "subject": "Payment confirmation",
        }

        redacted = redact_tool_args("send_payment_notification", tool_args)

        assert redacted["to"] == REDACTION_MARKER
        assert redacted["amount"] == REDACTION_MARKER
        assert redacted["account"] == REDACTION_MARKER
        assert redacted["subject"] == "Payment confirmation"

    def test_preserves_structure(self):
        """Redaction preserves dict structure for audit purposes."""
        tool_args = {
            "recipient": "alice@example.com",
            "message": "Hello",
        }

        redacted = redact_tool_args("bot_telegram_send", tool_args)

        assert set(redacted.keys()) == set(tool_args.keys())
        assert len(redacted) == len(tool_args)

    def test_empty_args_dict(self):
        """Redaction handles empty argument dict."""
        redacted = redact_tool_args("no_args_tool", {})
        assert redacted == {}

    def test_redacts_credential_patterns(self):
        """Common credential argument names are redacted."""
        credential_args = {
            "password": "super-secret",
            "token": "abc123",
            "secret": "xyz789",
            "key": "my-api-key",
            "api_key": "sk-proj-123",
        }

        redacted = redact_tool_args("auth_tool", credential_args)

        for arg_name in credential_args:
            assert redacted[arg_name] == REDACTION_MARKER


class TestRedactExecutionResult:
    """Test redaction of execution results."""

    def test_redacts_error_messages(self):
        """Error messages are redacted (may contain secrets in stack traces)."""
        result = {
            "success": False,
            "error": "Invalid API key: sk-proj-abc123xyz",
        }

        redacted = redact_execution_result(result)

        assert redacted["success"] is False
        assert redacted["error"] == REDACTION_MARKER

    def test_preserves_success_flag(self):
        """Success flag is always preserved."""
        result = {"success": True, "result": {"status": "ok"}}
        redacted = redact_execution_result(result)
        assert redacted["success"] is True

    def test_preserves_result_payload(self):
        """Result payload is preserved (assumed controlled by tool impl)."""
        result = {
            "success": True,
            "result": {"message_id": "msg-123", "status": "sent"},
        }

        redacted = redact_execution_result(result)

        assert redacted["result"] == result["result"]

    def test_handles_none_error(self):
        """Redaction handles None error value."""
        result = {"success": True, "error": None}
        redacted = redact_execution_result(result)
        assert redacted["error"] is None

    def test_handles_missing_error(self):
        """Redaction handles missing error field."""
        result = {"success": True, "result": {"value": 42}}
        redacted = redact_execution_result(result)
        assert "error" not in redacted

    def test_complex_error_messages(self):
        """Complex error messages with potential secrets are redacted."""
        result = {
            "success": False,
            "error": (
                "HTTPError 401: Authentication failed for user admin@example.com with token abc123"
            ),
        }

        redacted = redact_execution_result(result)

        assert redacted["error"] == REDACTION_MARKER

    def test_deep_copy_prevents_mutation_leaks(self):
        """Deep copy prevents mutations from leaking to original."""
        result = {
            "success": False,
            "error": "Original error",
            "metadata": {"nested": {"value": "original"}},
        }

        redacted = redact_execution_result(result)

        # Mutate the redacted copy
        redacted["metadata"]["nested"]["value"] = "modified"

        # Original should be unchanged
        assert result["metadata"]["nested"]["value"] == "original"


class TestShouldRedactForPresentation:
    """Test access control logic for redaction."""

    def test_redact_when_no_owner(self):
        """Redact for all viewers when owner is unknown."""
        assert should_redact_for_presentation("alice", None) is True
        assert should_redact_for_presentation("bob", None) is True

    def test_owner_sees_unredacted(self):
        """Owner sees unredacted details."""
        assert should_redact_for_presentation("alice", "alice") is False

    def test_non_owner_sees_redacted(self):
        """Non-owners see redacted details."""
        assert should_redact_for_presentation("bob", "alice") is True

    def test_case_sensitive_owner_match(self):
        """Owner matching is case-sensitive."""
        assert should_redact_for_presentation("Alice", "alice") is True
        assert should_redact_for_presentation("alice", "Alice") is True


class TestRedactionIntegration:
    """Integration tests for redaction workflows."""

    def test_sensitive_payload_redaction_flow(self):
        """Complete flow: redact args and result for storage."""
        # Simulate approval request with sensitive data
        tool_args = {
            "to": "alice@example.com",
            "amount": "500.00",
            "memo": "Monthly payment",
        }

        # Redact before storage
        redacted_args = redact_tool_args("send_payment", tool_args)

        assert redacted_args["to"] == REDACTION_MARKER
        assert redacted_args["amount"] == REDACTION_MARKER
        assert redacted_args["memo"] == "Monthly payment"

        # Simulate execution failure with secret in error
        exec_result = {
            "success": False,
            "error": "Payment failed: Invalid account number 1234-5678-9012",
        }

        redacted_result = redact_execution_result(exec_result)

        assert redacted_result["success"] is False
        assert redacted_result["error"] == REDACTION_MARKER

    def test_non_sensitive_payload_preserved(self):
        """Non-sensitive payloads pass through unchanged."""
        tool_args = {"message": "Hello", "priority": "high"}

        redacted_args = redact_tool_args("send_notification", tool_args)
        assert redacted_args == tool_args

        exec_result = {"success": True, "result": {"sent": True}}
        redacted_result = redact_execution_result(exec_result)
        assert redacted_result == exec_result
