"""Tests for error normalization."""

from __future__ import annotations

import pytest

from butlers.tools.messenger.reliability.errors import ErrorNormalizer, NormalizedError


class TestErrorNormalizer:
    """Test error normalization to canonical classes."""

    def test_normalize_validation_error_with_400_status(self):
        """Test that HTTP 400 maps to validation_error."""
        error = ValueError("Invalid recipient format")
        provider_context = {"status_code": 400}

        normalized = ErrorNormalizer.normalize(
            error, channel="telegram", provider_context=provider_context
        )

        assert normalized.error_class == "validation_error"
        assert not normalized.retryable
        assert normalized.original_error is error
        assert normalized.provider_context == provider_context

    def test_normalize_validation_error_with_401_status(self):
        """Test that HTTP 401 maps to validation_error."""
        error = Exception("Unauthorized")
        provider_context = {"status_code": 401}

        normalized = ErrorNormalizer.normalize(error, provider_context=provider_context)

        assert normalized.error_class == "validation_error"
        assert not normalized.retryable

    def test_normalize_validation_error_with_403_status(self):
        """Test that HTTP 403 maps to validation_error."""
        error = Exception("Forbidden")
        provider_context = {"status_code": 403}

        normalized = ErrorNormalizer.normalize(error, provider_context=provider_context)

        assert normalized.error_class == "validation_error"
        assert not normalized.retryable

    def test_normalize_validation_error_with_valueerror(self):
        """Test that ValueError maps to validation_error."""
        error = ValueError("Missing required field: recipient")

        normalized = ErrorNormalizer.normalize(error)

        assert normalized.error_class == "validation_error"
        assert not normalized.retryable

    def test_normalize_timeout_error_with_timeout_exception(self):
        """Test that timeout exceptions map to timeout class."""

        class TimeoutException(Exception):
            pass

        error = TimeoutException("Request timed out after 30s")

        normalized = ErrorNormalizer.normalize(error)

        assert normalized.error_class == "timeout"
        assert normalized.retryable

    def test_normalize_timeout_error_with_message_keyword(self):
        """Test that timeout keyword in message maps to timeout class."""
        error = Exception("Operation timed out")

        normalized = ErrorNormalizer.normalize(error)

        assert normalized.error_class == "timeout"
        assert normalized.retryable

    def test_normalize_target_unavailable_with_429_status(self):
        """Test that HTTP 429 maps to target_unavailable."""
        error = Exception("Rate limit exceeded")
        provider_context = {"status_code": 429}

        normalized = ErrorNormalizer.normalize(error, provider_context=provider_context)

        assert normalized.error_class == "target_unavailable"
        assert normalized.retryable

    def test_normalize_target_unavailable_with_503_status(self):
        """Test that HTTP 503 maps to target_unavailable."""
        error = Exception("Service unavailable")
        provider_context = {"status_code": 503}

        normalized = ErrorNormalizer.normalize(error, provider_context=provider_context)

        assert normalized.error_class == "target_unavailable"
        assert normalized.retryable

    def test_normalize_target_unavailable_with_connection_error(self):
        """Test that connection errors map to target_unavailable."""

        class ConnectionError(Exception):
            pass

        error = ConnectionError("Failed to connect to server")

        normalized = ErrorNormalizer.normalize(error)

        assert normalized.error_class == "target_unavailable"
        assert normalized.retryable

    def test_normalize_overload_error_with_queue_full_message(self):
        """Test that queue full messages map to overload_rejected."""
        error = Exception("Queue full, admission rejected")

        normalized = ErrorNormalizer.normalize(error)

        assert normalized.error_class == "overload_rejected"
        assert normalized.retryable

    def test_normalize_internal_error_fallback(self):
        """Test that unmapped errors default to internal_error."""
        error = Exception("Something went wrong")

        normalized = ErrorNormalizer.normalize(error)

        assert normalized.error_class == "internal_error"
        assert not normalized.retryable

    def test_normalize_preserves_provider_context(self):
        """Test that provider context is preserved in normalized error."""
        error = ValueError("Invalid input")
        provider_context = {"status_code": 400, "headers": {"x-request-id": "abc123"}}

        normalized = ErrorNormalizer.normalize(error, provider_context=provider_context)

        assert normalized.provider_context == provider_context

    def test_normalize_with_channel_context(self):
        """Test that channel is accepted for context (not used in current impl)."""
        error = Exception("Test error")

        # Should not raise even with channel specified
        normalized = ErrorNormalizer.normalize(error, channel="telegram")

        assert normalized is not None

    def test_normalized_error_immutability(self):
        """Test that NormalizedError is frozen (immutable)."""
        normalized = NormalizedError(
            error_class="timeout",
            message="Request timed out",
            retryable=True,
        )

        with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
            normalized.error_class = "validation_error"
