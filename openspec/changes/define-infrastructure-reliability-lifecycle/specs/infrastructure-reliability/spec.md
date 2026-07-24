# infrastructure-reliability

## Purpose

Define durable, deterministic handling for infrastructure conditions so an
outage remains visible until a complete successful observation proves recovery,
and so a continuing outage re-escalates without becoming repeated execution
work.

## ADDED Requirements

### Requirement: Canonical condition identity

The system SHALL identify an infrastructure condition by a canonical producer
`source` plus a SHA-256 `fingerprint` of a versioned, deterministically sorted
identity payload. The source SHALL be an explicit producer domain and SHALL
NOT be inferred from a `QaFinding.source_type`, connector provider/channel,
`healing_dispatch_events.butler_name`, exception fingerprint, timestamp, age,
or error prose. The identity payload SHALL include the canonical source, a
version, and only stable source-defined identity facts; object keys and
set-valued collections SHALL be recursively sorted before deterministic UTF-8
serialization and hashing.

#### Scenario: Stable evidence produces one identity
- **WHEN** a producer observes the same condition with updated timestamps,
  age text, or sanitized diagnostic prose
- **THEN** it uses the same canonical source and fingerprint
- **AND** those mutable values are retained only as evidence or metadata, not
  fingerprint input

#### Scenario: A producer changes its identity contract
- **WHEN** a producer must change the meaning or shape of condition identity
- **THEN** it increments the identity-payload version before computing the
  sorted SHA-256 payload
- **AND** it does not reinterpret prior episode identity from free-form
  evidence

#### Scenario: Mutable drift revisions remain evidence
- **WHEN** a migration-drift condition continues while expected or actual
  revision values change
- **THEN** its identity is based on the affected stable schema/chain set
- **AND** the revision values remain evidence rather than causing a new
  fingerprint solely because the diagnostic text changed

### Requirement: Episode lifecycle and complete-snapshot resolution

The system SHALL retain append-per-episode condition evidence with states
`open`, `aging`, and `resolved`. At most one `open` or `aging` episode SHALL
exist for a `(source, fingerprint)` pair. An observed condition SHALL create
an `open` episode when no active episode exists; the first due escalation SHALL
move that episode to `aging`. Only a complete successful snapshot covering the
producer's authoritative observation scope may resolve an active condition
that is absent from that snapshot. A failed, degraded, partial, or incomplete
snapshot SHALL NOT infer absence or resolve any active episode.

#### Scenario: Complete clean snapshot resolves an active episode
- **WHEN** a producer completes a successful snapshot of its authoritative
  scope and an active condition is absent
- **THEN** the active episode transitions to `resolved` exactly once
- **AND** its resolution timestamp and recovery evidence are retained

#### Scenario: Incomplete observation cannot impersonate recovery
- **WHEN** a producer report fails, is degraded, is partial, or cannot assert
  snapshot completeness
- **THEN** observed active conditions may receive confirming evidence
- **AND** no active condition absent from that report transitions to
  `resolved`

#### Scenario: Resolved condition recurs
- **WHEN** a condition with a resolved episode is observed again
- **THEN** the system creates the next episode as `open`
- **AND** it preserves the resolved episode and restarts the L0/L1 schedule
  for the new episode

### Requirement: Bounded lifecycle escalation and recurrence

The system SHALL calculate escalation from the active episode lifecycle rather
than from a permanent already-escalated marker. L0 SHALL record opening
evidence without escalation. L1 SHALL become due after the producer-owned
initial grace. L2 SHALL become due one day after L1, L3 three additional days
after L2, and a continuing L3 condition SHALL become due every seven days
after the preceding L3 action. The shared lifecycle SHALL NOT supply a global
default initial grace. It SHALL atomically claim each due transition before a
producer side effect so concurrent reconcilers cannot emit duplicate L1, L2,
or individual L3-repeat actions.

#### Scenario: First escalation follows source-owned grace
- **WHEN** an episode remains active through its producer-defined initial
  grace period
- **THEN** the lifecycle emits one L1 due transition and changes the episode
  state from `open` to `aging`
- **AND** it records the next due time for L2 one day later

#### Scenario: Continuing condition re-escalates at bounded levels
- **WHEN** an aging episode remains active after L1 and L2
- **THEN** it emits L2 one day after L1 and L3 three additional days later
- **AND** every subsequent due transition remains L3 and is scheduled seven
  days after the preceding L3 action

#### Scenario: Concurrent reconcilers see one due transition
- **WHEN** two reconcilers observe the same active episode at an escalation
  boundary
- **THEN** at most one reconciler claims that level's due transition
- **AND** the other reconciler returns a non-duplicate lifecycle result

### Requirement: Infrastructure-condition QA suppression is decision-only

The QA dispatcher SHALL evaluate an `infra_state` finding against its matching
active infrastructure condition after normal admission eligibility succeeds
and before it calls `create_or_join_attempt`. A matching `open` or `aging`
condition SHALL suppress the dispatch by recording one decision-only
`healing_dispatch_events` row for that suppressed dispatch with
`decision = infra_condition_open`, a condition reason, and null attempt
linkage. The suppression SHALL NOT create, join, delete, or mark a
`healing_attempt`; invoke an LLM; create a runtime session or worktree; or
change the existing QA source-type vocabulary.

#### Scenario: Known active InfraState finding is suppressed before attempt claim
- **WHEN** an `infra_state` finding passes normal eligibility and its explicit
  canonical source plus fingerprint match an active infrastructure condition
- **THEN** the dispatcher records `infra_condition_open` with `attempt_id =
  NULL` and returns a suppressed result before `create_or_join_attempt`
- **AND** the finding remains visible with an explicit suppression reason

#### Scenario: Resolved infrastructure condition no longer suppresses execution
- **WHEN** an otherwise eligible `infra_state` finding matches only a resolved
  condition episode
- **THEN** the infrastructure-condition suppression does not apply
- **AND** normal atomic attempt-claim behavior remains available

### Requirement: Expected-infinite dashboard loops are supervised

The dashboard lifespan SHALL supervise exactly one named expected-infinite
task for each current loop: `secrets_lifecycle`, `model_verify`,
`fleet_events_bridge`, `settings_console_delta`, `secrets_staleness`,
`migration_drift`, `calendar_sync_deadman`, `external_deadman`, and
`restore_drill`. `external_deadman` SHALL be registered only when its target
URL is configured. An unconfigured `EXTERNAL_DEADMAN_URL` SHALL be treated as
a legitimate absence, not an infrastructure condition: no external-deadman
loop is registered and the QA Staffer's existing `infra_state`
`external-deadman-stale` rule produces no finding. This requirement does not
define external-monitor provisioning, a synthetic unconfigured condition, or
any different QA staleness threshold. An ordinary return or exception from a
registered loop SHALL be logged with its name and restarted with bounded
backoff; the supervisor SHALL NOT run duplicate concurrent instances. Shutdown
cancellation SHALL cancel and await every registered loop, including calendar
deadman, and SHALL NOT restart a loop cancelled for shutdown.

#### Scenario: Unexpected loop return is restarted
- **WHEN** a named expected-infinite loop returns normally or raises an
  exception while the dashboard is not stopping
- **THEN** its supervisor records the named unexpected termination
- **AND** it restarts that one loop after bounded backoff without creating a
  concurrent duplicate

#### Scenario: Shutdown cancellation is terminal
- **WHEN** dashboard shutdown begins
- **THEN** the supervisor marks itself stopping before cancelling and awaiting
  every registered lifespan loop
- **AND** a resulting `CancelledError` produces no restart

#### Scenario: Unconfigured external deadman is a legitimate absence
- **GIVEN** `EXTERNAL_DEADMAN_URL` is unconfigured
- **WHEN** dashboard lifespan initialization and the QA Staffer's
  `infra_state` discovery source evaluate the external-deadman boundary
- **THEN** no `external_deadman` lifespan loop is registered
- **AND** no `external-deadman-stale` QA finding or synthetic infrastructure
  condition is created solely because the target is unconfigured
- **AND** external-monitor provisioning and the existing QA staleness policy
  remain outside this change

### Requirement: Heartbeat-derived connector liveness is authoritative

Every connector read model SHALL derive `online`, `stale`, and `offline` from
`last_heartbeat_at` through `derive_liveness`. The stored connector `state`
(`healthy`, `degraded`, or `error`) SHALL remain separate operational-health
evidence and SHALL NOT override heartbeat liveness. Paused and archived/deleted
records SHALL retain their explicit operator/lifecycle exclusions without being
used as liveness proof.

#### Scenario: Recent error heartbeat is online but error
- **WHEN** a connector has a recent `last_heartbeat_at` and stored
  `state = error`
- **THEN** its liveness is `online`
- **AND** its independently reported operational state remains `error`

#### Scenario: Stale healthy heartbeat is not live
- **WHEN** a connector's heartbeat age reaches a stale or offline threshold
  while its stored state remains `healthy`
- **THEN** its liveness is derived as `stale` or `offline` from the heartbeat
  timestamp
- **AND** the stored healthy state does not make it live

## Source References
- Non-Negotiable Rule 4 (deterministic daemon infrastructure and ephemeral LLM intelligence)
- Non-Negotiable Rule 7 (connector transport responsibility)
- RFC 0001 (daemon lifecycle and dispatch decision versus launched execution)
- RFC 0005 (recovery decision and execution-failure telemetry separation)
- `staffer-qa` §V1 Discovery Sources (unconfigured external-deadman target is a legitimate absence)
