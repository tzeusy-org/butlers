# Contribution Readiness

After completing the curriculum, you should be able to reason about:

- How an external event enters through a connector, becomes a canonical ingress envelope, routes through the Switchboard, and turns into a scoped butler session.
- Why modules register tools in-process while connectors stay transport-only.
- How spawned LLM CLI subprocesses remain constrained to their butler MCP endpoint.
- Which database schema owns a table, which data belongs in `public`, and when a migration must be guarded.
- Why retries, duplicate events, crash recovery, and background queues require idempotent writes.
- How identity, credentials, OAuth state, approval gates, and tool sensitivity protect owner trust and outbound side effects.
- Why scheduler advancement, calendar projection, and recurrence need timezone-aware reasoning.
- How to use traces, logs, metrics, Docker logs, and targeted tests to verify a change.

## Safer First Reading Targets

- `docs/concepts/` after Module 1.
- `docs/architecture/butler-daemon.md`, `docs/runtime/spawner.md`, and core tests after Module 2.
- `docs/architecture/database-design.md`, `docs/data_and_storage/`, and migration tests after Module 3.
- `docs/identity_and_secrets/` and approval tests after Module 4.
- `docs/runtime/scheduler-execution.md` and scheduler tests after Module 5.
- `docs/testing/`, `docs/architecture/observability.md`, and Docker scripts after Module 6.
- `docs/modules/memory.md`, selected roster tools, and frontend API docs after Module 7.

## Suggested First Contribution Categories

- Documentation clarifications that preserve existing doctrine and contracts.
- Focused tests around a narrow module/tool behavior.
- Small UI/API contract-aligned dashboard fixes after reading the relevant backend contract.
- Non-schema runtime fixes with targeted unit tests.
- Module tool changes that do not alter credential, approval, routing, or migration behavior.

## Hazard Areas

Do not modify these without contribution-ready mastery and targeted verification:

- Alembic revisions, schema role grants, `search_path`, or direct cross-schema SQL.
- Runtime adapter invocation, timeout propagation, model catalog resolution, or token ledgers.
- Connector checkpointing, route inbox handling, durable queue scanners, or idempotency keys.
- OAuth, credential storage, contact identity, owner bootstrapping, approval gates, or redaction.
- Scheduler advancement, recurrence, reminder/calendar projection, or task dispatch semantics.
- Memory fact/provenance writes, consolidation, vector search, and source episode handling.
- Test infrastructure involving event loop scope, xdist, testcontainers, or Docker service lifecycle.
