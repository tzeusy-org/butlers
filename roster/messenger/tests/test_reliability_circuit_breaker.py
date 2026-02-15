"""Tests for circuit breaker implementation."""

from __future__ import annotations

import asyncio

import pytest

from butlers.tools.messenger.reliability.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitOpenError,
    CircuitState,
)


class TestCircuitBreakerConfig:
    """Test circuit breaker configuration."""

    def test_default_config(self):
        """Test default circuit breaker config values."""
        config = CircuitBreakerConfig()

        assert config.failure_threshold == 5
        assert config.recovery_timeout_seconds == 60.0
        assert config.half_open_max_attempts == 3
        assert config.half_open_success_threshold == 2
        assert config.count_timeout_as_failure is True
        assert config.count_target_unavailable_as_failure is True

    def test_from_config_with_overrides(self):
        """Test creating config from dict."""
        config_dict = {
            "failure_threshold": 3,
            "recovery_timeout_seconds": 30.0,
            "half_open_max_attempts": 2,
            "count_timeout_as_failure": False,
        }

        config = CircuitBreakerConfig.from_config(config_dict)

        assert config.failure_threshold == 3
        assert config.recovery_timeout_seconds == 30.0
        assert config.half_open_max_attempts == 2
        assert config.count_timeout_as_failure is False
        # Unspecified fields use defaults
        assert config.half_open_success_threshold == 2


class TestCircuitBreaker:
    """Test circuit breaker state transitions and behavior."""

    async def test_initial_state_is_closed(self):
        """Test that circuit breaker starts in CLOSED state."""
        breaker = CircuitBreaker(provider="telegram")

        assert breaker.state == CircuitState.CLOSED
        assert not breaker.is_open

    async def test_successful_operation_in_closed_state(self):
        """Test that successful operations pass through in CLOSED state."""
        breaker = CircuitBreaker(provider="telegram")

        async def operation():
            return "success"

        result = await breaker.execute(operation)

        assert result == "success"
        assert breaker.state == CircuitState.CLOSED

    async def test_single_failure_does_not_open_circuit(self):
        """Test that a single failure doesn't open the circuit."""
        config = CircuitBreakerConfig(failure_threshold=3)
        breaker = CircuitBreaker(provider="telegram", config=config)

        async def operation():
            raise Exception("Service unavailable")

        with pytest.raises(Exception):
            await breaker.execute(operation)

        assert breaker.state == CircuitState.CLOSED

    async def test_consecutive_failures_open_circuit(self):
        """Test that consecutive failures open the circuit."""
        config = CircuitBreakerConfig(failure_threshold=3)
        breaker = CircuitBreaker(provider="telegram", config=config)

        async def operation():
            raise Exception("Service unavailable")

        # First 2 failures should keep circuit closed
        with pytest.raises(Exception):
            await breaker.execute(operation)
        with pytest.raises(Exception):
            await breaker.execute(operation)

        assert breaker.state == CircuitState.CLOSED

        # Third failure should open circuit
        with pytest.raises(Exception):
            await breaker.execute(operation)

        assert breaker.state == CircuitState.OPEN
        assert breaker.is_open

    async def test_open_circuit_rejects_operations_immediately(self):
        """Test that OPEN circuit rejects operations without executing them."""
        config = CircuitBreakerConfig(failure_threshold=2)
        breaker = CircuitBreaker(provider="telegram", config=config)

        call_count = 0

        async def operation():
            nonlocal call_count
            call_count += 1
            raise Exception("Service unavailable")

        # Open the circuit with 2 failures
        with pytest.raises(Exception):
            await breaker.execute(operation)
        with pytest.raises(Exception):
            await breaker.execute(operation)

        assert breaker.state == CircuitState.OPEN
        assert call_count == 2

        # Next call should be rejected without executing operation
        with pytest.raises(CircuitOpenError):
            await breaker.execute(operation)

        assert call_count == 2  # Operation was not called

    async def test_circuit_transitions_to_half_open_after_timeout(self):
        """Test that OPEN circuit transitions to HALF_OPEN after recovery timeout."""
        config = CircuitBreakerConfig(
            failure_threshold=2,
            recovery_timeout_seconds=0.1,  # Very short for testing
        )
        breaker = CircuitBreaker(provider="telegram", config=config)

        async def failing_operation():
            raise Exception("Service unavailable")

        # Open the circuit
        with pytest.raises(Exception):
            await breaker.execute(failing_operation)
        with pytest.raises(Exception):
            await breaker.execute(failing_operation)

        assert breaker.state == CircuitState.OPEN

        # Wait for recovery timeout
        await asyncio.sleep(0.15)

        # Next execution should transition to HALF_OPEN and execute
        # (even though it fails)
        with pytest.raises(Exception):
            await breaker.execute(failing_operation)

        # Should now be in HALF_OPEN state
        assert breaker.state == CircuitState.HALF_OPEN

    async def test_half_open_successful_requests_close_circuit(self):
        """Test that successful requests in HALF_OPEN close the circuit."""
        config = CircuitBreakerConfig(
            failure_threshold=2,
            recovery_timeout_seconds=0.1,
            half_open_success_threshold=2,
        )
        breaker = CircuitBreaker(provider="telegram", config=config)

        async def failing_operation():
            raise Exception("Service unavailable")

        async def successful_operation():
            return "success"

        # Open the circuit
        with pytest.raises(Exception):
            await breaker.execute(failing_operation)
        with pytest.raises(Exception):
            await breaker.execute(failing_operation)

        assert breaker.state == CircuitState.OPEN

        # Wait for recovery timeout
        await asyncio.sleep(0.15)

        # Transition to HALF_OPEN with first successful request
        result1 = await breaker.execute(successful_operation)
        assert result1 == "success"
        assert breaker.state == CircuitState.HALF_OPEN

        # Second success should close the circuit
        result2 = await breaker.execute(successful_operation)
        assert result2 == "success"
        assert breaker.state == CircuitState.CLOSED

    async def test_half_open_failures_reopen_circuit(self):
        """Test that failures in HALF_OPEN re-open the circuit."""
        config = CircuitBreakerConfig(
            failure_threshold=2,
            recovery_timeout_seconds=0.1,
            half_open_max_attempts=2,
        )
        breaker = CircuitBreaker(provider="telegram", config=config)

        async def failing_operation():
            raise Exception("Service unavailable")

        # Open the circuit
        with pytest.raises(Exception):
            await breaker.execute(failing_operation)
        with pytest.raises(Exception):
            await breaker.execute(failing_operation)

        assert breaker.state == CircuitState.OPEN

        # Wait for recovery timeout
        await asyncio.sleep(0.15)

        # First failure in HALF_OPEN
        with pytest.raises(Exception):
            await breaker.execute(failing_operation)

        assert breaker.state == CircuitState.HALF_OPEN

        # Second failure should re-open circuit
        with pytest.raises(Exception):
            await breaker.execute(failing_operation)

        assert breaker.state == CircuitState.OPEN

    async def test_validation_errors_do_not_count_toward_circuit_trip(self):
        """Test that validation errors don't count toward circuit trip."""
        config = CircuitBreakerConfig(failure_threshold=2)
        breaker = CircuitBreaker(provider="telegram", config=config)

        async def validation_error_operation():
            raise ValueError("Invalid input")

        # Multiple validation errors should not open circuit
        with pytest.raises(ValueError):
            await breaker.execute(validation_error_operation)
        with pytest.raises(ValueError):
            await breaker.execute(validation_error_operation)
        with pytest.raises(ValueError):
            await breaker.execute(validation_error_operation)

        assert breaker.state == CircuitState.CLOSED

    async def test_timeout_errors_count_when_configured(self):
        """Test that timeout errors count toward circuit trip when configured."""
        config = CircuitBreakerConfig(failure_threshold=2, count_timeout_as_failure=True)
        breaker = CircuitBreaker(provider="telegram", config=config)

        class TimeoutError(Exception):
            pass

        async def timeout_operation():
            raise TimeoutError("Request timed out")

        # Timeout errors should count
        with pytest.raises(TimeoutError):
            await breaker.execute(timeout_operation)
        with pytest.raises(TimeoutError):
            await breaker.execute(timeout_operation)

        assert breaker.state == CircuitState.OPEN

    async def test_timeout_errors_do_not_count_when_disabled(self):
        """Test that timeout errors don't count when disabled."""
        config = CircuitBreakerConfig(failure_threshold=2, count_timeout_as_failure=False)
        breaker = CircuitBreaker(provider="telegram", config=config)

        class TimeoutError(Exception):
            pass

        async def timeout_operation():
            raise TimeoutError("Request timed out")

        # Timeout errors should not count
        with pytest.raises(TimeoutError):
            await breaker.execute(timeout_operation)
        with pytest.raises(TimeoutError):
            await breaker.execute(timeout_operation)
        with pytest.raises(TimeoutError):
            await breaker.execute(timeout_operation)

        assert breaker.state == CircuitState.CLOSED

    async def test_get_status_returns_circuit_state(self):
        """Test that get_status returns current circuit state."""
        config = CircuitBreakerConfig(failure_threshold=2)
        breaker = CircuitBreaker(provider="telegram", config=config)

        status = breaker.get_status()

        assert status["provider"] == "telegram"
        assert status["state"] == "closed"
        assert status["consecutive_failures"] == 0
        assert status["opened_at"] is None
        assert status["config"]["failure_threshold"] == 2

    async def test_get_status_includes_error_info_after_failure(self):
        """Test that status includes error info after failures."""
        config = CircuitBreakerConfig(failure_threshold=2)
        breaker = CircuitBreaker(provider="telegram", config=config)

        async def operation():
            raise Exception("Service unavailable")

        with pytest.raises(Exception):
            await breaker.execute(operation)

        status = breaker.get_status()

        assert status["consecutive_failures"] == 1
        assert status["last_error_class"] == "target_unavailable"
        assert "unavailable" in status["last_error_message"].lower()

    async def test_circuit_open_error_includes_context(self):
        """Test that CircuitOpenError includes provider and timing context."""
        config = CircuitBreakerConfig(failure_threshold=2)
        breaker = CircuitBreaker(provider="telegram", config=config)

        async def operation():
            raise Exception("Service unavailable")

        # Open the circuit
        with pytest.raises(Exception):
            await breaker.execute(operation)
        with pytest.raises(Exception):
            await breaker.execute(operation)

        # Try to execute when circuit is open
        try:
            await breaker.execute(operation)
            assert False, "Should have raised CircuitOpenError"
        except CircuitOpenError as exc:
            assert exc.provider == "telegram"
            assert exc.opened_at is not None
            assert exc.last_error_class == "target_unavailable"

    async def test_success_resets_failure_counter_in_closed_state(self):
        """Test that successful operations reset failure counter."""
        config = CircuitBreakerConfig(failure_threshold=3)
        breaker = CircuitBreaker(provider="telegram", config=config)

        async def failing_operation():
            raise Exception("Service unavailable")

        async def successful_operation():
            return "success"

        # Two failures
        with pytest.raises(Exception):
            await breaker.execute(failing_operation)
        with pytest.raises(Exception):
            await breaker.execute(failing_operation)

        # Success should reset counter
        await breaker.execute(successful_operation)

        # Now need 3 more failures to open
        with pytest.raises(Exception):
            await breaker.execute(failing_operation)
        with pytest.raises(Exception):
            await breaker.execute(failing_operation)

        assert breaker.state == CircuitState.CLOSED

        # Third failure opens circuit
        with pytest.raises(Exception):
            await breaker.execute(failing_operation)

        assert breaker.state == CircuitState.OPEN
