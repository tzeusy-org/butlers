# Switchboard Butler

Delta spec for decentralize-heartbeat change.

## ADDED Requirements

### Requirement: Switchboard exposes an HTTP heartbeat endpoint

The Switchboard SHALL expose an HTTP POST endpoint at `/api/heartbeat` for butler liveness reporting. This endpoint is separate from the MCP tool interface and is designed for lightweight, high-frequency liveness signals.

#### Scenario: Valid heartbeat from a registered butler

WHEN an HTTP POST arrives at `/api/heartbeat` with JSON body `{"butler_name": "health"}`
AND `health` exists in the `butler_registry`
THEN the Switchboard MUST update `last_seen_at` for the `health` row to the current timestamp
AND the endpoint MUST return HTTP 200 with body `{"status": "ok", "eligibility_state": "<current state>"}`

#### Scenario: Heartbeat transitions stale butler to active

WHEN an HTTP POST arrives at `/api/heartbeat` with JSON body `{"butler_name": "health"}`
AND the `health` butler has `eligibility_state = 'stale'`
THEN `eligibility_state` MUST be transitioned to `active`
AND `eligibility_updated_at` MUST be set to the current timestamp
AND `last_seen_at` MUST be updated to the current timestamp
AND the transition MUST be logged to `butler_registry_eligibility_log` with `previous_state = 'stale'`, `new_state = 'active'`, and `reason = 'heartbeat_received'`

#### Scenario: Heartbeat from quarantined butler updates timestamp but not state

WHEN an HTTP POST arrives at `/api/heartbeat` with JSON body `{"butler_name": "health"}`
AND the `health` butler has `eligibility_state = 'quarantined'`
THEN `last_seen_at` MUST be updated to the current timestamp
BUT `eligibility_state` MUST remain `quarantined`
AND the endpoint MUST return HTTP 200 with body `{"status": "ok", "eligibility_state": "quarantined"}`

#### Scenario: Heartbeat from unknown butler returns 404

WHEN an HTTP POST arrives at `/api/heartbeat` with JSON body `{"butler_name": "unknown"}`
AND `unknown` does not exist in the `butler_registry`
THEN the endpoint MUST return HTTP 404
AND no row SHALL be inserted or modified in `butler_registry`

#### Scenario: Malformed heartbeat request

WHEN an HTTP POST arrives at `/api/heartbeat` with a body that is not valid JSON or lacks the `butler_name` field
THEN the endpoint MUST return HTTP 422

---

### Requirement: Switchboard schedules an eligibility sweep task

The Switchboard SHALL define a TOML scheduled task that periodically evaluates butler liveness based on `liveness_ttl_seconds` and transitions eligibility states accordingly.

#### Scenario: Eligibility sweep task is defined in TOML

WHEN the Switchboard butler starts with its `butler.toml`
THEN a `[[butler.schedule]]` entry named `eligibility-sweep` with cron `*/5 * * * *` MUST be present
AND it MUST be synced to the `scheduled_tasks` table with `source = 'toml'`

#### Scenario: Sweep transitions active butler to stale on TTL expiry

WHEN the eligibility sweep executes
AND a butler has `eligibility_state = 'active'` and `now() - last_seen_at > liveness_ttl_seconds`
THEN the butler's `eligibility_state` MUST be set to `stale`
AND `eligibility_updated_at` MUST be set to the current timestamp
AND a row MUST be inserted into `butler_registry_eligibility_log` with `reason = 'liveness_ttl_expired'`

#### Scenario: Sweep transitions stale butler to quarantined on 2x TTL expiry

WHEN the eligibility sweep executes
AND a butler has `eligibility_state = 'stale'` and `now() - last_seen_at > 2 * liveness_ttl_seconds`
THEN the butler's `eligibility_state` MUST be set to `quarantined`
AND `quarantined_at` MUST be set to the current timestamp
AND `quarantine_reason` MUST be set to `liveness_ttl_expired_2x`
AND `eligibility_updated_at` MUST be set to the current timestamp
AND a row MUST be inserted into `butler_registry_eligibility_log` with `reason = 'liveness_ttl_expired_2x'`

#### Scenario: Sweep skips butlers within TTL

WHEN the eligibility sweep executes
AND a butler has `eligibility_state = 'active'` and `now() - last_seen_at <= liveness_ttl_seconds`
THEN the butler's row MUST NOT be modified

#### Scenario: Sweep skips butlers with null last_seen_at

WHEN the eligibility sweep executes
AND a butler has `last_seen_at = NULL`
THEN the butler MUST NOT be transitioned by the sweep

## MODIFIED Requirements

### Requirement: discover populates the butler registry on startup

The Switchboard SHALL call `discover()` on startup to populate the butler registry by scanning butler config directories. Discovered butlers SHALL NOT include the Heartbeat Butler, which has been removed from the system.

#### Scenario: Startup discovery with multiple butler config directories

WHEN the Switchboard starts up and butler config directories exist for `general`, `relationship`, and `health`
THEN `discover()` MUST be invoked automatically
AND the `butler_registry` table MUST contain one row for each discovered butler with the correct `name`, `endpoint_url`, `description`, and `modules` parsed from each butler's `butler.toml`

#### Scenario: Startup discovery sets registered_at

WHEN `discover()` inserts a new butler into the registry
THEN `registered_at` MUST be set to the current timestamp

## REMOVED Requirements

### Requirement: Heartbeat Butler configuration

**Reason**: The Heartbeat Butler has been replaced by the internal scheduler loop (each butler drives its own `tick()`) and push-based liveness reporting (each butler reports to the Switchboard). There is no longer a centralized infrastructure butler that ticks other butlers.

**Migration**: Remove the `roster/heartbeat/` directory. Drop the `butler_heartbeat` database. Delete or close all Heartbeat-related beads and tests.
