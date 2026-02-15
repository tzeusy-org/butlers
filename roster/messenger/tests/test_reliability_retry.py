"""Tests for retry policy with exponential backoff and jitter."""

from __future__ import annotations

import pytest

from butlers.tools.messenger.reliability.errors import NormalizedError
from butlers.tools.messenger.reliability.retry import RetryPolicy, execute_with_retry


class TestRetryPolicy:
    """Test retry policy configuration and behavior."""

    def test_default_retry_policy(self):
        """Test default retry policy values."""
        policy = RetryPolicy()

        assert policy.max_attempts == 3
        assert policy.base_delay_seconds == 1.0
        assert policy.max_delay_seconds == 60.0
        assert policy.jitter_factor == 0.3
        assert policy.retry_timeout_errors is True
        assert policy.retry_target_unavailable is True
        assert policy.retry_overload is True

    def test_calculate_backoff_no_delay_for_first_attempt(self):
        """Test that first attempt has no backoff delay."""
        policy = RetryPolicy()

        delay = policy.calculate_backoff(attempt_number=1)

        assert delay == 0.0

    def test_calculate_backoff_exponential_growth(self):
        """Test that backoff grows exponentially."""
        policy = RetryPolicy(base_delay_seconds=1.0, jitter_factor=0.0)

        # Attempt 2: first retry, backoff = 1.0 * 2^0 = 1.0
        delay_2 = policy.calculate_backoff(attempt_number=2)
        assert 0.9 <= delay_2 <= 1.1  # Allow small float precision

        # Attempt 3: second retry, backoff = 1.0 * 2^1 = 2.0
        delay_3 = policy.calculate_backoff(attempt_number=3)
        assert 1.9 <= delay_3 <= 2.1

        # Attempt 4: third retry, backoff = 1.0 * 2^2 = 4.0
        delay_4 = policy.calculate_backoff(attempt_number=4)
        assert 3.9 <= delay_4 <= 4.1

    def test_calculate_backoff_respects_max_delay(self):
        """Test that backoff is capped at max_delay."""
        policy = RetryPolicy(base_delay_seconds=10.0, max_delay_seconds=20.0, jitter_factor=0.0)

        # Attempt 5: would be 10.0 * 2^3 = 80.0, but capped at 20.0
        delay = policy.calculate_backoff(attempt_number=5)

        assert delay <= 20.0

    def test_calculate_backoff_applies_jitter(self):
        """Test that jitter randomizes the backoff."""
        policy = RetryPolicy(base_delay_seconds=2.0, jitter_factor=0.3)

        # Run multiple times to check jitter variance
        delays = [policy.calculate_backoff(attempt_number=2) for _ in range(10)]

        # All delays should be within jitter range: 2.0 * (1 - 0.3) to 2.0 * (1 + 0.3)
        # = 1.4 to 2.6
        assert all(1.4 <= d <= 2.6 for d in delays)

        # Should not all be the same (probabilistically very unlikely)
        assert len(set(delays)) > 1

    def test_should_retry_validation_error_never_retries(self):
        """Test that validation errors are never retried."""
        policy = RetryPolicy()
        error = NormalizedError(
            error_class="validation_error",
            message="Invalid input",
            retryable=False,
        )

        should_retry = policy.should_retry(error, attempt_number=1)

        assert should_retry is False

    def test_should_retry_timeout_error_when_enabled(self):
        """Test that timeout errors are retried when policy allows."""
        policy = RetryPolicy(retry_timeout_errors=True)
        error = NormalizedError(
            error_class="timeout",
            message="Request timed out",
            retryable=True,
        )

        should_retry = policy.should_retry(error, attempt_number=1)

        assert should_retry is True

    def test_should_retry_timeout_error_when_disabled(self):
        """Test that timeout errors are not retried when policy disables."""
        policy = RetryPolicy(retry_timeout_errors=False)
        error = NormalizedError(
            error_class="timeout",
            message="Request timed out",
            retryable=True,
        )

        should_retry = policy.should_retry(error, attempt_number=1)

        assert should_retry is False

    def test_should_retry_target_unavailable_when_enabled(self):
        """Test that target_unavailable errors are retried when policy allows."""
        policy = RetryPolicy(retry_target_unavailable=True)
        error = NormalizedError(
            error_class="target_unavailable",
            message="Service unavailable",
            retryable=True,
        )

        should_retry = policy.should_retry(error, attempt_number=1)

        assert should_retry is True

    def test_should_retry_overload_when_enabled(self):
        """Test that overload errors are retried when policy allows."""
        policy = RetryPolicy(retry_overload=True)
        error = NormalizedError(
            error_class="overload_rejected",
            message="Queue full",
            retryable=True,
        )

        should_retry = policy.should_retry(error, attempt_number=1)

        assert should_retry is True

    def test_should_retry_stops_at_max_attempts(self):
        """Test that retries stop at max_attempts."""
        policy = RetryPolicy(max_attempts=3)
        error = NormalizedError(
            error_class="timeout",
            message="Request timed out",
            retryable=True,
        )

        # Attempt 1 and 2 should allow retry
        assert policy.should_retry(error, attempt_number=1) is True
        assert policy.should_retry(error, attempt_number=2) is True

        # Attempt 3 is the last attempt, no more retries
        assert policy.should_retry(error, attempt_number=3) is False

    def test_should_retry_internal_error_never_retries(self):
        """Test that internal errors are never retried."""
        policy = RetryPolicy()
        error = NormalizedError(
            error_class="internal_error",
            message="Internal server error",
            retryable=False,
        )

        should_retry = policy.should_retry(error, attempt_number=1)

        assert should_retry is False

    def test_from_config_with_overrides(self):
        """Test creating RetryPolicy from config dict."""
        config_dict = {
            "max_attempts": 5,
            "base_delay_seconds": 2.0,
            "max_delay_seconds": 120.0,
            "jitter_factor": 0.5,
            "retry_timeout_errors": False,
        }

        policy = RetryPolicy.from_config(config_dict)

        assert policy.max_attempts == 5
        assert policy.base_delay_seconds == 2.0
        assert policy.max_delay_seconds == 120.0
        assert policy.jitter_factor == 0.5
        assert policy.retry_timeout_errors is False
        # Unspecified fields use defaults
        assert policy.retry_target_unavailable is True

    def test_from_config_empty_dict_uses_defaults(self):
        """Test that empty config dict uses all defaults."""
        policy = RetryPolicy.from_config({})

        assert policy.max_attempts == 3
        assert policy.base_delay_seconds == 1.0
        assert policy.max_delay_seconds == 60.0


class TestExecuteWithRetry:
    """Test retry execution wrapper."""

    async def test_execute_with_retry_succeeds_on_first_attempt(self):
        """Test that successful operation returns immediately."""
        call_count = 0

        async def operation():
            nonlocal call_count
            call_count += 1
            return "success"

        policy = RetryPolicy()

        result = await execute_with_retry(operation, retry_policy=policy)

        assert result == "success"
        assert call_count == 1

    async def test_execute_with_retry_retries_on_retryable_error(self):
        """Test that retryable errors trigger retries."""
        call_count = 0

        async def operation():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("Service unavailable")
            return "success"

        policy = RetryPolicy(max_attempts=3, base_delay_seconds=0.01)

        result = await execute_with_retry(operation, retry_policy=policy, channel="telegram")

        assert result == "success"
        assert call_count == 3

    async def test_execute_with_retry_fails_fast_on_validation_error(self):
        """Test that validation errors fail fast without retries."""
        call_count = 0

        async def operation():
            nonlocal call_count
            call_count += 1
            raise ValueError("Invalid input")

        policy = RetryPolicy(max_attempts=3)

        with pytest.raises(ValueError, match="Invalid input"):
            await execute_with_retry(operation, retry_policy=policy)

        # Should fail on first attempt
        assert call_count == 1

    async def test_execute_with_retry_exhausts_retries(self):
        """Test that retries are exhausted for persistent errors."""
        call_count = 0

        async def operation():
            nonlocal call_count
            call_count += 1
            raise Exception("Service unavailable")

        policy = RetryPolicy(max_attempts=3, base_delay_seconds=0.01)

        with pytest.raises(Exception, match="Service unavailable"):
            await execute_with_retry(operation, retry_policy=policy, channel="telegram")

        # Should attempt 3 times
        assert call_count == 3

    async def test_execute_with_retry_applies_backoff(self):
        """Test that backoff delay is applied between retries."""
        import time

        call_times = []

        async def operation():
            call_times.append(time.time())
            if len(call_times) < 3:
                raise Exception("Service unavailable")
            return "success"

        policy = RetryPolicy(
            max_attempts=3,
            base_delay_seconds=0.1,
            jitter_factor=0.0,  # No jitter for predictable timing
        )

        await execute_with_retry(operation, retry_policy=policy, channel="telegram")

        # Check that there was delay between attempts
        assert len(call_times) == 3

        # First retry should have ~0.1s delay
        delay_1 = call_times[1] - call_times[0]
        assert 0.08 <= delay_1 <= 0.15

        # Second retry should have ~0.2s delay
        delay_2 = call_times[2] - call_times[1]
        assert 0.18 <= delay_2 <= 0.25

    async def test_execute_with_retry_includes_operation_context(self):
        """Test that operation context is passed for error normalization."""

        async def operation():
            raise Exception("Test error")

        policy = RetryPolicy(max_attempts=1)
        context = {"request_id": "test-123"}

        with pytest.raises(Exception):
            await execute_with_retry(
                operation,
                retry_policy=policy,
                channel="telegram",
                operation_context=context,
            )
