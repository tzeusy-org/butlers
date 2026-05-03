## ADDED Requirements

### Requirement: Instance Facts Internal Interface

Each butler daemon SHALL expose an internal interface by which the dashboard API layer
can read instance-level facts about that butler. These facts are already computed by the
daemon during normal operation (heartbeat, liveness registration, session creation);
this requirement codifies the contract so the System Overview page aggregator has a
normative interface to consume.

This requirement covers only the contractual shape of the data the System page expects.
The physical access path (liveness registry table, `{schema}.sessions` table, asyncpg
pool stats) is documented in the `system-overview-page` spec. This requirement defines
what the daemon is responsible for maintaining.

#### Scenario: Heartbeat registration is kept current

- **WHEN** a butler daemon is running
- **THEN** its heartbeat task fires at least once every `liveness_ttl_s / 2` seconds
- **AND** each heartbeat upserts the butler's liveness record in the switchboard
  liveness registry with the current UTC timestamp
- **AND** if the heartbeat task fails, the butler logs the failure but does not shut
  down -- liveness degradation is observable but not fatal

#### Scenario: Session completion updates the per-butler session record

- **WHEN** an ephemeral LLM session completes (success or failure)
- **THEN** the session row in `{schema}.sessions` is updated with:
  - `completed_at: timestamptz` -- the UTC timestamp at session completion
  - `status: "completed" | "failed"` -- terminal status
- **AND** this row is the source of truth for `last_session_at` in the System page
  heartbeat endpoint

#### Scenario: Active session count is derivable from the sessions table

- **WHEN** the System page queries active sessions for a butler
- **THEN** it derives the count from `SELECT COUNT(*) FROM {schema}.sessions WHERE
  status = 'running'` -- no dedicated active-session counter table is required
- **AND** this query is safe to run concurrently with session creation and completion
  without locking

#### Scenario: DB connection pool stats are not exposed in v1

- **WHEN** the System page reads per-butler facts in v1
- **THEN** asyncpg connection pool statistics (min_size, max_size, pool_size, in-use
  connections) are NOT surfaced via the System page endpoints
- **AND** this is a deliberate v1 simplification -- pool stats require in-process
  access that the dashboard API layer does not have without an additional internal
  API
- **AND** pool stats are marked as a forward-path item to be addressed if the System
  page adds real-time resource monitoring

## Source References

- `butler-base-spec` existing Requirement: Butler as Architectural Primitive --
  the heartbeat task and liveness reporter are described in the daemon lifecycle
  phase list; this ADDED requirement makes the contract for those tasks explicit
  rather than implicit.
- `system-overview-page` spec (this change): defines the consumer of these facts.
