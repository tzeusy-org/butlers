# Research Ledger

This ledger records the three independent prerequisite-discovery passes required by the training-curriculum workflow. Each pass used isolated context and reported candidate concepts before reconciliation.

## Pass 1

Angle: surface and topology pass.

Focus: top-level docs, architecture docs, manifests, configs, specs, directory boundaries, deployment surfaces, and major subsystem names.

Major concept clusters surfaced:

- MCP/FastMCP over SSE, daemon topology, ephemeral LLM sessions, and runtime adapters.
- Modules versus connectors, switchboard routing, envelopes, and idempotent ingress.
- PostgreSQL one-db/multi-schema storage, Alembic chains, JSONB, identity, credentials, approvals, and scheduling.
- OpenTelemetry, Docker Compose, frontend/API contracts, pytest/testcontainers, OpenSpec/doctrine, memory, blob storage, and WhatsApp sidecar.

## Pass 2

Angle: runtime and failure-mode pass.

Focus: tests, runtime orchestration, async behavior, logging, metrics, retries, timeouts, DB pools, network and auth failure paths, and operational scripts.

Major concept clusters surfaced:

- `asyncio` lifecycle, cancellation, semaphores, shielding, bounded queues, backpressure, crash recovery, retries, and canonical failure classification.
- Async subprocess runtime adapters, MCP discovery/retry behavior, env isolation, process diagnostics, model catalog authority, quotas, and token ledgers.
- PostgreSQL schemas, `SET ROLE`, migrations, JSONB round-trips, DB-backed secrets, OAuth state, API auth, approval gates, scheduler advancement, observability, Docker Compose, xdist, testcontainers, and blob storage.

## Pass 3

Angle: contribution-hazard and hidden-concept pass.

Focus: invariants a newcomer could violate, implicit vocabulary, tests that encode contracts, migrations, async/runtime boundaries, data/security hazards, and safe-change risks.

Major concept clusters surfaced:

- MCP runtime boundary, module/connector boundary, daemon startup order, module failure cascade, async cancellation, and backpressure.
- One-db schema isolation, guarded Alembic DDL, request context, prompt-injection boundary, idempotency, JSONB serialization, time/cron/recurrence, credentials, approvals, identity/entity anchoring, memory provenance, model catalog authority, and test topology.

## Reconciliation

Concepts that appeared in all three passes:

- MCP/FastMCP tool boundaries and ephemeral runtime sessions.
- Async runtime behavior, cancellation, subprocess sessions, queues, retry/timeout/failure semantics.
- Modules versus connectors, switchboard routing, route envelopes, request context, and idempotency.
- PostgreSQL schemas, roles/search paths, JSONB, Alembic migrations, and guarded schema evolution.
- Identity, credentials, OAuth or DB-backed secrets, approval gates, and trust boundaries.
- Scheduling, cron/time semantics, calendar/event projection, and autonomous workflows.
- Observability, Docker/service orchestration, and pytest/testcontainers/xdist test constraints.

Concepts that appeared in two passes:

- Model catalog authority, token quotas, and runtime timeout semantics.
- Memory facts/provenance/embeddings/vector search.
- Blob/object storage and attachment policy.
- Frontend/API contracts.
- OpenSpec/doctrine traceability.

Concepts that appeared in only one pass:

- WhatsApp Go sidecar/subprocess IPC. This is included as a deferable glossary/nice-later item, not a central module.

Late-shaping concepts:

- The runtime/failure pass elevated backpressure and failure classification from supporting detail to a first-class module.
- The contribution-hazard pass elevated schema roles/search paths and approval/sensitivity boundaries to implementation-depth topics.
- The surface pass added frontend/API contracts and blob storage as necessary for full-repo familiarity, but not as mandatory reading prerequisites.

Additional passes beyond the hard minimum were not needed. The three passes converged strongly on the core prerequisite surface, and the remaining single-pass concept was narrow and deferable.
