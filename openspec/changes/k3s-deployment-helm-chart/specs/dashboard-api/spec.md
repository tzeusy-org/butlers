## ADDED Requirements

### Requirement: Readiness probe endpoint

The dashboard-api SHALL expose a `GET /ready` endpoint (distinct from `/health`) that verifies critical dependencies are reachable before reporting the pod as ready to receive traffic.

The readiness check SHALL verify:
1. PostgreSQL connection pool is open and a simple query (`SELECT 1`) succeeds
2. At least one butler is discoverable in the roster

The endpoint SHALL return HTTP 200 with `{"ready": true}` when all checks pass, or HTTP 503 with `{"ready": false, "checks": {...}}` when any check fails.

#### Scenario: All dependencies healthy
- **WHEN** `GET /ready` is called and the DB pool is connected and roster discovery finds at least one butler
- **THEN** HTTP 200 is returned with `{"ready": true}`

#### Scenario: Database unreachable
- **WHEN** `GET /ready` is called and the DB pool query fails
- **THEN** HTTP 503 is returned with `{"ready": false, "checks": {"postgres": false, "roster": true}}`

#### Scenario: Readiness endpoint is public
- **WHEN** `GET /ready` is called without an API key
- **THEN** the request is allowed (no authentication required)
- **AND** `/ready` is added to the `_PUBLIC_PATHS` frozenset alongside `/health` and `/api/health`

### Requirement: Liveness probe compatibility

The existing `/health` endpoint SHALL continue to return HTTP 200 as a lightweight liveness check (no dependency verification). This is used for the k8s `livenessProbe`.

#### Scenario: Health endpoint remains lightweight
- **WHEN** `GET /health` is called
- **THEN** HTTP 200 is returned with `{"status": "ok"}` without checking external dependencies
