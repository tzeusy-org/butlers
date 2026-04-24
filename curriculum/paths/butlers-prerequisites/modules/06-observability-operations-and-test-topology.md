# Observability, Operations, And Test Topology

Estimated smart-human study time: 8 hours

## Why This Module Matters

This repository has real operational surfaces: structured logs, OpenTelemetry traces and metrics, Docker Compose services, health gates, and integration tests with real PostgreSQL containers. Understanding failures requires knowing these tools and their constraints.

## Learning Goals

- Explain trace context across daemons, subprocesses, and MCP tool calls.
- Understand structured logging, metrics, cardinality, and redaction.
- Read Docker Compose service dependencies, health checks, and network assumptions.
- Use pytest-asyncio, xdist, testcontainers, and E2E markers safely.

## Subsection: Traces, Logs, Metrics, And Runtime Debugging

### Why This Matters Here

Requests cross process boundaries: connector, Switchboard, target butler, spawned runtime, and tool handlers. Debugging needs correlation, not isolated print statements.

### Technical Deep Dive

OpenTelemetry traces model work as spans connected by context. In async systems, context propagation can be lost when a new task or process starts. W3C trace context and explicit environment propagation allow separate processes to remain part of one trace.

Metrics summarize system behavior but must avoid high-cardinality labels such as raw message IDs or sender text. Logs provide detailed events and errors, but should redact secrets and include stable correlation fields. Together, traces, metrics, and logs answer different questions: where did time go, how often is it failing, and what happened in this instance?

### Where It Appears In The Repo

- `docs/architecture/observability.md`
- `src/butlers/core/telemetry.py`
- `src/butlers/core/metrics.py`
- `src/butlers/core/logging.py`
- `docker-compose.observability.yml`
- `tests/telemetry/test_telemetry.py`
- `tests/core/test_otel_metrics.py`

### Sample Q&A

- Q: Why can an MCP tool call appear as a new root span without extra handling?
  A: It runs in a different HTTP/async task context from the spawner unless trace context is restored.
- Q: Why avoid `request_id` as a metric label?
  A: It is high-cardinality and can make metrics storage expensive or unusable.

### Progress

- [ ] Exposed: I can define span, trace context, metric cardinality, structured log, and redaction.
- [ ] Working: I can explain how traces cross a spawned runtime boundary.
- [ ] Working: I can choose whether a debugging signal belongs in a log, metric, or span.

### Mastery Check

Target level: `working`

You should be able to follow a failing session through logs, spans, metrics, and session/process records.

## Subsection: Docker Compose, Health Gates, And Service Topology

### Why This Matters Here

Local and deployed workflows involve PostgreSQL, butlers, connectors, dashboard API, frontend, observability, OAuth gates, volumes, networks, and restart policies.

### Technical Deep Dive

Docker Compose describes a multi-service runtime graph. Services can depend on other services, expose ports, mount volumes, share networks, and declare health checks. A service being started is not the same as being ready. Health gates and startup scripts coordinate readiness, credentials, OAuth flows, and connector launch order.

Operational changes require understanding which logs are authoritative, which environment variables are bootstrap-only, how containers resolve hostnames, and how restart policy affects recovery after host or service failure.

### Where It Appears In The Repo

- `docker-compose.yml`
- `Dockerfile`
- `Dockerfile.base`
- `scripts/compose.sh`
- `scripts/dev.sh`
- `scripts/egress-firewall.sh`
- `docs/operations/`

### Sample Q&A

- Q: Why can a connector fail even after its container starts?
  A: It may still be gated on Switchboard health, credentials, OAuth state, or network reachability.
- Q: Why are container logs often more authoritative than local `logs/` files?
  A: The live process writes inside the container; local files can lag or belong to a different run.

### Progress

- [ ] Exposed: I can define service, health check, volume, network, restart policy, and readiness gate.
- [ ] Working: I can read the Compose graph for database, butlers, connectors, and dashboard services.
- [ ] Working: I can identify which log source to inspect for a live service failure.

### Mastery Check

Target level: `working`

You should be able to explain why a service is blocked at startup and which dependency, credential, or health signal to inspect first.

## Subsection: Async Tests, xdist, testcontainers, And E2E Cost

### Why This Matters Here

Tests are not just correctness checks; they encode runtime contracts. But the test infrastructure itself has constraints that can cause misleading failures.

### Technical Deep Dive

`pytest-asyncio` manages event loops for async tests and fixtures. If a DB pool is created on one loop and used on another, failures can look like product bugs. `pytest-xdist` runs tests in parallel workers; parallelism can expose shared resource assumptions. `testcontainers` starts real Docker services, which makes DB tests realistic but slower and sensitive to Docker capacity.

E2E tests that spawn real LLM sessions have cost and credentials implications. Use targeted tests for active development and broaden only when the change surface demands it.

### Where It Appears In The Repo

- `pyproject.toml`
- `conftest.py`
- `docs/testing/testing-strategy.md`
- `docs/testing/markers-and-fixtures.md`
- `docs/testing/e2e/README.md`
- `tests/`
- `roster/*/tests/`

### Sample Q&A

- Q: Why can xdist reveal failures that do not happen in serial runs?
  A: Parallel workers share limited external resources and can expose hidden ordering or isolation assumptions.
- Q: Why not run LLM E2E benchmarks for every small bugfix?
  A: They cost tokens, require credentials, and test a broader surface than many focused changes need.

### Progress

- [ ] Exposed: I can define pytest marker, event loop scope, fixture scope, xdist worker, and testcontainer.
- [ ] Working: I can choose a targeted test for a small change.
- [ ] Contribution-ready: I can recognize when a failing async DB test may be infrastructure-related.

### Mastery Check

Target level: `contribution-ready`

You should be able to choose a right-sized test command and interpret failures in light of async loop scope, Docker, and parallelism.

## Module Mastery Gate

- [ ] I can explain trace continuity and metric cardinality.
- [ ] I can read the service topology and startup gates.
- [ ] I can pick targeted versus broad verification for a change.
- [ ] I can identify test-infrastructure failure modes before assuming regression.
