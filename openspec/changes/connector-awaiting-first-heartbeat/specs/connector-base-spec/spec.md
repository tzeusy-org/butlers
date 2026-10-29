## ADDED Requirements

### Requirement: Awaiting-first-heartbeat evidence is presentation-only

`operational_role` SHALL remain the complete persisted authority vocabulary:
`runtime_instance | checkpoint | unknown`. `awaiting_first_heartbeat` SHALL be
a derived presentation for a subset of `unknown` rows, never a fourth role, a
stored role alias, or a dashboard-controlled assignment.

ID: REQ-connector-base-spec-009
Source: Owner-adopted PR #4046 at 1a14d530 (2026-09-15), bu-poven; connector-runtime-instance-authority presentation amendment
Scope: v1-mandatory

A reader SHALL derive `presentation_state = "awaiting_first_heartbeat"` only
when `operational_role = 'unknown'` and at least one of these fixed positive
evidence predicates is true:

- `settings IS NOT NULL`;
- `observed_scopes IS NOT NULL`;
- `registered_via = 'dashboard'`; or
- `checkpoint_cursor IS NOT NULL AND parent_endpoint_identity IS NULL`, which
  is the persisted shape of a cursor explicitly declared to use its runtime
  identity as its own key.

These predicates may overlap. Settings, OAuth-derived scope metadata,
dashboard registration, a self-owned cursor, and a metadata cache are evidence
that setup work addressed the identity; none proves that an executable process
exists or is healthy. A Google Drive row whose only settings entry is
`metadata_cache` therefore qualifies through `settings IS NOT NULL`, including
when the cached map is empty. The derivation SHALL NOT inspect the cache, scope,
settings, or cursor contents and SHALL NOT return an evidence reason.

No other column or naming convention is positive evidence. In particular,
`first_seen_at`, the default `state`, zero-valued counters, and the shape or
content of `endpoint_identity` SHALL NOT qualify a row.

#### Scenario: Fixed evidence identifies the awaiting subset without reading content

- **WHEN** an `unknown` row has non-NULL settings, non-NULL observed scopes,
  dashboard registration, or a self-owned cursor
- **THEN** its derived `presentation_state` SHALL be
  `awaiting_first_heartbeat`
- **AND** empty settings, an empty observed-scope array, an empty metadata-cache
  map inside non-NULL settings, and an opaque non-NULL cursor SHALL qualify by
  presence exactly as their non-empty counterparts do
- **AND** no settings key or value, scope name, cursor value, cache key, cache
  value, credential, or evidence-reason field SHALL be exposed by this
  presentation derivation

#### Scenario: An unexplained unknown row stays unclassified

- **WHEN** an `unknown` row satisfies none of the fixed positive predicates
- **THEN** its derived `presentation_state` SHALL be `unclassified`
- **AND** it SHALL NOT be described as awaiting, configured, active, healthy,
  stale, or offline

#### Scenario: Persisted roles take precedence over presentation evidence

- **WHEN** a row's `operational_role` is `checkpoint` or `runtime_instance`
- **THEN** it SHALL NOT derive `awaiting_first_heartbeat`, regardless of which
  evidence-bearing columns are populated
- **AND** checkpoints SHALL retain their parent or unparented presentation
- **AND** runtime instances SHALL retain heartbeat-derived liveness

#### Scenario: The first real heartbeat promotes authority

- **WHEN** a heartbeat is committed for a row presented as
  `awaiting_first_heartbeat`
- **THEN** the heartbeat SHALL set `operational_role = 'runtime_instance'`
- **AND** the derived awaiting presentation SHALL disappear
- **AND** runtime liveness SHALL be evaluated from that real heartbeat under the
  existing liveness contract

#### Scenario: Later pre-heartbeat writers cannot demote a runtime instance

- **WHEN** a heartbeat and a settings, observed-scope, metadata-cache, or
  self-owned-cursor write target the same registry row concurrently or in
  either commit order
- **THEN** the committed row SHALL converge on
  `operational_role = 'runtime_instance'` once the heartbeat commits
- **AND** the non-heartbeat writer SHALL leave `operational_role` unchanged on
  conflict
- **AND** later writes of those evidence classes SHALL NOT restore the awaiting
  presentation or demote the row to `unknown`

## Source References

- Owner-adopted PR #4046 at `1a14d530abad009ad5ea08c5d71be3887c5a094a`; bu-poven, 2026-09-15 decision.
- Runtime authority baseline: `openspec/specs/connector-base-spec/spec.md`.
- Roster contract: `openspec/specs/dashboard-ingestion-dispatch-console/spec.md`.
