"""Validation, dry-run, and operational health tools for Messenger butler.

Implements specs from docs/roles/messenger_butler.md sections 5.1.3-5.1.4:
- messenger_validate_notify: validation without side effects
- messenger_dry_run: validation + target resolution + rate-limit headroom
- messenger_circuit_status: circuit breaker state per channel
- messenger_rate_limit_status: rate-limit headroom per channel/identity
- messenger_queue_depth: in-flight delivery counts
- messenger_delivery_stats: aggregated delivery metrics with grouping
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import asyncpg

from butlers.tools.messenger.rate_limiter import RateLimiter
from butlers.tools.messenger.reliability import CircuitBreaker

# ============================================================================
# 5.1.3 Validation and Dry-Run
# ============================================================================


async def messenger_validate_notify(
    notify_request: dict[str, Any],
) -> dict[str, Any]:
    """Run full validation pipeline without executing delivery.

    Validates schema, required fields, origin verification, and targeting
    against a notify.v1 envelope. Returns structured validation result
    with pass/fail and error details. No side effects.

    Parameters
    ----------
    notify_request:
        The notify.v1 envelope to validate.

    Returns
    -------
    dict:
        Validation result with "valid" boolean and "errors" list.
        Example:
        {
            "valid": false,
            "errors": [
                {"field": "delivery.message", "error": "required field missing"},
                {"field": "origin_butler", "error": "must be non-empty"}
            ]
        }
    """
    errors: list[dict[str, str]] = []

    # Schema version check
    schema_version = notify_request.get("schema_version")
    if schema_version != "notify.v1":
        errors.append(
            {
                "field": "schema_version",
                "error": f"unsupported or missing schema_version: {schema_version}",
            }
        )

    # Origin butler check
    origin_butler = notify_request.get("origin_butler")
    if not origin_butler or not isinstance(origin_butler, str):
        errors.append({"field": "origin_butler", "error": "required field missing or invalid type"})

    # Delivery block presence
    delivery = notify_request.get("delivery")
    if not delivery or not isinstance(delivery, dict):
        errors.append(
            {"field": "delivery", "error": "required delivery block missing or invalid type"}
        )
        # Early return - can't validate delivery fields without delivery block
        return {"valid": False, "errors": errors}

    # Required delivery fields
    intent = delivery.get("intent")
    if intent not in ("send", "reply"):
        errors.append(
            {"field": "delivery.intent", "error": f"must be 'send' or 'reply', got: {intent}"}
        )

    channel = delivery.get("channel")
    if not channel or not isinstance(channel, str):
        errors.append(
            {"field": "delivery.channel", "error": "required field missing or invalid type"}
        )

    message = delivery.get("message")
    if not message or not isinstance(message, str):
        errors.append(
            {"field": "delivery.message", "error": "required non-empty message field missing"}
        )

    # Reply intent requires request_context
    if intent == "reply":
        request_context = notify_request.get("request_context")
        if not request_context or not isinstance(request_context, dict):
            errors.append({"field": "request_context", "error": "required for reply intent"})
        else:
            # Validate required reply context fields
            if not request_context.get("request_id"):
                errors.append(
                    {"field": "request_context.request_id", "error": "required for reply intent"}
                )
            if not request_context.get("source_channel"):
                errors.append(
                    {
                        "field": "request_context.source_channel",
                        "error": "required for reply intent",
                    }
                )
            if not request_context.get("source_endpoint_identity"):
                errors.append(
                    {
                        "field": "request_context.source_endpoint_identity",
                        "error": "required for reply intent",
                    }
                )
            if not request_context.get("source_sender_identity"):
                errors.append(
                    {
                        "field": "request_context.source_sender_identity",
                        "error": "required for reply intent",
                    }
                )

    return {
        "valid": len(errors) == 0,
        "errors": errors,
    }


async def messenger_dry_run(
    notify_request: dict[str, Any],
    rate_limiter: RateLimiter | None = None,
) -> dict[str, Any]:
    """Full validation plus target resolution and rate-limit headroom check.

    Does not execute provider call or persist anything. Returns resolved
    target identity, channel adapter, rate-limit budget, and admission status.

    Parameters
    ----------
    notify_request:
        The notify.v1 envelope to dry-run.
    rate_limiter:
        Optional RateLimiter instance for headroom check.

    Returns
    -------
    dict:
        Dry-run result with validation, target resolution, and rate-limit info.
        Example:
        {
            "valid": true,
            "target_identity": "user@example.com",
            "channel_adapter": "email.bot",
            "intent": "send",
            "rate_limit_headroom": {
                "global": 45,
                "channel": 15,
                "recipient": 8
            },
            "would_be_admitted": true
        }
    """
    # First run validation
    validation_result = await messenger_validate_notify(notify_request)
    if not validation_result["valid"]:
        return {
            "valid": False,
            "errors": validation_result["errors"],
        }

    # Extract delivery info
    delivery = notify_request["delivery"]
    intent = delivery["intent"]
    channel = delivery["channel"]

    # Target resolution
    target_identity: str | None = None
    if intent == "send":
        # Explicit recipient or policy default
        target_identity = delivery.get("recipient")
        if not target_identity:
            target_identity = f"<policy-default-for-{channel}>"
    elif intent == "reply":
        # Derive from request_context
        request_context = notify_request["request_context"]
        target_identity = request_context["source_sender_identity"]

    # Channel adapter resolution (default to bot scope)
    channel_adapter = f"{channel}.bot"

    # Rate-limit headroom check
    rate_limit_headroom: dict[str, int] | None = None
    would_be_admitted = True

    if rate_limiter:
        # Get current headroom from rate limiter buckets
        rate_limit_headroom = {
            "global": rate_limiter._global_bucket.available(),
            "channel": 0,  # Placeholder - would need channel-specific lookup
            "recipient": 0,  # Placeholder - would need recipient-specific lookup
        }

        # Simplified admission check - just check global bucket
        would_be_admitted = rate_limiter._global_bucket.available() > 0

    return {
        "valid": True,
        "target_identity": target_identity,
        "channel_adapter": channel_adapter,
        "intent": intent,
        "rate_limit_headroom": rate_limit_headroom,
        "would_be_admitted": would_be_admitted,
    }


# ============================================================================
# 5.1.4 Operational Health
# ============================================================================


async def messenger_circuit_status(
    circuit_breakers: dict[str, CircuitBreaker] | None = None,
    channel: str | None = None,
) -> dict[str, Any]:
    """Return circuit breaker state per channel/provider.

    When open, includes trip reason, trip timestamp, and recovery timeout.

    Parameters
    ----------
    circuit_breakers:
        Dictionary mapping channel name to CircuitBreaker instance.
    channel:
        Optional filter - return status for specific channel only.

    Returns
    -------
    dict:
        Circuit breaker status per channel.
        Example:
        {
            "circuits": {
                "telegram.bot": {
                    "state": "open",
                    "trip_reason": "consecutive failures exceeded threshold",
                    "trip_timestamp": "2026-02-15T12:30:00Z",
                    "recovery_timeout_seconds": 60.0,
                    "consecutive_failures": 5
                },
                "email.bot": {
                    "state": "closed",
                    "consecutive_failures": 0
                }
            }
        }
    """
    if not circuit_breakers:
        return {"circuits": {}}

    circuits: dict[str, Any] = {}

    for channel_name, breaker in circuit_breakers.items():
        # Skip if filtering and doesn't match
        if channel and channel_name != channel:
            continue

        circuit_info: dict[str, Any] = {
            "state": breaker.state.state.value,
            "consecutive_failures": breaker.state.consecutive_failures,
        }

        # Add open-state details
        if breaker.state.state.value == "open":
            circuit_info["trip_reason"] = (
                f"Consecutive failures ({breaker.state.consecutive_failures}) "
                f"exceeded threshold ({breaker.config.failure_threshold})"
            )
            if breaker.state.opened_at:
                circuit_info["trip_timestamp"] = breaker.state.opened_at.isoformat()
            circuit_info["recovery_timeout_seconds"] = breaker.config.recovery_timeout_seconds
            if breaker.state.last_error_class:
                circuit_info["last_error_class"] = breaker.state.last_error_class
            if breaker.state.last_error_message:
                circuit_info["last_error_message"] = breaker.state.last_error_message

        # Add half-open state details
        elif breaker.state.state.value == "half_open":
            circuit_info["half_open_attempts"] = breaker.state.half_open_attempts
            circuit_info["half_open_successes"] = breaker.state.half_open_successes
            circuit_info["success_threshold"] = breaker.config.half_open_success_threshold

        circuits[channel_name] = circuit_info

    return {"circuits": circuits}


async def messenger_rate_limit_status(
    rate_limiter: RateLimiter | None = None,
    channel: str | None = None,
    identity_scope: str | None = None,
) -> dict[str, Any]:
    """Return current rate-limit headroom per channel and identity scope.

    Shows budget consumed vs total, window reset time, and active anti-flood limits.

    Parameters
    ----------
    rate_limiter:
        RateLimiter instance.
    channel:
        Optional filter for specific channel.
    identity_scope:
        Optional filter for specific identity scope (e.g., "bot", "user").

    Returns
    -------
    dict:
        Rate-limit status with headroom and window info.
        Example:
        {
            "global": {
                "capacity": 60,
                "available": 45,
                "consumed": 15,
                "refill_rate": 1.0,
                "window_reset_seconds": 15.0
            },
            "channels": {
                "telegram.bot": {
                    "capacity": 30,
                    "available": 22,
                    "consumed": 8,
                    "window_reset_seconds": 8.5
                }
            }
        }
    """
    if not rate_limiter:
        return {
            "global": {"capacity": 0, "available": 0, "consumed": 0},
            "channels": {},
        }

    # Global bucket status
    global_bucket = rate_limiter._global_bucket
    global_bucket.refill()  # Ensure current state
    global_status = {
        "capacity": global_bucket.capacity,
        "available": global_bucket.available(),
        "consumed": global_bucket.capacity - global_bucket.available(),
        "refill_rate": global_bucket.refill_rate,
        "window_reset_seconds": global_bucket.time_until_available(global_bucket.capacity),
    }

    # Per-channel buckets
    # Ensure channel buckets from config are initialized
    if rate_limiter._config.channel_limits:
        for channel_key in rate_limiter._config.channel_limits:
            if channel_key not in rate_limiter._channel_buckets:
                # Lazily create the bucket by calling _get_or_create_channel_bucket
                # Extract channel and scope from key
                parts = channel_key.split(".")
                if len(parts) == 2:
                    from datetime import UTC, datetime

                    from butlers.tools.messenger.rate_limiter import RateLimitBucket

                    limit = rate_limiter._config.channel_limits[channel_key]
                    rate_limiter._channel_buckets[channel_key] = RateLimitBucket(
                        capacity=limit,
                        tokens=float(limit),
                        refill_rate=limit / 60.0,
                        last_refill=datetime.now(UTC),
                    )

    channels_status: dict[str, Any] = {}
    for channel_key, bucket in rate_limiter._channel_buckets.items():
        # Filter by channel if specified
        if channel and not channel_key.startswith(channel):
            continue
        # Filter by identity_scope if specified
        if identity_scope and not channel_key.endswith(identity_scope):
            continue

        bucket.refill()
        channels_status[channel_key] = {
            "capacity": bucket.capacity,
            "available": bucket.available(),
            "consumed": bucket.capacity - bucket.available(),
            "window_reset_seconds": bucket.time_until_available(bucket.capacity),
        }

    return {
        "global": global_status,
        "channels": channels_status,
    }


async def messenger_queue_depth(
    pool: asyncpg.Pool,
    channel: str | None = None,
) -> dict[str, Any]:
    """Return count of in-flight deliveries, optionally filtered by channel.

    Includes breakdown by status (admitted, awaiting-retry, in-provider-call).

    Parameters
    ----------
    pool:
        Database connection pool.
    channel:
        Optional filter for specific channel.

    Returns
    -------
    dict:
        Queue depth with status breakdown.
        Example:
        {
            "total_in_flight": 15,
            "by_status": {
                "pending": 3,
                "in_progress": 12
            },
            "by_channel": {
                "telegram": 8,
                "email": 7
            }
        }
    """
    # Build WHERE clause
    where_conditions = ["status IN ('pending', 'in_progress')"]
    params: list[Any] = []

    if channel:
        params.append(channel)
        where_conditions.append(f"channel = ${len(params)}")

    where_clause = " AND ".join(where_conditions)

    # Get total in-flight count
    total_query = f"""
        SELECT COUNT(*) as total
        FROM delivery_requests
        WHERE {where_clause}
    """
    total_row = await pool.fetchrow(total_query, *params)
    total_in_flight = total_row["total"] if total_row else 0

    # Get breakdown by status
    status_query = f"""
        SELECT status, COUNT(*) as count
        FROM delivery_requests
        WHERE {where_clause}
        GROUP BY status
    """
    status_rows = await pool.fetch(status_query, *params)
    by_status = {row["status"]: row["count"] for row in status_rows}

    # Get breakdown by channel (if not already filtered)
    by_channel: dict[str, int] = {}
    if not channel:
        channel_query = """
            SELECT channel, COUNT(*) as count
            FROM delivery_requests
            WHERE status IN ('pending', 'in_progress')
            GROUP BY channel
        """
        channel_rows = await pool.fetch(channel_query)
        by_channel = {row["channel"]: row["count"] for row in channel_rows}

    return {
        "total_in_flight": total_in_flight,
        "by_status": by_status,
        "by_channel": by_channel,
    }


async def messenger_delivery_stats(
    pool: asyncpg.Pool,
    since: str | None = None,
    until: str | None = None,
    group_by: str | None = None,
) -> dict[str, Any]:
    """Aggregate delivery metrics over a time window.

    Supports grouping by channel, intent, origin_butler, outcome, error_class.
    Returns counts, success rate, p50/p95 latency, retry rate, dead-letter rate.

    Parameters
    ----------
    pool:
        Database connection pool.
    since:
        ISO timestamp - start of time window.
    until:
        ISO timestamp - end of time window.
    group_by:
        Optional grouping dimension: "channel", "intent", "origin_butler",
        "outcome", "error_class".

    Returns
    -------
    dict:
        Aggregated delivery statistics.
        Example:
        {
            "time_window": {"since": "...", "until": "..."},
            "total_deliveries": 1000,
            "success_count": 950,
            "success_rate": 0.95,
            "failed_count": 30,
            "dead_lettered_count": 20,
            "p50_latency_ms": 250,
            "p95_latency_ms": 1200,
            "retry_rate": 0.15,
            "groups": {
                "telegram": {...},
                "email": {...}
            }
        }
    """
    # Parse time window
    where_conditions: list[str] = []
    params: list[Any] = []

    since_dt: datetime | None = None
    until_dt: datetime | None = None

    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            params.append(since_dt)
            where_conditions.append(f"dr.created_at >= ${len(params)}")
        except ValueError:
            return {"error": f"Invalid since timestamp: {since}"}

    if until:
        try:
            until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
            params.append(until_dt)
            where_conditions.append(f"dr.created_at <= ${len(params)}")
        except ValueError:
            return {"error": f"Invalid until timestamp: {until}"}

    where_clause = " AND ".join(where_conditions) if where_conditions else "TRUE"

    # Validate group_by
    valid_group_by = ["channel", "intent", "origin_butler", "outcome", "error_class"]
    if group_by and group_by not in valid_group_by:
        return {"error": f"Invalid group_by: {group_by}. Must be one of {valid_group_by}"}

    # Build aggregation query
    group_by_clause = f"dr.{group_by}" if group_by else "NULL"

    query = f"""
        WITH delivery_stats AS (
            SELECT
                {group_by_clause} as group_key,
                COUNT(DISTINCT dr.id) as total_deliveries,
                COUNT(DISTINCT CASE WHEN dr.status = 'delivered' THEN dr.id END) as success_count,
                COUNT(DISTINCT CASE WHEN dr.status = 'failed' THEN dr.id END) as failed_count,
                COUNT(DISTINCT CASE WHEN dr.status = 'dead_lettered' THEN dr.id END)
                    as dead_lettered_count,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY da.latency_ms) as p50_latency_ms,
                PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY da.latency_ms) as p95_latency_ms,
                COUNT(da.id) as total_attempts,
                COUNT(DISTINCT dr.id) FILTER (WHERE da.attempt_number > 1) as retried_deliveries
            FROM delivery_requests dr
            LEFT JOIN delivery_attempts da ON da.delivery_request_id = dr.id
            WHERE {where_clause}
            GROUP BY group_key
        )
        SELECT
            group_key,
            total_deliveries,
            success_count,
            failed_count,
            dead_lettered_count,
            ROUND(p50_latency_ms::numeric, 0) as p50_latency_ms,
            ROUND(p95_latency_ms::numeric, 0) as p95_latency_ms,
            total_attempts,
            retried_deliveries,
            CASE
                WHEN total_deliveries > 0
                THEN ROUND((success_count::float / total_deliveries)::numeric, 4)
                ELSE 0
            END as success_rate,
            CASE
                WHEN total_deliveries > 0
                THEN ROUND((retried_deliveries::float / total_deliveries)::numeric, 4)
                ELSE 0
            END as retry_rate
        FROM delivery_stats
        ORDER BY total_deliveries DESC
    """

    rows = await pool.fetch(query, *params)

    # Aggregate totals
    total_deliveries = sum(row["total_deliveries"] for row in rows)
    success_count = sum(row["success_count"] for row in rows)
    failed_count = sum(row["failed_count"] for row in rows)
    dead_lettered_count = sum(row["dead_lettered_count"] for row in rows)

    success_rate = success_count / total_deliveries if total_deliveries > 0 else 0.0
    retried_sum = sum(row["retried_deliveries"] for row in rows)
    retry_rate = retried_sum / total_deliveries if total_deliveries > 0 else 0.0
    retry_rate = retried_sum / total_deliveries if total_deliveries > 0 else 0.0

    # Compute overall latency percentiles if we have data
    p50_latency_ms: int | None = None
    p95_latency_ms: int | None = None
    if rows:
        # Use weighted average of group percentiles (approximation)
        total_weight = sum(row["total_deliveries"] for row in rows)
        if total_weight > 0:
            p50_latency_ms = int(
                sum((row["p50_latency_ms"] or 0) * row["total_deliveries"] for row in rows)
                / total_weight
            )
            p95_latency_ms = int(
                sum((row["p95_latency_ms"] or 0) * row["total_deliveries"] for row in rows)
                / total_weight
            )

    result: dict[str, Any] = {
        "time_window": {
            "since": since_dt.isoformat() if since_dt else None,
            "until": until_dt.isoformat() if until_dt else None,
        },
        "total_deliveries": total_deliveries,
        "success_count": success_count,
        "success_rate": round(success_rate, 4),
        "failed_count": failed_count,
        "dead_lettered_count": dead_lettered_count,
        "p50_latency_ms": p50_latency_ms,
        "p95_latency_ms": p95_latency_ms,
        "retry_rate": round(retry_rate, 4),
    }

    # Add grouped results if group_by specified
    if group_by:
        groups: dict[str, Any] = {}
        for row in rows:
            group_key = row["group_key"]
            if group_key is None:
                continue
            groups[str(group_key)] = {
                "total_deliveries": row["total_deliveries"],
                "success_count": row["success_count"],
                "success_rate": float(row["success_rate"]),
                "failed_count": row["failed_count"],
                "dead_lettered_count": row["dead_lettered_count"],
                "p50_latency_ms": int(row["p50_latency_ms"]) if row["p50_latency_ms"] else None,
                "p95_latency_ms": int(row["p95_latency_ms"]) if row["p95_latency_ms"] else None,
                "retry_rate": float(row["retry_rate"]),
            }
        result["groups"] = groups

    return result
