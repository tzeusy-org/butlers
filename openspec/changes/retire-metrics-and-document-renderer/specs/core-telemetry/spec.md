# Core Telemetry

## ADDED Requirements

### Requirement: Read-only Prometheus HTTP query helper

Core SHALL own the existing asynchronous instant and range HTTP query functions at `butlers.core.prometheus`. `async_query(url, query, time=None)` SHALL GET `<url>/api/v1/query` with `query` and with `time` only when supplied. `async_query_range(url, query, start, end, step)` SHALL GET `<url>/api/v1/query_range` with those four query parameters. Both SHALL retain the existing 30-second HTTP timeout and 10-second connect timeout, return the exact successful `data.result` list including an empty list, and represent HTTP, network, JSON/decode or unexpected in-try failures and Prometheus non-success status using the existing single-element error list. No module registration, metric-definition persistence, emission, database work, authentication mechanism, retry, alias or additional cache SHALL be introduced by relocation. A payload outside the existing success extraction contract SHALL retain its current behavior; relocation SHALL NOT claim broader schema validation. API-owned parsing, aggregation, cache and degradation contracts remain authoritative and unchanged.

ID: REQ-core-telemetry-015
Source: Applied owner decision bu-lsxqb0.5 option A (2026-10-04), bu-lsxqb0.24; original module-metrics instant/range query requirements; current src/butlers/modules/metrics/prometheus.py; RFC 0005.
Scope: v1-mandatory

#### Scenario: Instant query preserves its vector and parameters
- **WHEN** an instant HTTP query succeeds with a vector result
- **THEN** the helper returns that exact list and sends the supplied expression and optional evaluation time

#### Scenario: Omitted evaluation time remains omitted
- **WHEN** the instant helper is called without time
- **THEN** no time parameter is sent and Prometheus retains server-time evaluation

#### Scenario: Range query preserves its matrix and parameters
- **WHEN** a range HTTP query succeeds with a matrix result
- **THEN** the helper returns that exact list and sends query, start, end and step unchanged

#### Scenario: Empty successful results stay empty
- **WHEN** either endpoint returns success with an empty data.result list
- **THEN** the helper returns an empty list so its callers can apply their existing truthful-zero policy

#### Scenario: Query failures retain the existing error representation
- **WHEN** either request encounters an HTTP error, network error, invalid JSON, an unexpected in-try failure or Prometheus non-success status
- **THEN** the existing single error entry is returned without new retries or success substitution

#### Scenario: Timeout and request method remain unchanged
- **WHEN** either helper creates its HTTP client and sends a request
- **THEN** it retains total timeout 30 seconds and connect timeout 10 seconds and uses GET

#### Scenario: Relocation has no module or storage effects
- **WHEN** the core helper is imported or either query function executes
- **THEN** it adds no MCP tools, module registration, metric writes, database writes or blob lifecycle effects
