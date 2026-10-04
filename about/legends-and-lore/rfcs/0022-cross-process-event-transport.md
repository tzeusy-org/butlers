# RFC 0022: Cross-Process Event Transport (NOTIFY/LISTEN Fleet Event Bridge)

**Status:** Accepted
**Date:** 2026-07-12
**Amended:** 2026-07-18 (Switchboard ingestion, connector filtered-event batches, Calendar, and Chronicler producers)

## Decision

Use PostgreSQL NOTIFY/LISTEN to carry best-effort live freshness signals from
producer processes to the dashboard-api process. Producers publish on their
own database pool; a dedicated dashboard listener republishes notifications
onto the existing in-process fleet event bus and `WS /api/events/stream`.
Daemon-local event-bus calls cannot reach dashboard subscribers, so daemon
producers use the bridge exclusively. Native dashboard events retain their
existing in-process path.

The adopted behavioral contract now lives in
[`core-fleet-events`](../../../openspec/specs/core-fleet-events/spec.md).
It owns the channel/envelope, bounded publication and privacy rules, canonical
database selection, bus compatibility, defensive parsing, lifecycle/recovery,
loss semantics, producer materiality/commit boundaries, and client cache/poll
reconciliation. The 2026-07-18 amendments are incorporated there.

The shared database topology from [RFC 0006](0006-database-schema-and-isolation.md)
already connects every producer and the dashboard. NOTIFY is database-scoped,
so schema-scoped producers can share one listener without introducing a new
service, port, credential, table, or migration. Holding a dedicated connection
keeps its connection-scoped LISTEN registration alive independently of pool
recycling.

## Trade-offs

- **Low overhead, best-effort delivery.** NOTIFY supplies the needed pub/sub
  primitive without a broker. It is not a durable queue: restart/reconnect
  gaps lose signals. Durable business records and dashboard polling remain
  the correctness backstop; ring snapshots cover only events received by the
  dashboard bus. Publication failures must not change business outcomes.
- **Small freshness metadata.** PostgreSQL's 8000-byte NOTIFY limit motivates
  the lower publisher budget and reference-only treatment of future
  unbounded content. Ingestion and projection signals avoid raw user content.
  Dashboard arrival timestamps avoid introducing an origin-side clock.
- **Self-recovery.** A lost listener must reconnect rather than silently leave
  a connected WebSocket receiving no producer events. Startup isolation keeps
  bridge failure from taking down the dashboard.
- **Alternatives rejected.** An HTTP callback adds authenticated dashboard
  ingress; an internal RPC adds its own transport, auth, and reconnect story.
  Redis/NATS adds a service for a primitive the shared PostgreSQL already
  supplies ([RFC 0008](0008-deployment-network-security.md)). Durable-table polling is
  retained as fallback, but cannot provide the intended immediate live signal.
