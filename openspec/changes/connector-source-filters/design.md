## Context

Every message that clears the connector's existing label/tier pipeline is submitted to the Switchboard, which spawns LLM inference. In practice, a significant portion of incoming email and Telegram messages originates from newsletter services, automated bots, and high-volume senders that the user never wants processed. The existing `LabelFilterPolicy` in `gmail_policy.py` handles Gmail-label-based exclusion but is not surfaced in the UI and has no equivalent for Telegram or other connectors.

This change adds a generic, connector-agnostic filter registry with a UI. Filters are authored once and reused across connectors. Enforcement is done at the connector level before any Switchboard call.

## Goals / Non-Goals

**Goals:**
- Named, reusable filter objects stored in the switchboard DB (`source_filters` table)
- Per-connector filter assignment with `enabled` toggle and `priority` ordering (`connector_source_filters` join table)
- Enforcement in all connectors: messages that fail the filter gate are dropped before Switchboard ingest
- REST API for CRUD + assignment management
- Frontend UI: assign filters per connector; create/edit/delete named filters
- Support multiple source key types: `domain`, `sender_address`, `substring` (email); `chat_id` (Telegram); extensible without schema changes

**Non-Goals:**
- Per-message content filtering (body/subject text matching) — this belongs in triage rules
- Gmail label filtering (already handled by `LabelFilterPolicy` in `gmail_policy.py`; these are separate gate layers)
- Real-time filter push to connectors (TTL-based polling refresh is sufficient)
- Filter analytics / per-filter block counters exposed in the dashboard (Prometheus counter only for now)

## Decisions

### D1: `source_key_type` as open TEXT, not a DB CHECK enum

Constraining `source_key_type` to a DB-level enum would require a migration every time a new connector type is supported. Instead, `source_key_type` is an unconstrained TEXT column; valid values per channel are enforced at the API layer (422 if invalid for the connector's channel) and at the connector runtime (unknown key types log a warning and the filter is skipped). This keeps the schema stable as new connectors are added.

**Alternative considered:** A separate `source_key_types` lookup table. Rejected — over-engineered for a short, stable set of values per channel.

### D2: Shared `source_filter.py` module for evaluation logic

Rather than implementing filter loading and evaluation independently in each connector (gmail.py, telegram_bot.py, etc.), a shared `src/butlers/connectors/source_filter.py` module provides:
- `SourceFilterSpec` dataclass (in-memory representation of a filter)
- `SourceFilterEvaluator` that loads filters from DB, manages TTL cache, and exposes `evaluate(key_value: str) -> FilterResult`
- `FilterResult(allowed: bool, reason: str)`

Each connector instantiates one `SourceFilterEvaluator` for its `(connector_type, endpoint_identity)` pair and calls it per message. This avoids duplicated DB query logic and ensures consistent semantics across connectors.

### D3: Blacklist/whitelist composition when multiple filters are active

When a connector has multiple active filters of mixed modes, the evaluation order is:
1. **Blacklist filters evaluated first** (all active blacklists, in priority order): if any blacklist pattern matches → drop.
2. **Whitelist filters evaluated next**: if any whitelist filter is active AND the message matches none of them → drop.
3. If no filters are active → pass (opt-in, safe default).

This composition rule means blacklist always wins over whitelist (you can explicitly exclude something that a whitelist would otherwise allow). It is deterministic and easy to reason about.

**Alternative considered:** Separate whitelist and blacklist passes independently and OR the results. Rejected — counter-intuitive when mixing modes.

### D4: TTL-based cache refresh, not push

The `SourceFilterEvaluator` caches the filter set in memory with a configurable TTL (default 300 s via `CONNECTOR_FILTER_REFRESH_INTERVAL_S`). On TTL expiry, the next call to `evaluate()` triggers an async re-fetch from DB. This means filter changes take effect within one TTL window without a connector restart.

**Alternative considered:** Pushing filter updates to connectors via the heartbeat response payload. Rejected — adds coupling between the heartbeat and filter subsystems; TTL polling is simpler and sufficient given the low update frequency of filter config.

### D5: Connector DB access for filter loading

Connectors already have read access to the shared switchboard DB for credential resolution (`BUTLER_SHARED_DB_NAME`). Filter loading uses the same asyncpg connection pool (the `switchboard` schema is accessible from the connector's DB connection). No new credentials or connection pools are needed.

### D6: Filter API lives in the switchboard API router

The `source_filters` and `connector_source_filters` tables live in the switchboard DB schema. Their REST API is co-located in `roster/switchboard/api/router.py` alongside the existing connector registry endpoints. This keeps all connector management surface in one place.

## Risks / Trade-offs

[Risk: Filter TTL creates a gap window] → A user adds a blocking filter but the connector still ingests matching messages for up to one TTL interval. Mitigation: default TTL of 300 s is acceptable for config changes; reduce TTL if latency requirements tighten. Document the behavior in the UI ("changes take effect within 5 minutes").

[Risk: `source_key_type` validation is API-layer only] → A filter created via direct DB insert with an invalid key type would silently be skipped at the connector. Mitigation: the API rejects invalid key types with 422; direct DB access is an operator concern.

[Risk: Mixed blacklist/whitelist filters are confusing] → If a user accidentally enables a whitelist filter alongside blacklists, the AND-composition may block unexpected messages. Mitigation: the UI shows the composed effective policy ("X of Y messages blocked") with a preview indicator; document the composition rules in the Manage Filters panel.

[Risk: Frontend Filters button breaks ConnectorCard link navigation] → The Filters button sits inside an `<a>` tag (ConnectorCard is a Link). A click on the button would navigate away. Mitigation: `stopPropagation()` on the Filters button click; use a separate action area outside the link hitbox.

## Migration Plan

1. Apply switchboard migration `sw_026` (`026_create_source_filters.py`) — creates both tables, no data changes.
2. Deploy updated switchboard API router — new endpoints are additive, no breaking changes to existing API surface.
3. Deploy updated connectors — new filter evaluation is inert until filters are configured (no active filters = pass all, same as current behavior).
4. Deploy updated frontend — Filters button appears on all ConnectorCards; clicking it shows an empty filter list with a "No filters configured" empty state.
5. Rollback: disable the Filters UI toggle in frontend config; the backend tables and API can remain without affecting existing behavior.

## Open Questions

- Should filter changes be logged to the operator audit log (`operator_audit_log` table) for traceability? Tentatively yes — use the existing audit log infrastructure.
- Should the heartbeat response eventually carry the filter ETag so connectors can skip the re-fetch when nothing changed? Out of scope for this change; worth a follow-up.
- Discord connector: does it exist in production? If so, what is the appropriate source key type (guild_id? user_id? channel_id?)? The spec uses `channel_id` as a placeholder; confirm before implementation.
