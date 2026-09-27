## MODIFIED Requirements

### Requirement: Token and Cost Rollup per Request ID
The system MUST aggregate token usage and cost across all sessions attributed
to a single `request_id` while distinguishing missing model pricing from an
absence of runtime usage.

#### Scenario: Rollup for a request ID
- **WHEN** `ingestion_event_rollup(request_id, sessions, pricing=None)` is called (synchronous; it aggregates the session list returned by `ingestion_event_sessions`, it does not query the database itself)
- **THEN** the result includes `total_sessions`, `total_input_tokens`, `total_output_tokens`, nullable `total_cost`, `unpriced_session_count`, `no_usage_session_count`, and a `by_butler` breakdown with per-butler token totals, nullable known-cost subtotal, and unpriced-session count.
- **AND** `unpriced_session_count` counts only sessions with usage whose model cost cannot be resolved, while `no_usage_session_count` counts sessions with neither usage nor a known stored cost.
- **AND** an all-unpriced group returns `total_cost: null` and a positive `unpriced_session_count`; a mixed group returns its known-priced subtotal with a positive count; an explicitly declared known zero returns `0.0` with a zero count.
- **AND** `GET /api/ingestion/events/{request_id}/rollup`, `GET /api/ingestion/events` list enrichment, and `GET /api/ingestion/rollup` expose the same coverage state through their API models and frontend types.
- **AND** list enrichment uses the available session lineage as its cost evidence rather than retaining a denormalized compatibility zero when that lineage is unpriced or partial.
- **AND** lazy write-back to `public.ingestion_events.cost_usd` occurs only when at least one session exists, every session has a known price, and the known subtotal is non-null; an explicitly known `0.0` MUST still be persisted.
- **AND** the rollup covers all sessions with `request_id` equal to the given value regardless of whether an `ingestion_events` row exists.

## ADDED Requirements

### Requirement: Session Cost Evidence
List and detail session projections SHALL expose cost evidence without exposing
raw runtime failures.

#### Scenario: Failed session produces no usage
- **WHEN** a failed session has no token buckets and no known stored cost
- **THEN** its `cost_evidence` is `no_usage`
- **AND** the raw persisted error text is not returned by the ingestion API

#### Scenario: Session has usage but no price
- **WHEN** a session has one or more token buckets but its model cannot be
  resolved to a cost
- **THEN** its `cost_evidence` is `unpriced`
- **AND** it contributes to `unpriced_session_count`, not
  `no_usage_session_count`

### Requirement: Replay Policy Evidence on Timeline Rows
Every ingestion list row SHALL include server-derived replay-policy evidence.

#### Scenario: Unknown policy is fail-closed in a list response
- **WHEN** source or registry data cannot resolve a row's replay policy
- **THEN** the row reports a non-actionable replay policy with a
  non-sensitive reason
- **AND** clients MUST NOT assume the row is replay-safe from missing data
