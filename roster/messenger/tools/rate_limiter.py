"""Rate limiting, backpressure, and admission control for Messenger butler.

Implements the rate-limit contract from docs/roles/messenger_butler.md section 8:
- Three-layer limits (global, per-channel+identity, per-recipient)
- Reply priority (replies bypass rate limits or get higher quota)
- Explicit overflow handling (queue, reject, or defer)
- Retry-After honor (respect provider rate limit responses)
- Fairness across recipients and origins
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimitConfig:
    """Configuration for rate limiting."""

    # Global limits
    global_max_per_minute: int = 60
    global_max_in_flight: int = 100

    # Per-channel+identity limits (per minute)
    channel_limits: dict[str, int] = None  # e.g., {"telegram.bot": 30, "email.bot": 20}

    # Per-recipient anti-flood limits (per minute)
    per_recipient_max_per_minute: int = 10

    # Reply priority multiplier (replies get this much more quota)
    reply_priority_multiplier: float = 2.0

    def __post_init__(self):
        """Set default channel limits if not provided."""
        if self.channel_limits is None:
            object.__setattr__(
                self,
                "channel_limits",
                {
                    "telegram.bot": 30,
                    "telegram.user": 20,
                    "email.bot": 20,
                    "email.user": 10,
                },
            )


@dataclass
class RateLimitBucket:
    """Token bucket for rate limiting."""

    capacity: int
    """Maximum tokens in bucket."""

    tokens: float
    """Current available tokens."""

    refill_rate: float
    """Tokens added per second."""

    last_refill: datetime
    """Last time bucket was refilled."""

    def refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = datetime.now(UTC)
        elapsed = (now - self.last_refill).total_seconds()
        tokens_to_add = elapsed * self.refill_rate
        self.tokens = min(self.capacity, self.tokens + tokens_to_add)
        self.last_refill = now

    def consume(self, count: float = 1.0) -> bool:
        """Try to consume tokens from bucket.

        Returns True if successful, False if insufficient tokens.
        """
        self.refill()
        if self.tokens >= count:
            self.tokens -= count
            return True
        return False

    def available(self) -> int:
        """Get number of available tokens (rounded down)."""
        self.refill()
        return int(self.tokens)

    def time_until_available(self, count: float = 1.0) -> float:
        """Get seconds until count tokens will be available."""
        self.refill()
        if self.tokens >= count:
            return 0.0
        tokens_needed = count - self.tokens
        return tokens_needed / self.refill_rate


@dataclass
class AdmissionResult:
    """Result of rate limit admission check."""

    admitted: bool
    """Whether the delivery was admitted."""

    error_class: str | None = None
    """Error class if rejected: 'overload_rejected' or 'target_unavailable'."""

    error_message: str | None = None
    """Human-readable error message."""

    retry_after_seconds: float | None = None
    """Suggested retry delay in seconds."""

    limit_type: str | None = None
    """Which limit was hit: 'global', 'channel', 'recipient'."""


@dataclass
class ProviderThrottle:
    """Provider-reported throttling state."""

    channel: str
    """Channel that was throttled."""

    retry_after: datetime
    """When to retry (absolute timestamp)."""

    reason: str
    """Throttle reason from provider."""


class RateLimiter:
    """Rate limiter with three-layer limits and reply priority.

    Enforces:
    - Global delivery admission budget
    - Per-channel + identity budget
    - Per-recipient/per-thread anti-flood budget
    - Reply priority (replies get higher quota)
    - Provider throttle honor (respects Retry-After)
    """

    def __init__(self, config: RateLimitConfig) -> None:
        """Initialize rate limiter.

        Parameters
        ----------
        config:
            Rate limit configuration.
        """
        self._config = config
        self._lock = asyncio.Lock()

        # Global bucket
        self._global_bucket = RateLimitBucket(
            capacity=config.global_max_per_minute,
            tokens=float(config.global_max_per_minute),
            refill_rate=config.global_max_per_minute / 60.0,  # per second
            last_refill=datetime.now(UTC),
        )

        # Per-channel+identity buckets
        self._channel_buckets: dict[str, RateLimitBucket] = {}

        # Per-recipient buckets (recipient -> bucket)
        self._recipient_buckets: dict[str, RateLimitBucket] = {}

        # In-flight tracking
        self._in_flight_count = 0
        self._in_flight_by_channel: dict[str, int] = defaultdict(int)
        self._in_flight_by_recipient: dict[str, int] = defaultdict(int)

        # Provider throttles (channel -> throttle)
        self._provider_throttles: dict[str, ProviderThrottle] = {}

    def _get_channel_key(self, channel: str, identity_scope: str) -> str:
        """Get channel+identity key."""
        return f"{channel}.{identity_scope}"

    def _get_channel_bucket(self, channel: str, identity_scope: str) -> RateLimitBucket:
        """Get or create channel bucket."""
        key = self._get_channel_key(channel, identity_scope)

        if key not in self._channel_buckets:
            limit = self._config.channel_limits.get(key, 30)  # Default to 30/min if not configured
            self._channel_buckets[key] = RateLimitBucket(
                capacity=limit,
                tokens=float(limit),
                refill_rate=limit / 60.0,  # per second
                last_refill=datetime.now(UTC),
            )

        return self._channel_buckets[key]

    def _get_recipient_bucket(self, recipient: str) -> RateLimitBucket:
        """Get or create recipient bucket."""
        if recipient not in self._recipient_buckets:
            limit = self._config.per_recipient_max_per_minute
            self._recipient_buckets[recipient] = RateLimitBucket(
                capacity=limit,
                tokens=float(limit),
                refill_rate=limit / 60.0,  # per second
                last_refill=datetime.now(UTC),
            )

        return self._recipient_buckets[recipient]

    async def check_admission(
        self,
        *,
        channel: str,
        identity_scope: str,
        recipient: str,
        intent: str,
        origin_butler: str,
    ) -> AdmissionResult:
        """Check if delivery should be admitted through rate limits.

        Checks three layers:
        1. Global admission budget
        2. Per-channel+identity budget
        3. Per-recipient anti-flood budget

        Reply intents get priority (higher effective quota).

        Parameters
        ----------
        channel:
            Target channel (telegram, email, etc.).
        identity_scope:
            Identity scope (bot, user).
        recipient:
            Normalized recipient identity.
        intent:
            Delivery intent (send, reply).
        origin_butler:
            Butler originating the request.

        Returns
        -------
        AdmissionResult
            Admission decision with error details if rejected.
        """
        async with self._lock:
            # Check provider throttle first
            throttle = self._provider_throttles.get(channel)
            if throttle and datetime.now(UTC) < throttle.retry_after:
                retry_seconds = (throttle.retry_after - datetime.now(UTC)).total_seconds()
                return AdmissionResult(
                    admitted=False,
                    error_class="target_unavailable",
                    error_message=f"Provider throttled: {throttle.reason}",
                    retry_after_seconds=retry_seconds,
                    limit_type="provider",
                )

            # Calculate token cost (replies cost less due to priority)
            is_reply = intent == "reply"
            token_cost = 1.0 / self._config.reply_priority_multiplier if is_reply else 1.0

            # Check global in-flight limit
            if self._in_flight_count >= self._config.global_max_in_flight:
                return AdmissionResult(
                    admitted=False,
                    error_class="overload_rejected",
                    error_message=(
                        f"Global in-flight limit reached "
                        f"({self._in_flight_count}/{self._config.global_max_in_flight})"
                    ),
                    retry_after_seconds=5.0,  # Default retry delay
                    limit_type="global_in_flight",
                )

            # Check global rate limit
            if not self._global_bucket.consume(token_cost):
                retry_seconds = self._global_bucket.time_until_available(token_cost)
                return AdmissionResult(
                    admitted=False,
                    error_class="overload_rejected",
                    error_message="Global rate limit exceeded",
                    retry_after_seconds=retry_seconds,
                    limit_type="global",
                )

            # Check channel+identity rate limit
            channel_bucket = self._get_channel_bucket(channel, identity_scope)
            if not channel_bucket.consume(token_cost):
                retry_seconds = channel_bucket.time_until_available(token_cost)
                # Return tokens to global bucket
                self._global_bucket.tokens += token_cost
                return AdmissionResult(
                    admitted=False,
                    error_class="overload_rejected",
                    error_message=f"Channel {channel}.{identity_scope} rate limit exceeded",
                    retry_after_seconds=retry_seconds,
                    limit_type="channel",
                )

            # Check per-recipient anti-flood limit
            recipient_bucket = self._get_recipient_bucket(recipient)
            if not recipient_bucket.consume(token_cost):
                retry_seconds = recipient_bucket.time_until_available(token_cost)
                # Return tokens to previous buckets
                self._global_bucket.tokens += token_cost
                channel_bucket.tokens += token_cost
                return AdmissionResult(
                    admitted=False,
                    error_class="overload_rejected",
                    error_message=f"Recipient {recipient} rate limit exceeded (anti-flood)",
                    retry_after_seconds=retry_seconds,
                    limit_type="recipient",
                )

            # Admitted - track in-flight
            self._in_flight_count += 1
            channel_key = self._get_channel_key(channel, identity_scope)
            self._in_flight_by_channel[channel_key] += 1
            self._in_flight_by_recipient[recipient] += 1

            logger.info(
                "Delivery admitted through rate limits",
                extra={
                    "channel": channel,
                    "identity_scope": identity_scope,
                    "recipient": recipient,
                    "intent": intent,
                    "origin_butler": origin_butler,
                    "token_cost": token_cost,
                    "global_in_flight": self._in_flight_count,
                },
            )

            return AdmissionResult(admitted=True)

    async def release(
        self,
        *,
        channel: str,
        identity_scope: str,
        recipient: str,
    ) -> None:
        """Release in-flight tracking after delivery completes.

        Parameters
        ----------
        channel:
            Target channel.
        identity_scope:
            Identity scope.
        recipient:
            Recipient identity.
        """
        async with self._lock:
            self._in_flight_count = max(0, self._in_flight_count - 1)
            channel_key = self._get_channel_key(channel, identity_scope)
            self._in_flight_by_channel[channel_key] = max(
                0, self._in_flight_by_channel[channel_key] - 1
            )
            self._in_flight_by_recipient[recipient] = max(
                0, self._in_flight_by_recipient[recipient] - 1
            )

    async def record_provider_throttle(
        self,
        *,
        channel: str,
        retry_after_seconds: float,
        reason: str,
    ) -> None:
        """Record provider throttle response (e.g., 429).

        Parameters
        ----------
        channel:
            Channel that was throttled.
        retry_after_seconds:
            Seconds to wait before retry (from Retry-After header).
        reason:
            Throttle reason from provider.
        """
        async with self._lock:
            retry_after = datetime.now(UTC) + timedelta(seconds=retry_after_seconds)
            self._provider_throttles[channel] = ProviderThrottle(
                channel=channel,
                retry_after=retry_after,
                reason=reason,
            )

            logger.warning(
                "Provider throttle recorded",
                extra={
                    "channel": channel,
                    "retry_after_seconds": retry_after_seconds,
                    "retry_after": retry_after.isoformat(),
                    "reason": reason,
                },
            )

    async def clear_provider_throttle(self, channel: str) -> None:
        """Clear provider throttle (after successful delivery or manual reset).

        Parameters
        ----------
        channel:
            Channel to clear throttle for.
        """
        async with self._lock:
            if channel in self._provider_throttles:
                del self._provider_throttles[channel]
                logger.info(
                    "Provider throttle cleared",
                    extra={"channel": channel},
                )

    async def get_status(
        self, channel: str | None = None, identity_scope: str | None = None
    ) -> dict[str, Any]:
        """Get current rate limit status.

        Parameters
        ----------
        channel:
            Optional channel filter.
        identity_scope:
            Optional identity scope filter.

        Returns
        -------
        dict
            Rate limit status including headroom, in-flight counts, and throttles.
        """
        async with self._lock:
            # Global status
            status: dict[str, Any] = {
                "global": {
                    "in_flight": self._in_flight_count,
                    "max_in_flight": self._config.global_max_in_flight,
                    "available_tokens": self._global_bucket.available(),
                    "capacity": self._global_bucket.capacity,
                },
            }

            # Channel status
            channel_status = {}
            for key, bucket in self._channel_buckets.items():
                if channel and not key.startswith(f"{channel}."):
                    continue
                if identity_scope and not key.endswith(f".{identity_scope}"):
                    continue

                channel_status[key] = {
                    "in_flight": self._in_flight_by_channel.get(key, 0),
                    "available_tokens": bucket.available(),
                    "capacity": bucket.capacity,
                }

            if channel_status:
                status["channels"] = channel_status

            # Provider throttles
            throttles = {}
            for ch, throttle in self._provider_throttles.items():
                if channel and ch != channel:
                    continue

                remaining = (throttle.retry_after - datetime.now(UTC)).total_seconds()
                if remaining > 0:
                    throttles[ch] = {
                        "retry_after_seconds": remaining,
                        "retry_after": throttle.retry_after.isoformat(),
                        "reason": throttle.reason,
                    }

            if throttles:
                status["provider_throttles"] = throttles

            # Recipient anti-flood (top offenders if no specific filter)
            if not channel and not identity_scope:
                # Show top 5 recipients by in-flight count
                top_recipients = sorted(
                    self._in_flight_by_recipient.items(),
                    key=lambda x: x[1],
                    reverse=True,
                )[:5]

                if top_recipients:
                    status["top_recipients_in_flight"] = [
                        {"recipient": r, "in_flight": count} for r, count in top_recipients
                    ]

            return status
