"""Receiver-derived, exact-endpoint count/listening windows.

The database owns the snapshot clock and recording admission. A successful
empty history read is observational, never sufficient for a DEAF verdict.
Counts and listening have independent availability and transaction savepoints.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

_READ_FAILURES = (asyncpg.PostgresError, OSError, TimeoutError)


async def recording_catalog(connection: Any) -> list[dict[str, Any]] | None:
    """Lock/inspect the trusted chain, or preserve an explicitly legacy read."""
    try:
        async with connection.transaction():
            return await connection.fetchval("SELECT switchboard.heartbeat_recording_catalog()")
    except _READ_FAILURES:
        return None


def _bounds(as_of: datetime, period: str) -> list[tuple[datetime, datetime]]:
    if period == "24h":
        step, length, end = timedelta(hours=1), 24, as_of
    else:
        step, length = timedelta(days=1), 8 if period == "7d" else 31
        end = as_of.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0) + step
    return [(end - step * (length - i), end - step * (length - i - 1)) for i in range(length)]


async def read_buckets(
    connection: Any,
    pairs: list[tuple[str, str]],
    period: str,
    *,
    catalog: list[dict[str, Any]] | None,
    as_of: datetime,
) -> tuple[dict[tuple[str, str], list[dict[str, Any]]], dict[str, Any]]:
    """Read one fixed pair set in batch after relation/endpoint/row locks."""
    bounds = _bounds(as_of, period)
    start, end = bounds[0][0], bounds[-1][1]
    width = timedelta(hours=1) if period == "24h" else timedelta(days=1)
    source_start = (
        as_of - timedelta(hours=24)
        if period == "24h"
        else as_of - timedelta(days=7 if period == "7d" else 30)
    )
    selected = [{"connector_type": kind, "endpoint_identity": endpoint} for kind, endpoint in pairs]
    if not pairs:
        return {}, {
            "as_of": as_of.isoformat(),
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "count_source_start": source_start.isoformat(),
            "count_source_end": as_of.isoformat(),
            "bucket_width_s": int(width.total_seconds()),
            "hourly_events_available": True,
            "heartbeat_history_available": False,
            "recording_coverage_available": catalog is not None,
        }
    counts: dict[tuple[str, str, datetime], dict[str, int]] = {}
    counts_available = True
    try:
        async with connection.transaction():
            rows = await connection.fetch(
                """
                WITH pairs AS (SELECT * FROM jsonb_to_recordset($1::jsonb)
                    AS p(connector_type text, endpoint_identity text)),
                events AS (
                    SELECT p.connector_type, p.endpoint_identity, e.received_at,
                        (e.status = 'ingested')::int AS ingested,
                        (e.status = 'failed')::int AS failed, 0 AS filtered
                    FROM pairs p JOIN public.ingestion_events e
                      ON COALESCE(e.source_provider, e.source_channel) = p.connector_type
                     AND e.source_endpoint_identity = p.endpoint_identity
                    WHERE e.received_at >= $5::timestamptz
                      AND e.received_at < LEAST($3::timestamptz, $6::timestamptz)
                    UNION ALL
                    SELECT p.connector_type, p.endpoint_identity, f.received_at, 0, 0, 1
                    FROM pairs p JOIN connectors.filtered_events f
                      ON f.connector_type = p.connector_type
                     AND f.endpoint_identity = p.endpoint_identity
                    WHERE f.received_at >= $5::timestamptz
                      AND f.received_at < LEAST($3::timestamptz, $6::timestamptz)
                )
                SELECT connector_type, endpoint_identity,
                    $2::timestamptz + floor(
                        extract(epoch FROM (received_at - $2::timestamptz)) /
                        $4::double precision) *
                        ($4::double precision * interval '1 second') AS bucket,
                    sum(ingested)::bigint AS messages_ingested,
                    sum(failed)::bigint AS messages_failed,
                    sum(filtered)::bigint AS messages_filtered
                FROM events GROUP BY 1,2,3 ORDER BY 1,2,3
                """,
                selected,
                start,
                end,
                width.total_seconds(),
                source_start,
                as_of,
            )
            for row in rows:
                counts[(row["connector_type"], row["endpoint_identity"], row["bucket"])] = {
                    name: int(row[name])
                    for name in ("messages_ingested", "messages_failed", "messages_filtered")
                }
    except _READ_FAILURES:
        counts_available = False

    observations: dict[tuple[str, str, datetime], tuple[int, bool]] = {}
    history_available = True
    try:
        async with connection.transaction():
            rows = await connection.fetch(
                """
                WITH pairs AS (SELECT * FROM jsonb_to_recordset($1::jsonb)
                    AS p(connector_type text, endpoint_identity text)),
                buckets AS (SELECT generate_series($2::timestamptz,
                    $3::timestamptz - ($4::double precision * interval '1 second'),
                    $4::double precision * interval '1 second') AS bucket)
                SELECT p.connector_type, p.endpoint_identity, b.bucket,
                    count(h.id)::bigint AS observed,
                    COALESCE($5::jsonb IS NOT NULL
                      AND r.operational_role = 'runtime_instance'
                      AND r.first_seen_at <= b.bucket
                      AND r.heartbeat_history_coverage->>'version' = '1'
                      AND (r.heartbeat_history_coverage->>'coverage_start')::timestamptz <= b.bucket
                      AND b.bucket >= $6 - interval '7 days'
                      AND b.bucket + ($4::double precision * interval '1 second') <= $6
                      AND (SELECT sum(extract(epoch FROM
                          least((receipt->>'range_end')::timestamptz,
                                b.bucket + ($4::double precision * interval '1 second')) -
                          greatest((receipt->>'range_start')::timestamptz, b.bucket)))
                          FROM jsonb_array_elements($5::jsonb) receipt
                          WHERE (receipt->>'range_start')::timestamptz <
                                    b.bucket + ($4::double precision * interval '1 second')
                            AND (receipt->>'range_end')::timestamptz > b.bucket) = $4
                      AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements($5::jsonb) receipt
                          WHERE (receipt->>'range_start')::timestamptz <
                                    b.bucket + ($4::double precision * interval '1 second')
                            AND (receipt->>'range_end')::timestamptz > b.bucket
                            AND NOT r.heartbeat_history_coverage->'partitions'
                                @> jsonb_build_array(receipt)), false) AS covered
                FROM pairs p CROSS JOIN buckets b
                LEFT JOIN switchboard.connector_registry r ON r.connector_type = p.connector_type
                    AND r.endpoint_identity = p.endpoint_identity AND r.deleted_at IS NULL
                LEFT JOIN switchboard.connector_heartbeat_log h
                  ON h.connector_type = p.connector_type
                    AND h.endpoint_identity = p.endpoint_identity
                    AND h.received_at >= b.bucket
                    AND h.received_at < least(
                        b.bucket + ($4::double precision * interval '1 second'), $6)
                GROUP BY p.connector_type, p.endpoint_identity, b.bucket,
                    r.operational_role, r.first_seen_at, r.heartbeat_history_coverage
                ORDER BY 1,2,3
                """,
                selected,
                start,
                end,
                width.total_seconds(),
                catalog,
                as_of,
            )
            for row in rows:
                observations[(row["connector_type"], row["endpoint_identity"], row["bucket"])] = (
                    int(row["observed"]),
                    bool(row["covered"]),
                )
    except _READ_FAILURES:
        history_available = False

    result: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for kind, endpoint in pairs:
        buckets = []
        for bucket_start, bucket_end in bounds:
            key = (kind, endpoint, bucket_start)
            observed, covered = observations.get(key, (0, False))
            # A compromised/unreachable guard chain cannot certify even a
            # positive row. Retained legacy rows remain observational positives
            # only while the current actual chain is intact.
            if catalog is None:
                observed, covered = 0, False
            listening = "live" if observed else "deaf" if covered else "unknown"
            values = (
                counts.get(
                    key,
                    dict.fromkeys(("messages_ingested", "messages_failed", "messages_filtered"), 0),
                )
                if counts_available
                else dict.fromkeys(
                    ("messages_ingested", "messages_failed", "messages_filtered"), None
                )
            )
            buckets.append(
                {
                    "connector_type": kind,
                    "endpoint_identity": endpoint,
                    "bucket_start": bucket_start.isoformat(),
                    "bucket_end": bucket_end.isoformat(),
                    "listening": listening,
                    "counts_partial": bucket_start < source_start or bucket_end > as_of,
                    "listening_reason": "accepted_heartbeat"
                    if observed
                    else "complete_recording"
                    if covered
                    else "recording_unknown",
                    **values,
                }
            )
        result[(kind, endpoint)] = buckets
    return result, {
        "as_of": as_of.isoformat(),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "count_source_start": source_start.isoformat(),
        "count_source_end": as_of.isoformat(),
        "bucket_width_s": int(width.total_seconds()),
        "hourly_events_available": counts_available,
        "heartbeat_history_available": history_available,
        "recording_coverage_available": catalog is not None,
    }


async def locked_buckets(pool: Any, pairs: list[tuple[str, str]], period: str = "24h"):
    """Batch roster snapshot; its selected pairs cannot grow during the read."""
    async with pool.acquire() as connection:
        async with connection.transaction(isolation="read_committed"):
            catalog = await recording_catalog(connection)
            for kind, endpoint in sorted(set(pairs)):
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                    f"connector-classification:{kind}:{endpoint}",
                )
            # A legacy/core-only read has no protected canonical registry
            # authority. Keep independent count reads available without
            # pretending that an alternate namespace supplied that authority.
            if pairs and catalog is not None:
                await connection.fetch(
                    "SELECT connector_type FROM switchboard.connector_registry "
                    "WHERE (connector_type, endpoint_identity) IN "
                    "(SELECT connector_type, endpoint_identity FROM jsonb_to_recordset($1::jsonb) "
                    "AS p(connector_type text, endpoint_identity text)) "
                    "ORDER BY connector_type, endpoint_identity FOR UPDATE",
                    [
                        {"connector_type": kind, "endpoint_identity": endpoint}
                        for kind, endpoint in pairs
                    ],
                )
            as_of = await connection.fetchval("SELECT clock_timestamp()")
            return await read_buckets(connection, pairs, period, catalog=catalog, as_of=as_of)
