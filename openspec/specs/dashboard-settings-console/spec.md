# dashboard-settings-console

## Purpose

`dashboard-settings-console` is the new top-level Settings console page: a Dispatch-language settings shell at `/settings`. It replaces the prior single-scroll preferences stack with a panel grid of summary cards (one per Settings sub-route) prefixed by an `AttentionStrip` of items demanding human attention, framing `/settings` as the operator control plane rather than a SaaS preferences screen. The capability owns the `/settings` Console grid, the attention strip, the breadcrumb-less editorial shell, and the `GET /api/settings/console` aggregator; live updates are delivered over the unified fleet event bus (`WS /api/events/stream`), not a dedicated socket.

## Requirements

### Requirement: Settings Console Page
The dashboard SHALL have a top-level page at `/settings` rendered in the Dispatch design language. The page is a panel grid of summary cards, one per Settings sub-route, prefixed by an `AttentionStrip` of items demanding human attention.

#### Scenario: Console page layout
- **WHEN** a user navigates to `/settings`
- **THEN** the page renders, in vertical order:
  - **Page header**: title "Settings", mono eyebrow "system · console", clock (mono, `HH:MM` 24h, tabular nums).
  - **AttentionStrip**: a rule-separated list of `{id, tone: red|amber, kind, text, action_route}` items. It initially renders the capped `attention[]` view from `GET /api/settings/console`; each row uses the attention-tint pattern: 4–7% alpha background in `tone` color, paired with a 2px left rail in the same color. Rows are clickable; click navigates to `action_route`.
  - **Panel grid**: one summary panel per sub-route (`/settings/models`, `/settings/spend`, `/settings/permissions`). Each panel fetches its own summary endpoint in parallel; a slow fetch in one MUST NOT block others.
- **AND** the page uses Inter Tight (sans), JetBrains Mono (mono), Source Serif 4 (serif), and the OKLCH palette tokens already shipped in `frontend/src/index.css`; no new tokens are introduced.
- **AND** the page contains no card chrome, no drop shadows, no gradients.

#### Scenario: Inline attention overflow
- **WHEN** `attention_truncated_count > 0`
- **THEN** the strip renders an inline native control labelled `"...N more →"` that expands the omitted items from `attention_all[]` in the same strip
- **AND** the control exposes its state with `aria-expanded`, supports keyboard activation, and collapses the same local list without navigation
- **AND** the control does NOT navigate to `/audit-log` or any other route.

#### Scenario: Empty attention strip
- **WHEN** `attention_all[]` is empty
- **THEN** the strip section renders a single serif-italic line "Everything is in hand." and no rows.

#### Scenario: Panel summary load failure
- **WHEN** one panel's summary fetch fails
- **THEN** the panel renders a mono caption "Failed to load." with a `Retry →` link
- **AND** the other panels render normally.

### Requirement: Settings Console Aggregator API
The dashboard SHALL expose `GET /api/settings/console` returning aggregated header counts and the attention strip items.

#### Scenario: Console aggregator response
- **WHEN** `GET /api/settings/console` is called
- **THEN** the response is `ApiResponse[SettingsConsole]` where `SettingsConsole` contains:
  - `header_counts: {active_butlers: int | null, spend_mtd_usd: float | null, open_approvals: int | null, models_verified: int | null, models_total: int | null}` — a field is `null`, never a confident `0`, when its subsystem aggregation failed (the failure is always also surfaced as an amber `attention` item, but a header-only consumer must not have to cross-reference that list to tell "genuinely zero" from "unknown")
  - `attention: AttentionItem[]` — the cap-sized compatibility view
  - `attention_all: AttentionItem[]` — the complete ordered attention list
  - `AttentionItem = {id: str, tone: "red"|"amber", kind: str, text: str, action_route: str}` where `id` is stable and unique for each independently actionable item; `kind` is not an identity key
  - `attention_truncated_count: int` — `max(0, len(attention_all) - len(attention))`
- **AND** the server caps only `attention[]` at 5 items while returning the complete `attention_all[]`; items beyond 5 are surfaced locally through the inline overflow control.
- **AND** the response is cached server-side for 10 seconds (revalidated on cache miss). The cache is in-memory keyed by `actor` identity; in single-owner deployments the cache is effectively global.
- **AND** the response uses tabular-nums-friendly types (integers and floats; never formatted strings).

#### Scenario: Spend MTD is priced from the ledger, not a rolling-30d fan-out
- **WHEN** the aggregator computes `header_counts.spend_mtd_usd`
- **THEN** it is priced from `public.token_usage_ledger` via the shared `butlers.core.model_routing.price_mtd_from_ledger` helper — the exact helper `check_monthly_ceiling` (the spawn-deny gate) and `GET /api/spend/forecast` price MTD from (bu-7o89u.1/.2) — so this figure can never diverge from the number that halts the fleet
- **AND** it is NOT summed from a rolling-30d per-butler `sessions_summary` fan-out (the pre-bu-7o89u.2 behavior, which both mislabeled the window as "MTD" and could drive the near-ceiling attention item off a figure the gate was not actually enforcing)
- **AND** a ledger failure, or no `DatabaseManager` wired (there is no MCP fallback for ledger rows), sets `header_counts.spend_mtd_usd = null` plus an amber `subsystem_error` attention item — never a fabricated `$0`
- **AND** the "spend near ceiling" attention item (below) compares this same ledger-priced figure against the same `public.spend_ceiling` singleton row `check_monthly_ceiling` reads, so the alarm can never fire independently of what the gate is actually enforcing
- **AND** the Settings Console page's own "Spend" summary panel (which fetches its per-panel summary independently of the header aggregator) sources its "MTD" figure from `GET /api/spend/forecast`'s ledger-priced `mtd_usd`, and renders a degraded indicator (not a fabricated `$0.00`) when that response's `ceiling_source_error` is `true`

#### Scenario: Sub-system aggregation failure is reported, not propagated
- **WHEN** one sub-system aggregation fails (e.g., spend backend unavailable) while `GET /api/settings/console` is responding
- **THEN** the endpoint still returns 200 with the partial header (fields that succeeded) and the partial attention data
- **AND** the failed sub-system contributes one item `{id: "subsystem_error:<source>", tone: "amber", kind: "subsystem_error", text: "<subsystem> aggregation failed: <error_id>", action_route: "<subsystem route>"}` so the operator notices.

#### Scenario: Attention items composed from sub-systems
- **WHEN** the aggregator runs
- **THEN** it composes `attention_all[]` from:
  - Open approvals waiting for the owner (kind `open_approvals`, route `/approvals`).
  - Models with `state ∈ {error, rate-limited}` (kind `model_error`, route `/settings/models`).
  - Auth-renewal required for any CLI provider (kind `auth_renewal`, route `/secrets?focus=c:cli-auth/<provider>` with the dynamic provider segment URL-encoded as needed). Each provider has its own stable `auth_renewal:<provider>` identity.
  - Spend within 10% of the monthly ceiling (kind `spend_ceiling`, route `/settings/spend`).
  - Failed webhook deliveries in the last 24h (kind `webhook_failure`, route `/settings/permissions`).
- **AND** items are ordered with `tone="red"` first, then `tone="amber"`; `attention[]` is the five-item prefix of that order.

### Requirement: Settings Console Deltas On The Unified Fleet Event Bus
The dashboard SHALL fan Settings Console `header_delta` / `attention_add` / `attention_remove` events onto the unified fleet event bus (`WS /api/events/stream`) so a client can receive live console updates via the single shared bus connection (bu-3quv8, completing the settings-console half of bu-qvnce.14's single-socket doctrine; the earlier dedicated `WS /api/settings/stream` route was retired in bu-01r64.2 once the bus fully covered this traffic).

#### Scenario: Deltas are emitted via the shared bus
- **WHEN** the console payload changes (a header count or an attention item)
- **THEN** the backend emits the corresponding `header_delta` / `attention_add` / `attention_remove` event via `emit_event` onto `WS /api/events/stream`
- **AND** `attention_add` is an identity-keyed upsert carrying a complete `AttentionItem`, and `attention_remove` carries its stable `id`; multiple items with the same `kind` remain distinct
- **AND** this happens via a standalone background aggregation loop, independent of whether any client is connected.

#### Scenario: Dashboard client subscribes via the shared bus, not a second socket
- **WHEN** the dashboard's Settings Console page needs live header/attention updates
- **THEN** it subscribes to `"header_delta"` / `"attention_add"` / `"attention_remove"` on the shared `EventBusProvider` connection
- **AND** it does not open a dedicated settings-console socket.

#### Scenario: A missed or replayed delta converges rather than drifts
- **WHEN** a bus event is replayed from the shared bus's ring-buffer snapshot (on initial connect or reconnect), or a delta is missed entirely while disconnected
- **THEN** the client ignores replayed console-delta events and instead relies on its periodic `GET /api/settings/console` reconciliation poll to reseed the full, authoritative state
- **AND** live add/remove events update `attention_all[]` by stable identity before deriving its capped `attention[]` view and truncated count
- **AND** state converges to the server's own aggregation on that fixed cadence rather than silently drifting from an unapplied or double-applied delta.

## Source References
- Non-Negotiable Rule 1 (Composure is the brand) and Rule 4 (every element earns its place against state) from `about/heart-and-soul/design-language.md`.
- PLAN.md §4 routes contract and §5 Settings Console API surface.
- Visual reference: the `SettingsConsole` redesign prototype (graduated; now shipped in `frontend/`).
