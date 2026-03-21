# Architecture

> **Scope:** System-level design decisions and structural patterns.
> **Belongs here:** System topology, daemon internals, routing design, database schema, observability architecture.
> **Does NOT belong here:** Per-butler profiles (see [Butlers](../butlers/index.md)), per-module details, operational procedures.

- [System Topology](system-topology.md) — service ports, inter-service communication, overall shape
- [Butler Daemon](butler-daemon.md) — daemon internals, startup sequence, core components
- [Routing](routing.md) — switchboard routing architecture, classification, fanout
- [Database Design](database-design.md) — shared schema, per-butler schemas, JSONB patterns
- [Observability](observability.md) — OpenTelemetry, Grafana, Tempo, trace propagation
- [Email Priority Queuing](email-priority-queuing.md) — email priority and queuing design
- [Pre-Classification Triage](pre-classification-triage.md) — pre-classification triage design
- [Thread Affinity Routing](thread-affinity-routing.md) — thread affinity routing design
