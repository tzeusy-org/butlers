## MODIFIED Requirements

### Requirement: No Per-Event LLM Invocation

Chronicler projection adapters SHALL NOT invoke an LLM on a per-event basis.

#### Scenario: Projection adapter path forbids LLM

- **WHEN** a projection adapter runs under its scheduled job
- **THEN** it SHALL NOT make any LLM call
- **AND** guardrail tests SHALL assert the no-LLM invariant

#### Scenario: Bounded Tier 2 interpretation

- **WHEN** a Tier 2 interpretation path runs (day-close, drilldown,
  ambiguity resolution, correction assistance)
- **THEN** its input bundle SHALL be bounded to a fixed maximum size
- **AND** the call SHALL preserve provenance in output
- **AND** the call SHALL be a single LLM invocation, not a fan-out over
  events

#### Scenario: Day-close prose stays human-readable and local-day bound

- **WHEN** the scheduled day-close interpretation produces a candidate
  human-facing summary for a closed local day
- **THEN** timestamps SHALL be described in the owner's configured timezone
- **AND** raw source references, connector row IDs, truncation flags, and other
  machine-only provenance fields SHALL NOT be printed by default
- **AND** machine provenance SHALL remain available to the cache/staleness path
  from tool-call results rather than depending on prose citations
- **AND** the candidate SHALL carry a structured `date_label` equal to the
  owner-timezone local day represented by its cache key and cache window
- **AND** the cache key SHALL bind that date to the exact resolved owner IANA
  timezone, so different local-day windows cannot share a cache row or writer
  lock
- **AND** writer serialization SHALL retain the actual date and exact timezone
  tuple rather than treating a fixed-width hash as identity
- **AND** the cache writer SHALL use a documented deterministic shape predicate
  to admit only owner-facing retrospective prose, not a model judgment or a
  second LLM call

#### Scenario: Execution trace or date mismatch is not cacheable prose

- **WHEN** a day-close candidate is empty, lacks or mismatches its structured
  `date_label`, contains a machine role/tool-call/protocol payload, serialized
  object, code fence, or documented execution-planning/planning-verb preamble,
  or otherwise fails the deterministic shape predicate
- **THEN** Chronicler SHALL classify the candidate as invalid
- **AND** it SHALL NOT create or replace a renderable day-close cache entry
- **AND** it SHALL NOT invoke an LLM to repair, classify, or rewrite the
  candidate

#### Scenario: Invalid persisted cache is contained on read

- **WHEN** a cache reader encounters a previously persisted day-close row that
  fails the same deterministic admission predicate or local-day binding
- **THEN** it SHALL treat the row as invalid rather than fresh or stale
- **AND** it SHALL retain the row for audit/recovery without returning its prose
  to an owner-facing caller
- **AND** a covered, available briefing SHALL use deterministic fallback copy
  instead of the invalid prose
