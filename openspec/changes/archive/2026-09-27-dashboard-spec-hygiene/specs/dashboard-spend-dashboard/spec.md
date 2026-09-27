## MODIFIED Requirements

### Requirement: Spend Live Stream
The dashboard SHALL fan per-call spend events onto the unified fleet event bus (`WS /api/events/stream`); there is no dedicated spend socket.

#### Scenario: Stream event shape
- **WHEN** the runtime records a completed LLM call
- **THEN** an event `{type: "spend", data: {kind: "call", ts, butler, model, tokens_in, tokens_out, tokens_cached, tokens_cache_write, cost_usd, session_id, extra}}` is broadcast on `WS /api/events/stream` (token fields are `tokens_in`/`tokens_out` for the uncached buckets plus `tokens_cached`/`tokens_cache_write` for prompt-cache reads/writes, and cost is `cost_usd` in dollars, not `cost_cents`)
- **AND** the frontend appends events to the forecast chart series without re-fetching.

#### Scenario: Cache invalidation on live spend events
- **WHEN** a `"spend"` event is broadcast on `WS /api/events/stream`
- **THEN** the shared cache-patch registry (`event-cache-registry.ts`'s `spendPatch`) invalidates `["cost-summary"]`, `["daily-costs"]`, `["top-sessions"]`, `["costs-by-schedule"]`, `["spend-breakdown"]`, `["spend-rules"]`, and `["spend-forecast"]`
- **AND** each of those queries polls only on the shared cadence defined by `dashboard-shell` Requirement: Bus-Aware Poll Architecture, never on a fixed per-hook timer; live invalidation remains the primary update path
