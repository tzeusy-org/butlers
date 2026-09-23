## ADDED Requirements

### Requirement: Readiness probe endpoint

The dashboard-api SHALL expose a public, content-blind `GET /ready` endpoint (distinct from `/health`) that verifies PostgreSQL connectivity, expected Git-roster discovery, a fresh complete receiver-derived observer snapshot, verified current-generation identity and advertised route acceptance for every expected non-paused daemon, compatible route contracts, qualifying QA patrol freshness, Dashboard lifespan-loop state from the existing process-fenced supervised-job health projection, and the content-free effect-free Switchboard route preflight defined by `REQ-dashboard-api-063`. The Switchboard runtime-attention delivery worker is outside that Dashboard projection and retains its separate condition/outbox status. The preflight traverses production selection, policy, compatibility, and exact endpoint resolution and performs one bounded identity GET; it does not execute a target inbox transaction and cannot claim target acceptance. It SHALL return HTTP 200 with `{"ready": true}` only when all checks pass, or HTTP 503 with `{"ready": false, "checks": {...}}` when any check fails. Failure checks SHALL be fixed-name booleans for `postgres`, `roster`, `observer`, `fleet`, `qa_patrol`, `supervisors`, and `route_canary`; they SHALL NOT disclose daemon names, endpoints, credentials, event content, or raw error text. A failed or incomplete observation SHALL NOT become an all-clear. This requirement composes with `restore-butler-control-plane-liveness`; process startup or a discoverable roster row alone SHALL NOT establish control-plane readiness.

ID: REQ-dashboard-api-061
Source: `restore-butler-control-plane-liveness` §Fleet readiness; RFC 0007 §Core System Endpoints; RFC 0008 §Dashboard owner-authentication composition
Scope: v1-mandatory

#### Scenario: All dependencies healthy
- **WHEN** `GET /ready` is called and all seven functional checks pass against the current fleet generation
- **THEN** HTTP 200 is returned with `{"ready": true}`

#### Scenario: Database unreachable
- **WHEN** `GET /ready` is called and the DB pool query fails
- **THEN** HTTP 503 is returned with `ready=false` and `checks.postgres=false`
- **AND** every other fixed check reports its independently verified boolean state or `false` when unverified

#### Scenario: Running API with stale fleet is not ready
- **WHEN** the dashboard process is live but the observer snapshot is stale, QA has missed its patrol bound, or the current route canary fails
- **THEN** `GET /ready` returns HTTP 503 with the failed fixed-name checks
- **AND** `GET /health` remains a lightweight process-liveness check

#### Scenario: Readiness endpoint is public
- **WHEN** `GET /ready` is called without an API key
- **THEN** the request is allowed (no authentication required)
- **AND** the exact `GET /ready` method/path pair is allowed by the central owner-auth middleware alongside the existing public health probes

### Requirement: Liveness probe compatibility

The existing `/health` endpoint SHALL continue to return HTTP 200 as a lightweight liveness check (no dependency verification). This is used for the k8s `livenessProbe`.

ID: REQ-dashboard-api-062
Source: RFC 0007 §Core System Endpoints; RFC 0008 §Dashboard owner-authentication composition
Scope: v1-mandatory

#### Scenario: Health endpoint remains lightweight
- **WHEN** `GET /health` is called
- **THEN** HTTP 200 is returned with `{"status": "ok"}` without checking external dependencies
