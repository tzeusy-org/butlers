# Architecture

> **Scope:** System-level design decisions and structural patterns.
> **Belongs here:** System topology, daemon internals, routing implementation detail (triage, thread affinity, priority queuing), database schema, observability architecture.
> **Does NOT belong here:** The end-to-end routing flow (see [Switchboard Routing](../concepts/switchboard-routing.md)), per-butler profiles (see [Butlers](../butlers/index.md)), per-module details, operational procedures.

- [System Topology](system-topology.md) — one-picture overview with links to the authoritative topology
- [Butler Daemon](butler-daemon.md) — daemon internals, startup sequence, core components
- [Database Design](database-design.md) — public schema, per-butler schemas, JSONB patterns
- [Observability](observability.md) — OpenTelemetry, Grafana, Tempo, trace propagation
- [Email Priority Queuing](email-priority-queuing.md) — email priority and queuing design
- [Pre-Classification Triage](pre-classification-triage.md) — pre-classification triage design
- [Thread Affinity Routing](thread-affinity-routing.md) — thread affinity routing design
- [Beads Runtime Data Bridge](beads-runtime-data-bridge.md) — the shipped single-host read-only Beads export mount and its consumers
