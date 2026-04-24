# Butlers Prerequisite Curriculum

This curriculum is for a developer who wants enough background to understand and safely change this repository. It is not a replacement for the project docs. It teaches the technical concepts those docs assume: MCP tool boundaries, async runtime behavior, PostgreSQL schema isolation, migrations, identity and secrets, scheduling, observability, tests, memory, retrieval, and domain module patterns.

Shortest-path learning order:

1. System boundaries: what kind of system this is and how MCP, daemons, modules, connectors, and routing fit together.
2. Runtime behavior: how async tasks, queues, subprocesses, retries, idempotency, and failure classification work.
3. Storage: how PostgreSQL schemas, roles, JSONB, and Alembic migration chains shape nearly every feature.
4. Trust: how identity, credentials, OAuth, approvals, and tool sensitivity protect side effects.
5. Time: how cron, calendar projection, recurrence, and autonomous jobs work.
6. Operations and tests: how OpenTelemetry, Docker Compose, pytest-asyncio, xdist, and testcontainers affect debugging and verification.
7. Memory and domain surfaces: how facts, provenance, vector search, blobs, frontend/API contracts, and roster-specific capabilities extend the core system.

Estimated effort: 57 smart-human study hours. A learner with strong Python, PostgreSQL, and distributed-systems background can skim faster; a learner new to async systems, migrations, or LLM tooling should treat the estimates as realistic.

## Mandatory Before Reading Code

These topics are mandatory before the repository becomes legible:

- MCP/FastMCP tool servers and ephemeral LLM runtime sessions.
- The daemon/session/module/connector/switchboard separation.
- Basic `asyncio` task, cancellation, timeout, and semaphore behavior.
- PostgreSQL schemas, `search_path`, roles, JSONB, and migrations.
- Identity, trust boundaries, credentials, and side-effect approval gates.

## Can Wait Until First Contribution Work

These can wait until the learner has read the architecture once:

- Calendar recurrence and projection internals.
- Memory consolidation, embeddings, and provenance.
- Frontend cache contracts and dashboard route details.
- Docker Compose deployment topology and observability backends.
- WhatsApp sidecar details and other narrow connector-specific transport concerns.

## Curriculum Overview

| Section | Why it matters | Hours | Progress |
|---|---:|---:|---|
| System boundaries | Prevents confusion about what owns reasoning, routing, tools, and transport. | 8 | [ ] |
| Async runtime and failure semantics | Explains why work is queued, retried, timed out, and classified the way it is. | 9 | [ ] |
| PostgreSQL storage and migrations | Covers the data model and migration hazards behind most safe changes. | 9 | [ ] |
| Identity, secrets, and approvals | Protects credentials, owner identity, OAuth state, and outbound side effects. | 8 | [ ] |
| Time, scheduling, and autonomous workflows | Explains cron, calendar projection, event chains, and recurrence boundaries. | 7 | [ ] |
| Observability, operations, and tests | Makes debugging and verification meaningful instead of accidental. | 8 | [ ] |
| Memory, retrieval, and domain surfaces | Covers fact storage, retrieval, blobs, API/UI contracts, and domain modules. | 8 | [ ] |

## Completion Criteria

- [ ] I can explain the system without using local file names as a crutch.
- [ ] I can trace a message from connector ingress through routing into a butler session.
- [ ] I can explain which database schema a query should target and why.
- [ ] I can identify whether a change is a runtime, migration, trust-boundary, scheduling, or UI/API contract change.
- [ ] I can choose a right-sized test scope for a first contribution.
