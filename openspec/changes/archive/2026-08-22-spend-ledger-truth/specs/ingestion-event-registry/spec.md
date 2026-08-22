## MODIFIED Requirements

### Requirement: Token and Cost Rollup per Request ID
The system MUST aggregate token usage and cost across all sessions attributed
to a single `request_id`.

#### Scenario: Rollup for a request ID
- **WHEN** `ingestion_event_rollup(request_id, sessions, pricing=None)` is called (synchronous; it aggregates the session list returned by `ingestion_event_sessions`, it does not query the database itself)
- **THEN** the result includes `total_sessions`, `total_input_tokens`, `total_output_tokens`, nullable `total_cost`, `unpriced_session_count`, and a `by_butler` breakdown with per-butler token totals, nullable known-cost subtotal, and unpriced-session count.
- **AND** an all-unpriced group returns `total_cost: null` and a positive `unpriced_session_count`; a mixed group returns its known-priced subtotal with a positive count; an explicitly declared known zero returns `0.0` with a zero count.
- **AND** `GET /api/ingestion/events/{request_id}/rollup`, `GET /api/ingestion/events` list enrichment, and `GET /api/ingestion/rollup` expose the same coverage state through their API models and frontend types.
- **AND** list enrichment uses the available session lineage as its cost evidence rather than retaining a denormalized compatibility zero when that lineage is unpriced or partial.
- **AND** lazy write-back to `public.ingestion_events.cost_usd` occurs only when at least one session exists, every session has a known price, and the known subtotal is non-null; an explicitly known `0.0` MUST still be persisted.
- **AND** the rollup covers all sessions with `request_id` equal to the given value regardless of whether an `ingestion_events` row exists.
