# Liveness Reporting

Liveness reporting is a push-based mechanism where each butler daemon periodically sends a heartbeat signal to the Switchboard. The Switchboard uses these signals to maintain butler eligibility state (`active`, `stale`, `quarantined`), enabling the system to detect unresponsive butlers without a centralized polling service.

## ADDED Requirements

### Requirement: Daemon starts a liveness reporter after server is ready

The butler daemon SHALL start an asyncio background task (the "liveness reporter") after the FastMCP server begins accepting connections. The liveness reporter SHALL periodically send an HTTP POST to the Switchboard's heartbeat endpoint to signal that the butler is alive.

#### Scenario: Liveness reporter starts on daemon startup

WHEN the butler daemon completes its startup sequence and the FastMCP server is listening
THEN an asyncio background task for the liveness reporter MUST be started
AND the first heartbeat MUST be sent within 5 seconds of startup (immediate initial report)

#### Scenario: Liveness reporter sends periodic heartbeats

WHEN the liveness reporter is running with the default interval of 120 seconds
THEN it MUST send an HTTP POST to the Switchboard's heartbeat endpoint approximately every 120 seconds
AND the POST body MUST include the butler's name

#### Scenario: Liveness reporter continues after connection failures

WHEN the liveness reporter attempts to send a heartbeat and the Switchboard is unreachable
THEN the connection error MUST be logged at WARNING level (not ERROR, since transient unavailability is expected)
AND the liveness reporter MUST NOT terminate
AND the next heartbeat attempt MUST occur after the configured interval

#### Scenario: Liveness reporter is disabled for the Switchboard butler

WHEN the Switchboard butler daemon starts
THEN it MUST NOT start a liveness reporter background task
AND the Switchboard SHALL NOT send heartbeats to itself

---

### Requirement: Liveness reporter interval and Switchboard URL are configurable

The liveness reporter SHALL resolve the Switchboard URL from the `BUTLERS_SWITCHBOARD_URL` environment variable, defaulting to `http://localhost:40200`. The heartbeat interval SHALL default to 120 seconds and MAY be configured via `[butler.scheduler]` in `butler.toml`.

#### Scenario: Default Switchboard URL

WHEN the `BUTLERS_SWITCHBOARD_URL` environment variable is not set
THEN the liveness reporter MUST send heartbeats to `http://localhost:40200/api/heartbeat`

#### Scenario: Custom Switchboard URL from environment

WHEN the `BUTLERS_SWITCHBOARD_URL` environment variable is set to `http://switchboard:9000`
THEN the liveness reporter MUST send heartbeats to `http://switchboard:9000/api/heartbeat`

#### Scenario: Custom heartbeat interval from config

WHEN the butler starts with a `butler.toml` containing:
```toml
[butler.scheduler]
heartbeat_interval_seconds = 60
```
THEN the liveness reporter MUST use an interval of 60 seconds

#### Scenario: Default heartbeat interval

WHEN the butler starts with no `heartbeat_interval_seconds` in config
THEN the liveness reporter MUST use an interval of 120 seconds

---

### Requirement: Switchboard exposes an HTTP heartbeat endpoint

The Switchboard SHALL expose an HTTP POST endpoint at `/api/heartbeat` that accepts butler liveness reports. The endpoint SHALL update `last_seen_at` in the `butler_registry` and transition the butler to `active` if it was previously `stale`.

#### Scenario: Valid heartbeat updates last_seen_at

WHEN an HTTP POST arrives at `/api/heartbeat` with body `{"butler_name": "health"}`
AND `health` exists in the `butler_registry`
THEN `last_seen_at` for the `health` row MUST be updated to the current timestamp
AND the endpoint MUST return HTTP 200

#### Scenario: Heartbeat reactivates a stale butler

WHEN an HTTP POST arrives at `/api/heartbeat` with body `{"butler_name": "health"}`
AND the `health` butler has `eligibility_state = 'stale'`
THEN `eligibility_state` MUST be transitioned to `active`
AND `last_seen_at` MUST be updated to the current timestamp
AND the transition MUST be logged to `butler_registry_eligibility_log`

#### Scenario: Heartbeat for quarantined butler does not auto-reactivate

WHEN an HTTP POST arrives at `/api/heartbeat` with body `{"butler_name": "health"}`
AND the `health` butler has `eligibility_state = 'quarantined'`
THEN `last_seen_at` MUST be updated to the current timestamp
BUT `eligibility_state` MUST remain `quarantined`
AND the endpoint MUST return HTTP 200 with a response indicating the butler is quarantined

#### Scenario: Heartbeat for unknown butler

WHEN an HTTP POST arrives at `/api/heartbeat` with body `{"butler_name": "unknown"}`
AND `unknown` does not exist in the `butler_registry`
THEN the endpoint MUST return HTTP 404
AND no row SHALL be inserted into `butler_registry`

---

### Requirement: Switchboard runs periodic eligibility sweep

The Switchboard SHALL have a scheduled task (`eligibility-sweep`) that periodically evaluates butler liveness and transitions eligibility states based on `liveness_ttl_seconds`.

#### Scenario: Active butler exceeding TTL becomes stale

WHEN the eligibility sweep runs
AND a butler has `eligibility_state = 'active'` and `last_seen_at + liveness_ttl_seconds < now()`
THEN the butler's `eligibility_state` MUST be transitioned to `stale`
AND `eligibility_updated_at` MUST be set to the current timestamp
AND the transition MUST be logged to `butler_registry_eligibility_log` with reason `liveness_ttl_expired`

#### Scenario: Stale butler exceeding 2x TTL becomes quarantined

WHEN the eligibility sweep runs
AND a butler has `eligibility_state = 'stale'` and `last_seen_at + (2 * liveness_ttl_seconds) < now()`
THEN the butler's `eligibility_state` MUST be transitioned to `quarantined`
AND `quarantined_at` MUST be set to the current timestamp
AND `quarantine_reason` MUST be set to `liveness_ttl_expired_2x`
AND the transition MUST be logged to `butler_registry_eligibility_log`

#### Scenario: Active butler within TTL is unchanged

WHEN the eligibility sweep runs
AND a butler has `eligibility_state = 'active'` and `last_seen_at + liveness_ttl_seconds >= now()`
THEN the butler's row MUST NOT be modified

#### Scenario: Butler with null last_seen_at is not swept

WHEN the eligibility sweep runs
AND a butler has `last_seen_at` as NULL (never reported)
THEN the butler's `eligibility_state` MUST NOT be changed by the sweep
AND the butler SHALL be skipped

#### Scenario: Eligibility sweep is a TOML scheduled task

WHEN the Switchboard butler starts with its `butler.toml`
THEN a scheduled task named `eligibility-sweep` with cron `*/5 * * * *` MUST be synced to the `scheduled_tasks` table
AND the task's prompt MUST instruct the runtime to evaluate butler liveness and transition states

---

### Requirement: Liveness reporter is cancelled during graceful shutdown

The liveness reporter asyncio task SHALL be cancelled during the daemon's graceful shutdown sequence, alongside the scheduler loop cancellation.

#### Scenario: Shutdown cancels the liveness reporter

WHEN the daemon receives a shutdown signal (SIGTERM/SIGINT)
THEN the liveness reporter asyncio task MUST be cancelled
AND cancellation SHALL occur before module `on_shutdown()` calls

#### Scenario: Final heartbeat is not required on shutdown

WHEN the daemon shuts down
THEN the liveness reporter MUST NOT attempt to send a final heartbeat before stopping
AND the Switchboard SHALL detect the butler's absence via TTL expiry during the next eligibility sweep
