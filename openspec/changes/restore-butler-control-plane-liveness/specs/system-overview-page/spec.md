## MODIFIED Requirements

### Requirement: Per-Butler Heartbeat Facts

The `/api/system/butlers/heartbeat` endpoint SHALL return the last successfully verified healthy receiver-observation
timestamp and session activity summary for each registered butler. It SHALL also
expose observation, administrative policy, route compatibility, and effective eligibility
separately. The legacy timestamp field name is retained for existing clients but its value
is the receiver's DB-server time of the last successful healthy verification, not a daemon-authored heartbeat or the latest failed probe. A separate `last_probe_at` SHALL carry the last attempted receiver probe time. Clients SHALL classify effective status from `observation_state` and `effective_eligibility`, never heartbeat age alone.

ID: REQ-system-overview-page-007
Source: RFC 0007 §System Ownership Page; docs/reviews/2026-09-23-liveness-control-plane-reliability-packet.md §5.1; [Observed] src/butlers/api/routers/system.py
Scope: v1-mandatory

#### Scenario: Heartbeat endpoint returns per-butler status

- **WHEN** `GET /api/system/butlers/heartbeat` is called
- **THEN** the response body contains:
  - `butlers: ButlerHeartbeat[]` -- one entry per registered butler, ordered by
    butler name ascending
- **AND** each `ButlerHeartbeat` entry contains:
  - `name: string` -- butler name (e.g., `"general"`, `"health"`)
  - `last_heartbeat_at: string | null` -- ISO 8601 UTC timestamp of the most recent
    successfully verified healthy receiver observation in the switchboard registry,
    or `null` if the butler has never passed verification
  - `last_probe_at: string | null` -- ISO 8601 UTC timestamp of the latest receiver
    probe attempt, successful or failed; it does not imply health
  - `last_session_at: string | null` -- ISO 8601 UTC timestamp of the most recent
    completed session for this butler, derived from `{schema}.sessions WHERE
    completed_at IS NOT NULL ORDER BY completed_at DESC LIMIT 1`. The `IS NOT NULL`
    filter is required because active sessions have `completed_at = NULL` and
    PostgreSQL sorts NULLs last by default in DESC -- omitting the filter risks
    returning an active (incomplete) session as the "last" session.
  - `active_session_count: number` -- count of sessions where `completed_at IS NULL`
    in `{schema}.sessions` at query time. Note: the sessions table has no `status`
    column; active sessions are identified by `completed_at IS NULL` (see
    `src/butlers/core/sessions.py` `sessions_active` implementation)
  - `heartbeat_age_seconds: number | null` -- seconds since `last_heartbeat_at`,
    or `null`; it does not override a newer failed probe or non-active policy
  - `observation_state`, `administrative_policy`, `route_compatibility`, and
    `effective_eligibility` -- separate content-blind control-plane facts
- **AND** the response wraps in the standard `ApiResponse<HeartbeatFacts>` envelope

#### Scenario: Heartbeat data is read from the registry, not from live MCP calls

- **WHEN** the heartbeat endpoint assembles its response
- **THEN** it reads liveness data from the switchboard's liveness registry table
  (the same source the butler list page uses)
- **AND** it does NOT issue live MCP `status` tool calls to any butler
- **AND** if a butler's liveness entry is missing from the registry (never started or
  deregistered), `last_heartbeat_at`, `last_probe_at`, and `heartbeat_age_seconds` are `null`

#### Scenario: Session facts are read via the dashboard API's existing DB fan-out

- **WHEN** the heartbeat endpoint reads per-butler session data
- **THEN** it uses the `DatabaseManager` fan-out pattern that the dashboard API already
  uses for cross-butler queries (not new ad-hoc SQL per butler)
- **AND** if a butler's schema is unreachable, that butler's `last_session_at` and
  `active_session_count` are `null` and 0 respectively, and the entry is still
  included in the response with an `error: "schema_unreachable"` flag

#### Scenario: Administrative quarantine is not a heartbeat age
- **WHEN** a daemon is freshly observed but paused or quarantined by the owner
- **THEN** the endpoint reports fresh observation and the separate non-active policy
- **AND** clients do not infer policy from `heartbeat_age_seconds`

#### Scenario: Fresh failed probe cannot look green
- **WHEN** a receiver probe fails after an earlier successful healthy observation
- **THEN** `last_probe_at` advances to the failed attempt while `last_heartbeat_at` and its age remain based on the earlier success
- **AND** `observation_state` and `effective_eligibility` report the failure and the System UI cannot classify the row healthy from a recent `last_probe_at` or legacy age alone
