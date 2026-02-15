"""Tests for rate limiter, backpressure, and admission control."""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Add roster to path for importing messenger tools
roster_path = Path(__file__).parent.parent.parent
if str(roster_path) not in sys.path:
    sys.path.insert(0, str(roster_path))

from messenger.tools.rate_limiter import (  # noqa: E402
    RateLimitBucket,
    RateLimitConfig,
    RateLimiter,
)


class TestRateLimitBucket:
    """Test token bucket implementation."""

    def test_initial_state(self):
        """Test bucket starts at full capacity."""
        bucket = RateLimitBucket(
            capacity=10,
            tokens=10.0,
            refill_rate=1.0,
            last_refill=datetime.now(UTC),
        )

        assert bucket.capacity == 10
        assert bucket.tokens == 10.0
        assert bucket.available() == 10

    def test_consume_tokens(self):
        """Test consuming tokens."""
        bucket = RateLimitBucket(
            capacity=10,
            tokens=10.0,
            refill_rate=1.0,
            last_refill=datetime.now(UTC),
        )

        # Consume 3 tokens
        assert bucket.consume(3) is True
        assert bucket.available() == 7

        # Consume 5 more
        assert bucket.consume(5) is True
        assert bucket.available() == 2

    def test_consume_insufficient_tokens(self):
        """Test consuming more tokens than available."""
        bucket = RateLimitBucket(
            capacity=10,
            tokens=5.0,
            refill_rate=1.0,
            last_refill=datetime.now(UTC),
        )

        # Try to consume 6 tokens (only 5 available)
        assert bucket.consume(6) is False
        # Tokens should remain unchanged
        assert bucket.available() == 5

    async def test_refill_over_time(self):
        """Test bucket refills over time."""
        bucket = RateLimitBucket(
            capacity=10,
            tokens=5.0,
            refill_rate=2.0,  # 2 tokens per second
            last_refill=datetime.now(UTC),
        )

        # Wait 2 seconds
        await asyncio.sleep(2.1)

        # Should have ~4 more tokens (2 tokens/sec * 2 sec)
        assert bucket.available() >= 9

    def test_refill_caps_at_capacity(self):
        """Test refill doesn't exceed capacity."""
        # Create bucket that should have refilled past capacity
        past = datetime.now(UTC) - timedelta(seconds=100)
        bucket = RateLimitBucket(
            capacity=10,
            tokens=5.0,
            refill_rate=1.0,
            last_refill=past,
        )

        # Refill should cap at capacity
        bucket.refill()
        assert bucket.available() == 10

    def test_time_until_available(self):
        """Test time calculation until tokens available."""
        bucket = RateLimitBucket(
            capacity=10,
            tokens=2.0,
            refill_rate=1.0,  # 1 token per second
            last_refill=datetime.now(UTC),
        )

        # Need 5 tokens, have 2, need 3 more
        # At 1 token/sec, should take 3 seconds
        wait_time = bucket.time_until_available(5)
        assert 2.9 <= wait_time <= 3.1  # Allow small timing variance

    def test_time_until_available_immediate(self):
        """Test time is zero when tokens already available."""
        bucket = RateLimitBucket(
            capacity=10,
            tokens=10.0,
            refill_rate=1.0,
            last_refill=datetime.now(UTC),
        )

        assert bucket.time_until_available(5) == 0.0


class TestRateLimitConfig:
    """Test rate limit configuration."""

    def test_default_config(self):
        """Test default configuration values."""
        config = RateLimitConfig()

        assert config.global_max_per_minute == 60
        assert config.global_max_in_flight == 100
        assert config.per_recipient_max_per_minute == 10
        assert config.reply_priority_multiplier == 2.0

        # Should have default channel limits
        assert "telegram.bot" in config.channel_limits
        assert "email.bot" in config.channel_limits

    def test_custom_config(self):
        """Test custom configuration."""
        config = RateLimitConfig(
            global_max_per_minute=100,
            global_max_in_flight=50,
            per_recipient_max_per_minute=5,
            channel_limits={"custom.bot": 25},
        )

        assert config.global_max_per_minute == 100
        assert config.global_max_in_flight == 50
        assert config.per_recipient_max_per_minute == 5
        assert config.channel_limits == {"custom.bot": 25}


class TestGlobalRateLimits:
    """Test global rate limiting."""

    async def test_global_in_flight_limit(self):
        """Test global in-flight limit enforcement."""
        config = RateLimitConfig(
            global_max_in_flight=2,
            global_max_per_minute=100,
        )
        limiter = RateLimiter(config)

        # First two should be admitted
        result1 = await limiter.check_admission(
            channel="telegram",
            identity_scope="bot",
            recipient="user1",
            intent="send",
            origin_butler="health",
        )
        assert result1.admitted is True

        result2 = await limiter.check_admission(
            channel="telegram",
            identity_scope="bot",
            recipient="user2",
            intent="send",
            origin_butler="health",
        )
        assert result2.admitted is True

        # Third should be rejected (in-flight limit)
        result3 = await limiter.check_admission(
            channel="telegram",
            identity_scope="bot",
            recipient="user3",
            intent="send",
            origin_butler="health",
        )
        assert result3.admitted is False
        assert result3.error_class == "overload_rejected"
        assert result3.limit_type == "global_in_flight"

        # Release one
        await limiter.release(
            channel="telegram",
            identity_scope="bot",
            recipient="user1",
        )

        # Now should be admitted
        result4 = await limiter.check_admission(
            channel="telegram",
            identity_scope="bot",
            recipient="user4",
            intent="send",
            origin_butler="health",
        )
        assert result4.admitted is True

    async def test_global_rate_limit(self):
        """Test global rate limit enforcement."""
        config = RateLimitConfig(
            global_max_per_minute=2,  # Very low for testing
            global_max_in_flight=100,
        )
        limiter = RateLimiter(config)

        # First two should be admitted
        result1 = await limiter.check_admission(
            channel="telegram",
            identity_scope="bot",
            recipient="user1",
            intent="send",
            origin_butler="health",
        )
        assert result1.admitted is True
        await limiter.release(channel="telegram", identity_scope="bot", recipient="user1")

        result2 = await limiter.check_admission(
            channel="telegram",
            identity_scope="bot",
            recipient="user2",
            intent="send",
            origin_butler="health",
        )
        assert result2.admitted is True
        await limiter.release(channel="telegram", identity_scope="bot", recipient="user2")

        # Third should be rejected (rate limit)
        result3 = await limiter.check_admission(
            channel="telegram",
            identity_scope="bot",
            recipient="user3",
            intent="send",
            origin_butler="health",
        )
        assert result3.admitted is False
        assert result3.error_class == "overload_rejected"
        assert result3.limit_type == "global"
        assert result3.retry_after_seconds is not None


class TestChannelRateLimits:
    """Test per-channel+identity rate limiting."""

    async def test_channel_rate_limit(self):
        """Test per-channel rate limit enforcement."""
        config = RateLimitConfig(
            global_max_per_minute=100,
            channel_limits={"telegram.bot": 2},  # Low limit for testing
        )
        limiter = RateLimiter(config)

        # First two on telegram.bot should be admitted
        result1 = await limiter.check_admission(
            channel="telegram",
            identity_scope="bot",
            recipient="user1",
            intent="send",
            origin_butler="health",
        )
        assert result1.admitted is True
        await limiter.release(channel="telegram", identity_scope="bot", recipient="user1")

        result2 = await limiter.check_admission(
            channel="telegram",
            identity_scope="bot",
            recipient="user2",
            intent="send",
            origin_butler="health",
        )
        assert result2.admitted is True
        await limiter.release(channel="telegram", identity_scope="bot", recipient="user2")

        # Third should be rejected (channel rate limit)
        result3 = await limiter.check_admission(
            channel="telegram",
            identity_scope="bot",
            recipient="user3",
            intent="send",
            origin_butler="health",
        )
        assert result3.admitted is False
        assert result3.error_class == "overload_rejected"
        assert result3.limit_type == "channel"
        assert "telegram.bot" in result3.error_message

    async def test_channel_isolation(self):
        """Test that different channels have independent limits."""
        config = RateLimitConfig(
            global_max_per_minute=100,
            channel_limits={
                "telegram.bot": 2,
                "email.bot": 2,
            },
        )
        limiter = RateLimiter(config)

        # Use up telegram.bot quota
        for i in range(2):
            result = await limiter.check_admission(
                channel="telegram",
                identity_scope="bot",
                recipient=f"user{i}",
                intent="send",
                origin_butler="health",
            )
            assert result.admitted is True
            await limiter.release(channel="telegram", identity_scope="bot", recipient=f"user{i}")

        # Telegram should be exhausted
        result = await limiter.check_admission(
            channel="telegram",
            identity_scope="bot",
            recipient="user_extra",
            intent="send",
            origin_butler="health",
        )
        assert result.admitted is False

        # But email should still work
        result = await limiter.check_admission(
            channel="email",
            identity_scope="bot",
            recipient="email_user",
            intent="send",
            origin_butler="health",
        )
        assert result.admitted is True


class TestRecipientRateLimits:
    """Test per-recipient anti-flood rate limiting."""

    async def test_recipient_rate_limit(self):
        """Test per-recipient anti-flood enforcement."""
        config = RateLimitConfig(
            global_max_per_minute=100,
            per_recipient_max_per_minute=2,  # Low limit for testing
        )
        limiter = RateLimiter(config)

        # First two to same recipient should be admitted
        result1 = await limiter.check_admission(
            channel="telegram",
            identity_scope="bot",
            recipient="user123",
            intent="send",
            origin_butler="health",
        )
        assert result1.admitted is True
        await limiter.release(channel="telegram", identity_scope="bot", recipient="user123")

        result2 = await limiter.check_admission(
            channel="telegram",
            identity_scope="bot",
            recipient="user123",
            intent="send",
            origin_butler="relationship",
        )
        assert result2.admitted is True
        await limiter.release(channel="telegram", identity_scope="bot", recipient="user123")

        # Third to same recipient should be rejected
        result3 = await limiter.check_admission(
            channel="telegram",
            identity_scope="bot",
            recipient="user123",
            intent="send",
            origin_butler="general",
        )
        assert result3.admitted is False
        assert result3.error_class == "overload_rejected"
        assert result3.limit_type == "recipient"
        assert "user123" in result3.error_message

    async def test_recipient_isolation(self):
        """Test that different recipients have independent limits."""
        config = RateLimitConfig(
            global_max_per_minute=100,
            per_recipient_max_per_minute=1,
        )
        limiter = RateLimiter(config)

        # Use up quota for user1
        result = await limiter.check_admission(
            channel="telegram",
            identity_scope="bot",
            recipient="user1",
            intent="send",
            origin_butler="health",
        )
        assert result.admitted is True
        await limiter.release(channel="telegram", identity_scope="bot", recipient="user1")

        # user1 exhausted
        result = await limiter.check_admission(
            channel="telegram",
            identity_scope="bot",
            recipient="user1",
            intent="send",
            origin_butler="health",
        )
        assert result.admitted is False

        # But user2 should still work
        result = await limiter.check_admission(
            channel="telegram",
            identity_scope="bot",
            recipient="user2",
            intent="send",
            origin_butler="health",
        )
        assert result.admitted is True


class TestReplyPriority:
    """Test reply priority (replies get higher quota)."""

    async def test_reply_costs_less(self):
        """Test that replies consume fewer tokens."""
        config = RateLimitConfig(
            global_max_per_minute=3,  # 3 tokens
            reply_priority_multiplier=2.0,  # Replies cost 0.5 tokens
        )
        limiter = RateLimiter(config)

        # Send consumes 1 token (2 tokens left)
        result1 = await limiter.check_admission(
            channel="telegram",
            identity_scope="bot",
            recipient="user1",
            intent="send",
            origin_butler="health",
        )
        assert result1.admitted is True
        await limiter.release(channel="telegram", identity_scope="bot", recipient="user1")

        # Reply consumes 0.5 tokens (1.5 tokens left)
        result2 = await limiter.check_admission(
            channel="telegram",
            identity_scope="bot",
            recipient="user2",
            intent="reply",
            origin_butler="health",
        )
        assert result2.admitted is True
        await limiter.release(channel="telegram", identity_scope="bot", recipient="user2")

        # Another reply consumes 0.5 tokens (1 token left)
        result3 = await limiter.check_admission(
            channel="telegram",
            identity_scope="bot",
            recipient="user3",
            intent="reply",
            origin_butler="health",
        )
        assert result3.admitted is True
        await limiter.release(channel="telegram", identity_scope="bot", recipient="user3")

        # Another reply consumes 0.5 tokens (0.5 tokens left)
        result4 = await limiter.check_admission(
            channel="telegram",
            identity_scope="bot",
            recipient="user4",
            intent="reply",
            origin_butler="health",
        )
        assert result4.admitted is True
        await limiter.release(channel="telegram", identity_scope="bot", recipient="user4")

        # Send should fail (need 1 token, only 0.5 left)
        result5 = await limiter.check_admission(
            channel="telegram",
            identity_scope="bot",
            recipient="user5",
            intent="send",
            origin_butler="health",
        )
        assert result5.admitted is False


class TestProviderThrottle:
    """Test provider throttle handling (Retry-After honor)."""

    async def test_provider_throttle_blocks_admission(self):
        """Test that provider throttle blocks admissions."""
        config = RateLimitConfig()
        limiter = RateLimiter(config)

        # Record provider throttle
        await limiter.record_provider_throttle(
            channel="telegram",
            retry_after_seconds=5.0,
            reason="Rate limit exceeded",
        )

        # Admission should be blocked
        result = await limiter.check_admission(
            channel="telegram",
            identity_scope="bot",
            recipient="user1",
            intent="send",
            origin_butler="health",
        )
        assert result.admitted is False
        assert result.error_class == "target_unavailable"
        assert result.limit_type == "provider"
        assert result.retry_after_seconds is not None
        assert result.retry_after_seconds <= 5.0

    async def test_provider_throttle_expires(self):
        """Test that provider throttle expires after retry period."""
        config = RateLimitConfig()
        limiter = RateLimiter(config)

        # Record very short throttle
        await limiter.record_provider_throttle(
            channel="telegram",
            retry_after_seconds=0.1,
            reason="Rate limit exceeded",
        )

        # Should be blocked initially
        result = await limiter.check_admission(
            channel="telegram",
            identity_scope="bot",
            recipient="user1",
            intent="send",
            origin_butler="health",
        )
        assert result.admitted is False

        # Wait for throttle to expire
        await asyncio.sleep(0.2)

        # Should be admitted now
        result = await limiter.check_admission(
            channel="telegram",
            identity_scope="bot",
            recipient="user2",
            intent="send",
            origin_butler="health",
        )
        assert result.admitted is True

    async def test_provider_throttle_channel_isolation(self):
        """Test that provider throttle is channel-specific."""
        config = RateLimitConfig()
        limiter = RateLimiter(config)

        # Throttle telegram only
        await limiter.record_provider_throttle(
            channel="telegram",
            retry_after_seconds=10.0,
            reason="Rate limit exceeded",
        )

        # Telegram should be blocked
        result = await limiter.check_admission(
            channel="telegram",
            identity_scope="bot",
            recipient="user1",
            intent="send",
            origin_butler="health",
        )
        assert result.admitted is False

        # Email should still work
        result = await limiter.check_admission(
            channel="email",
            identity_scope="bot",
            recipient="user2",
            intent="send",
            origin_butler="health",
        )
        assert result.admitted is True

    async def test_clear_provider_throttle(self):
        """Test manual throttle clearing."""
        config = RateLimitConfig()
        limiter = RateLimiter(config)

        # Record throttle
        await limiter.record_provider_throttle(
            channel="telegram",
            retry_after_seconds=60.0,
            reason="Rate limit exceeded",
        )

        # Should be blocked
        result = await limiter.check_admission(
            channel="telegram",
            identity_scope="bot",
            recipient="user1",
            intent="send",
            origin_butler="health",
        )
        assert result.admitted is False

        # Clear throttle
        await limiter.clear_provider_throttle("telegram")

        # Should work now
        result = await limiter.check_admission(
            channel="telegram",
            identity_scope="bot",
            recipient="user2",
            intent="send",
            origin_butler="health",
        )
        assert result.admitted is True


class TestRateLimitStatus:
    """Test rate limit status reporting."""

    async def test_get_status(self):
        """Test getting overall rate limit status."""
        config = RateLimitConfig()
        limiter = RateLimiter(config)

        # Admit a few deliveries
        await limiter.check_admission(
            channel="telegram",
            identity_scope="bot",
            recipient="user1",
            intent="send",
            origin_butler="health",
        )
        await limiter.check_admission(
            channel="email",
            identity_scope="bot",
            recipient="user2",
            intent="send",
            origin_butler="relationship",
        )

        status = await limiter.get_status()

        # Should have global status
        assert "global" in status
        assert status["global"]["in_flight"] == 2
        assert status["global"]["max_in_flight"] == config.global_max_in_flight

        # Should have channel status
        assert "channels" in status
        assert "telegram.bot" in status["channels"]
        assert "email.bot" in status["channels"]

    async def test_get_status_filtered(self):
        """Test getting status filtered by channel."""
        config = RateLimitConfig()
        limiter = RateLimiter(config)

        await limiter.check_admission(
            channel="telegram",
            identity_scope="bot",
            recipient="user1",
            intent="send",
            origin_butler="health",
        )

        status = await limiter.get_status(channel="telegram")

        # Should only have telegram channels
        assert "channels" in status
        assert "telegram.bot" in status["channels"]
        assert "email.bot" not in status["channels"]

    async def test_get_status_with_throttles(self):
        """Test status includes provider throttles."""
        config = RateLimitConfig()
        limiter = RateLimiter(config)

        await limiter.record_provider_throttle(
            channel="telegram",
            retry_after_seconds=10.0,
            reason="Rate limit exceeded",
        )

        status = await limiter.get_status()

        # Should include throttle info
        assert "provider_throttles" in status
        assert "telegram" in status["provider_throttles"]
        assert status["provider_throttles"]["telegram"]["reason"] == "Rate limit exceeded"
        assert status["provider_throttles"]["telegram"]["retry_after_seconds"] <= 10.0


class TestFairness:
    """Test fairness across origins and recipients."""

    async def test_fairness_across_origins(self):
        """Test that one noisy origin doesn't starve others."""
        config = RateLimitConfig(
            global_max_per_minute=100,
            per_recipient_max_per_minute=2,
        )
        limiter = RateLimiter(config)

        # Noisy origin exhausts quota for recipient1
        for i in range(2):
            result = await limiter.check_admission(
                channel="telegram",
                identity_scope="bot",
                recipient="recipient1",
                intent="send",
                origin_butler="noisy_butler",
            )
            assert result.admitted is True
            await limiter.release(channel="telegram", identity_scope="bot", recipient="recipient1")

        # Noisy origin blocked for recipient1
        result = await limiter.check_admission(
            channel="telegram",
            identity_scope="bot",
            recipient="recipient1",
            intent="send",
            origin_butler="noisy_butler",
        )
        assert result.admitted is False

        # But quiet origin can still reach different recipient
        result = await limiter.check_admission(
            channel="telegram",
            identity_scope="bot",
            recipient="recipient2",
            intent="send",
            origin_butler="quiet_butler",
        )
        assert result.admitted is True


class TestEdgeCases:
    """Test edge cases and error conditions."""

    async def test_concurrent_admission_checks(self):
        """Test concurrent admission checks don't cause races."""
        config = RateLimitConfig(
            global_max_per_minute=10,
        )
        limiter = RateLimiter(config)

        # Run 5 concurrent admission checks
        tasks = [
            limiter.check_admission(
                channel="telegram",
                identity_scope="bot",
                recipient=f"user{i}",
                intent="send",
                origin_butler="health",
            )
            for i in range(5)
        ]

        results = await asyncio.gather(*tasks)

        # All should be admitted (under global limit)
        assert all(r.admitted for r in results)

    async def test_release_without_admission(self):
        """Test that release without admission doesn't cause issues."""
        config = RateLimitConfig()
        limiter = RateLimiter(config)

        # Release without admission (should not raise)
        await limiter.release(
            channel="telegram",
            identity_scope="bot",
            recipient="user1",
        )

        # Should still work normally
        result = await limiter.check_admission(
            channel="telegram",
            identity_scope="bot",
            recipient="user1",
            intent="send",
            origin_butler="health",
        )
        assert result.admitted is True
