# Connector Horizontal Scaling

> **Purpose:** Document patterns for scaling connectors beyond single-instance deployments while maintaining exactly-once ingestion semantics.
> **Audience:** Operators managing high-throughput or high-availability connector deployments.
> **Prerequisites:** [Ingestion Envelope](../api_and_protocols/ingestion-envelope.md), [Docker Deployment](docker-deployment.md).

## Overview

Connectors are transport adapters that fetch events from external systems (Telegram, Gmail, Slack) and submit them to the Switchboard via the `ingest.v1` envelope protocol. The default deployment model is one process per connector per endpoint identity, using DB-backed checkpoints via `cursor_store`. This page describes scaling patterns for when a single instance becomes a bottleneck.

## Current Single-Instance Model (v1)

The baseline deployment uses a simple architecture:

1. A single connector process polls the source system (e.g., Telegram `getUpdates`).
2. Events are normalized to `ingest.v1` and submitted to the Switchboard MCP server.
3. The resume checkpoint is persisted in `switchboard.connector_registry` via `cursor_store`.
4. On restart, the connector replays from the last saved checkpoint. Duplicates are safe because the Switchboard deduplicates at ingest.

**Key properties:**
- Single owner of checkpoint state -- no coordination needed.
- Transactional writes via asyncpg prevent partial checkpoint corruption.
- At-least-once delivery from source to connector; exactly-once effect at Switchboard.

## When to Consider Scaling

**Stay with single-instance if:**
- Current and projected load is within capacity.
- Source API rate limits are not a bottleneck.
- Cost of coordination infrastructure outweighs benefits.

**Consider scaling when:**
- Sustained ingest rate exceeds 1000 events/sec per endpoint.
- Single-instance CPU/memory consistently above 80%.
- High-availability SLO requires sub-minute failover time.
- Source system supports native partitioning.

## Pattern 1: Lease-Based Active-Standby

**Use case:** High availability without throughput increase.

Two instances are deployed. One holds a lease (via Redis, etcd, or Consul) and actively ingests. The other remains in standby, periodically attempting to acquire the lease. On active instance failure, the lease expires (TTL: 10-30 seconds) and the standby takes over, resuming from the last checkpoint.

| Aspect | Rating |
|--------|--------|
| Coordination complexity | Low |
| Throughput gain | None |
| HA benefit | High |
| Recommended technology | Redis `SET EX NX`, etcd leases |

## Pattern 2: Distributed Locking with Sharded Checkpoints

**Use case:** Active-active parallelization across multiple instances.

The event stream is divided into deterministic shards (e.g., `hash(event_id) % num_shards`). Each instance acquires locks for available shards and processes only events belonging to its assigned shards. Each shard maintains an independent checkpoint.

| Aspect | Rating |
|--------|--------|
| Coordination complexity | Medium |
| Throughput gain | 2-4x |
| HA benefit | Medium |
| Recommended technology | Redis RedLock, PostgreSQL advisory locks |

## Pattern 3: Partition-Based Static Assignment

**Use case:** Source systems with native partitioning (e.g., multiple bot tokens, multiple mailboxes).

Each instance is statically assigned to specific partitions via environment variable (`CONNECTOR_PARTITIONS=0,1`). No coordination is required because partitions are disjoint. Deploy using Kubernetes StatefulSets with partition ID derived from pod ordinal.

| Aspect | Rating |
|--------|--------|
| Coordination complexity | None |
| Throughput gain | Linear |
| HA benefit | High |
| Limitation | Requires source-native partitioning |

## Pattern 4: Kafka-Style Consumer Groups

**Use case:** Very high throughput (above 10k events/sec) with automatic rebalancing.

An intermediate queue layer (Kafka, Pulsar, RabbitMQ) sits between the source and connector instances. A single producer fetches from the source and publishes to the queue. Multiple consumer instances form a consumer group with automatic partition reassignment on join/leave.

| Aspect | Rating |
|--------|--------|
| Coordination complexity | High |
| Throughput gain | Linear |
| HA benefit | High |
| Trade-off | Additional infrastructure and latency |

## Conflict Resolution and Checkpoints

Storage backends include PostgreSQL (v1 baseline, single-writer), Redis (low latency, TTL), etcd (strong consistency), and PostgreSQL advisory locks (no new infra).

When two instances contest a checkpoint, use generation numbers (epoch fencing), compare-and-swap (CAS), or lease-based fencing.

## Migration Path

1. **Phase 1:** Deploy standby instance, verify no interference (7 days).
2. **Phase 2:** Deploy coordination store, enable lease-based coordination (14-day canary).
3. **Phase 3:** Implement shard-based filtering and per-shard checkpoints.

Rollback: stop scaled instances, copy checkpoint to `connector_registry`, restart single instance.

## Related Pages

- [Ingestion Envelope](../api_and_protocols/ingestion-envelope.md) -- The ingest.v1 protocol
- [Docker Deployment](docker-deployment.md) -- Base deployment configuration
- [Grafana Monitoring](grafana-monitoring.md) -- Metrics and dashboards
