# dashboard-settings-console

## Purpose

`dashboard-settings-console` is the new top-level Settings console page: a Dispatch-language settings shell at `/settings`. It replaces the prior single-scroll preferences stack with a panel grid of summary cards (one per Settings sub-route) prefixed by an `AttentionStrip` of items demanding human attention, framing `/settings` as the operator control plane rather than a SaaS preferences screen. The capability owns the `/settings` Console grid, the attention strip, the breadcrumb-less editorial shell, the `GET /api/settings/console` aggregator, and the `WS /api/settings/stream` ticker.

## Requirements

### Requirement: Settings Console Page
The dashboard SHALL have a top-level page at `/settings` rendered in the Dispatch design language. The page is a panel grid of summary cards, one per Settings sub-route, prefixed by an `AttentionStrip` of items demanding human attention.

#### Scenario: Console page layout
- **WHEN** a user navigates to `/settings`
- **THEN** the page renders, in vertical order:
  - **Page header**: title "Settings", mono eyebrow "system · console", clock (mono, `HH:MM` 24h, tabular nums).
  - **AttentionStrip**: a rule-separated list of `{tone: red|amber, kind, text, action_route}` items drawn from `GET /api/settings/console` `attention[]`. Each row uses the attention-tint pattern: 4–7% alpha background in `tone` color, paired with a 2px left rail in the same color. Rows are clickable; click navigates to `action_route`.
  - **Panel grid**: one summary panel per sub-route (`/settings/models`, `/settings/spend`, `/settings/permissions`). Each panel fetches its own summary endpoint in parallel; a slow fetch in one MUST NOT block others.
- **AND** the page uses Inter Tight (sans), JetBrains Mono (mono), Source Serif 4 (serif), and the OKLCH palette tokens already shipped in `frontend/src/index.css`; no new tokens are introduced.
- **AND** the page contains no card chrome, no drop shadows, no gradients.

#### Scenario: Empty attention strip
- **WHEN** `attention[]` is empty
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
  - `header_counts: {active_butlers: int, spend_mtd_usd: float, open_approvals: int, models_verified: int, models_total: int}`
  - `attention: AttentionItem[]` where `AttentionItem = {tone: "red"|"amber", kind: str, text: str, action_route: str}`
  - `attention_truncated_count: int` — items beyond the cap (0 if `attention.length <= 5`)
- **AND** the server caps `attention[]` at 5 items; items beyond 5 are surfaced via `attention_truncated_count` so the UI can render a `"...N more →"` indicator linking to `/audit-log`.
- **AND** the response is cached server-side for 10 seconds (revalidated on cache miss). The cache is in-memory keyed by `actor` identity; in single-owner deployments the cache is effectively global.
- **AND** the response uses tabular-nums-friendly types (integers and floats; never formatted strings).

#### Scenario: Sub-system aggregation failure is reported, not propagated
- **WHEN** one sub-system aggregation fails (e.g., spend backend unavailable) while `GET /api/settings/console` is responding
- **THEN** the endpoint still returns 200 with the partial header (fields that succeeded) and the partial `attention[]` array
- **AND** the failed sub-system contributes one `attention` item `{tone: "amber", kind: "system", text: "<subsystem> aggregation failed: <error_id>", action_route: "<subsystem route>"}` so the operator notices.

#### Scenario: WebSocket requires authentication at upgrade
- **WHEN** a client requests `WS /api/settings/stream` without a valid `?api_key=<value>` query param matching the server's `DASHBOARD_API_KEY`
- **THEN** the upgrade is refused with `401 Unauthorized` (or the WS-equivalent close code 4401 if upgrade has already happened).
- **AND** the same auth requirement applies to `WS /api/spend/stream` and `WS /api/approvals/stream`.

#### Scenario: Attention items composed from sub-systems
- **WHEN** the aggregator runs
- **THEN** it composes `attention[]` from:
  - Open approvals waiting for the owner (kind `approval`, route `/approvals`).
  - Models with `state ∈ {error, rate-limited}` (kind `model`, route `/settings/models`).
  - Auth-renewal required for any CLI provider (kind `auth`, route `/secrets`).
  - Spend within 10% of the monthly ceiling (kind `spend`, route `/settings/spend`).
  - Failed webhook deliveries in the last 24h (kind `webhook`, route `/settings/permissions`).
- **AND** items are ordered with `tone="red"` first, then `tone="amber"`.

### Requirement: Settings Console Live Stream
The dashboard SHALL expose `WS /api/settings/stream` emitting incremental updates that mirror the fields returned by `GET /api/settings/console`.

#### Scenario: Stream emits typed events
- **WHEN** a client connects to `WS /api/settings/stream`
- **THEN** the server emits JSON events of shape `{type: "header_delta"|"attention_add"|"attention_remove", payload: object}`.
- **AND** the client may apply these events incrementally without re-fetching `GET /api/settings/console`.

#### Scenario: Stream disconnect and reconnect
- **WHEN** a client reconnects after a disconnect
- **THEN** the first event delivered SHALL be a full `header_delta` containing the current snapshot
- **AND** subsequent events resume incremental delivery.

## Source References
- Non-Negotiable Rule 1 (Composure is the brand) and Rule 4 (every element earns its place against state) from `about/heart-and-soul/design-language.md`.
- PLAN.md §4 routes contract and §5 Settings Console API surface.
- Visual reference: the `SettingsConsole` redesign prototype (graduated; now shipped in `frontend/`).
