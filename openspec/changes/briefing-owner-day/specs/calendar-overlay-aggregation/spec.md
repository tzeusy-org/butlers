## MODIFIED Requirements

### Requirement: Overlay Contribution Schema and State Key Convention

ID: REQ-calendar-overlay-aggregation-006
Source: bu-5ii4gj.5.1 (run 26 move 5)
Scope: v1-mandatory

Each contributing specialist butler MUST write its daily overlay contribution as a JSON envelope with fields `butler` (string, butler name), `date` (string, ISO date YYYY-MM-DD), `has_entries` (boolean), and `entries` (array of entry objects), under a state key matching `calendar/overlay/<YYYY-MM-DD>` where the date is the target calendar date in the owner's configured timezone (falling back to SGT (UTC+8) when none is usable). Each entry object SHALL have `kind` (string), `label` (string), and `priority` (one of `"high"`, `"medium"`, `"low"`), plus an optional kind-specific `meta` object. The v1 envelope MUST NOT contain a generated-prose `summary` field (the narrative layer is deferred).

#### Scenario: Envelope with entries written under the date key
- **WHEN** a contributing specialist has domain-relevant events for a target date
- **THEN** it writes an envelope with `has_entries=true` and a non-empty `entries` array to its state store under key `calendar/overlay/<date>`
- **AND** entries are ordered by priority descending (`"high"` first, then `"medium"`, then `"low"`)

#### Scenario: Envelope with no entries is still written (honest empty-state)
- **WHEN** a contributing specialist has no domain events for a target date in its lookahead window
- **THEN** it writes an envelope with `has_entries=false` and an empty `entries` array under `calendar/overlay/<date>`
- **BECAUSE** persisting the empty envelope lets the read layer distinguish "job ran, nothing found" from "job has not run"

#### Scenario: Key upserts stale entry
- **WHEN** the contribution job runs and an envelope for a given date already exists
- **THEN** the existing entry is overwritten via `state_set` (upsert semantics)

#### Scenario: Pruning removes old entries
- **WHEN** the contribution job completes its writes
- **THEN** it deletes all `calendar/overlay/*` state entries whose date suffix is older than the retention window
- **AND** when there are no entries to prune, the prune step completes as a no-op

#### Scenario: v1 envelope carries no generated prose
- **WHEN** a v1 overlay envelope is written
- **THEN** it contains no `summary` (generated-prose) field
- **BECAUSE** RFC-0020 adopted the no-LLM structured variant; the batched pre-rendered narrative layer is deferred
