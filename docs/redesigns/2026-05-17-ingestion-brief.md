# ingestion redesign — integration brief

> **SUPERSEDED 2026-05-17** — this brief is fully superseded by the in-flight OpenSpec change `openspec/changes/redesign-ingestion-dispatch-console/`, which had already completed four Phase 1 review rounds + four Phase 2 agent passes, authored 9 capability specs, and shipped one prerequisite (bu-ty7gh, PR #1718) before this brief was written. A reconciliation diff confirmed zero load-bearing gaps in either direction; every open question in §5 is either resolved by the change's specs or explicitly deferred as non-goal. Retain this brief as a Phase D audit artifact only. **Source of truth for implementation: `openspec/changes/redesign-ingestion-dispatch-console/`.**
>
> Root cause of the duplicate run: `/butlers-redesign-prompt` Phase 0 iteration detection checked `bd list` and `docs/redesigns/` but did NOT check `openspec/changes/`. Filed as a parent-skill hole; do not propagate.

**Date:** 2026-05-17
**Version:** v1
**Bundle path:** `pr/overview/ingestion-redesign/`
**Mode:** fresh
**Phase D verdict:** clear (superseded — see banner above)
**Prior brief (if any):** None — but see banner re prior OpenSpec change

---

## 0. Design intent

> Captured via Phase 0.5 user questions; the user provided a high-level WHY and explicitly deferred specific design moves and rejections to the bundle (`INGESTION_HANDOFF.md` + `DESIGN_LANGUAGE.md`). The bundle thus functions as the de facto detailed intent for the soft gate Phase D applied.

### Problem being solved

The current `/ingestion` page is proof-of-concept-grade and reads as such. The redesign overhauls it to production-grade design quality so the owner can monitor and triage ingested items without the surfaces feeling like a prototype.

### Primary audience

Owner first; operators secondary.

### Deliberate design moves

The user did not enumerate these directly. Per Phase 0.5, the bundle's `INGESTION_HANDOFF.md` + `DESIGN_LANGUAGE.md` are the binding source for specific moves. Bundle-declared moves Phase A extracted:

- **Three nested routes** (`/ingestion`, `/ingestion/connectors`, `/ingestion/filters`) under a sticky sub-nav matching the `/butlers/{butler}` shell — same shape as the existing detail surface, learnability dividend.
- **Dispatch visual language** (hairline rules, no card chrome, mono numerals, butler hues on letter-marks only) — production polish without bespoke chrome per component.
- **Financial-statement metaphor for the Timeline ledger** — every external item top-to-bottom with end-to-end pipeline detail behind click-to-expand. Reads like a record, not a feed.
- **Per-event drawer with three tabs** (flame / raw / replay history) — debugging surface that puts payload, butler topology, and idempotency state in one place.
- **Pipeline funnel diagram on `/ingestion/filters`** — visualizes the 5-gate flow (accept → dedupe → tier → route → execute) as the page's narrative spine, not a buried metric.

### What we are deliberately NOT doing

The user did not enumerate. SOFT gate Phase D applied:

- No card chrome, no transforms on hover, no emoji in chrome, no gradients/glassmorphism — per `DESIGN_LANGUAGE.md` "Hard do not" list.
- No AI-narrative captions, no "smart" suggestions, no live LLM recompose — confirmed `clear` by Phase D against every component (zero LLM-driven affordances in the bundle).
- Butler hues live on letter-marks only — never on buttons, backgrounds, or borders.
- No history *tab* — "history" is the Timeline with a wider time range (`INGESTION_HANDOFF.md` §1a).
- No count-up animations, no shimmer/skeleton-pulse, no "delight" micro-interactions.

### Success criteria

The user did not enumerate. Implicit criterion Phase D applied:

- Owner can use `/ingestion` to monitor + triage ingested items at production polish, without any PoC-feeling surface remaining.
- Zero new LLM cost surprises in the dashboard layer (Phase D verified $0/user/day across all components).
- Switchboard's identity (`MANIFESTO.md:9–23`) is preserved or strengthened — Phase D confirmed.

---

## 1. Scope

This redesign replaces the dashboard `/ingestion` surface (currently a single page with query-param tabs at `frontend/src/pages/IngestionPage.tsx`) with a nested-route shell matching the `/butlers/{butler}` shape. The visual language is **Dispatch** (binding: `pr/overview/ingestion-redesign/DESIGN_LANGUAGE.md`). The integration target is the live `frontend/` React app (Vite + React 18 + react-router v7 + TanStack Query + shadcn/Tailwind v4). Backend touches Switchboard's ingestion router and General butler for system-wide rules/contacts/defaults — no other butlers' APIs change.

### Sub-pages

| Route | Source file(s) | Purpose (one sentence) | Sticky-nav parent? |
|-------|---|---|---|
| `/ingestion` | `ingestion-app.jsx`, `ingestion-v1.jsx` | Timeline view showing all ingestion events in chronological order with click-to-expand drawer for debugging. | Page shell wraps all routes; matches `/butlers/{butler}` shape with sticky sub-nav. |
| `/ingestion/connectors` | `ingestion-app.jsx`, `ingestion-connectors-a.jsx` | Roster of all configured channels with health dots, 24h sparklines, auth status, and an "available but not connected" section. | Same sticky sub-nav. |
| `/ingestion/connectors/:connectorType/:endpointIdentity` | `ingestion-app.jsx`, `ingestion-connector-detail.jsx` | Per-connector detail page (Spotify reference implementation) showing KPIs, incident log, OAuth scopes, scheduling knobs, and reauth callout. | Same sticky sub-nav; breadcrumb navigation back to roster. |
| `/ingestion/filters` | `ingestion-app.jsx`, `ingestion-filters.jsx` | Pipeline explanation page showing five sequential gates (accept → dedupe → tier → route → execute) with funnel diagram, per-gate rules, priority contacts, and channel defaults. | Same sticky sub-nav. |

### Design tokens (binding)

#### Color

**Dark palette:**
- `--bg`: `oklch(0.145 0 0)` — page background
- `--bg-elev`: `oklch(0.205 0 0)` — elevated surfaces (code blocks, tooltips)
- `--bg-deep`: `oklch(0.115 0 0)` — sidebar, sticky bars
- `--fg`: `oklch(0.985 0 0)` — primary text
- `--mfg`: `oklch(0.708 0 0)` — muted text, eyebrows
- `--dim`: `oklch(0.55 0 0)` — tertiary text, deltas
- `--border`: `oklch(1 0 0 / 0.10)` — hairline rules
- `--border-soft`: `oklch(1 0 0 / 0.06)` — list separators
- `--border-strong`: `oklch(1 0 0 / 0.18)` — buttons, link underlines
- `--red`: `oklch(0.685 0.250 29)` — errors, reauth, blockers
- `--amber`: `oklch(0.810 0.185 84)` — degraded, medium severity
- `--green`: `oklch(0.790 0.195 148)` — healthy, positive

**Light palette:** Paper-warm (oklch hue 85), not stark white. Full values in `primitives.jsx:23–41`.

**Butler category hues** (reserved for letter-marks only; never buttons, borders, backgrounds):
- `--category-1` (relationship), `--category-2` (memory*), `--category-3` (calendar*), `--category-4` (health), `--category-5` (household/home), `--category-6` (education), `--category-7` (qa), `--category-8` (chronicler).
- (*) Phase D flagged `memory` and `calendar` keys as having no roster manifesto; see Section 4 manifesto updates.
- Additional Ingestion-specific hues exist for switchboard, lifestyle, and extended chronicler context (`ingestion-data.jsx`).

#### Typography

**Font families:**
- **Inter Tight** — all UI (display, body, labels, numbers in interfaces)
- **Source Serif 4** — voice/prose (LLM elaborations, empty states, "why" glosses)
- **JetBrains Mono** — times, IDs, deltas, KPI numbers, eyebrows, code, file paths

**Type scale:**

| Role | Family | Size | Weight | Tracking | Leading |
|---|---|---|---|---|---|
| Display | sans | 44px | 500 | -0.025em | 1.08 |
| Title | sans | 24–28px | 500 | -0.015 to -0.02em | 1.2 |
| Body | sans | 13–14px | 400 | normal | 1.5 |
| Voice (serif) | serif | 16px | 400 | normal | 1.55–1.6 |
| Voice italic (empty states) | serif | 12–16px | 400 italic | normal | 1.5–1.55 |
| Eyebrow | mono | 10px | 400 | 0.14em | 1.0 |
| Mono inline | mono | 11px | 400 | 0.01em | 1.4 |
| Mono small (tags, labels) | mono | 9–10px | 400 | 0.06–0.14em | 1.0 |

**Numeric rule:** All numeric values get `font-variant-numeric: tabular-nums` (utility class `.tnum` applies this).

#### Spacing & rhythm

**Base unit:** 4px. Common values: 4, 6, 8, 10, 12, 14, 16, 18, 24, 32, 36, 48, 56px.

**Page layout:**
- Page padding: `48px 56px`
- Column max-width: 1280px or 1500px (ingestion uses 1500)
- Section gutter (between major columns): 56px
- Two-column editorial grid (when used): `1.4fr 1fr` gap `56px`

**List row padding:** 8–18px vertical depending on importance.

#### Motion

**Allowed animations only:**
- Briefing paragraph cross-fade: `200ms cubic-bezier(0.22, 1, 0.36, 1)`
- Sidebar chevron rotation: `120ms linear`
- Theme toggle fade: `200ms ease`
- Tooltip appear/disappear: `0ms` (instant)

**Forbidden:** Spring physics, bounce, parallax, scale-in, scale-on-hover, shimmer, skeleton-pulse, count-up, "delight" micro-interactions.

#### Hard "do not" list

1. **No card chrome.** Hairlines + rhythm, never bordered/shadowed cards (`DESIGN_LANGUAGE.md` §9:369–386).
2. **No transforms on hover.** Row hover is tint only (6% white dark / 5% black light).
3. **No emoji in interface chrome.** Use ASCII glyphs (●, ◐, ■, ◇, ›) or stroke-only SVG icons.
4. **Butler hues appear only on letter-marks.** Never on buttons, hover states, borders, or backgrounds.
5. **State colors (red/amber/green) foreground/border only.** Never as background fills.
6. **No box-shadow except on the briefing-style pill** (sparingly).
7. **Display weight is 500, never 700.** Tight tracking does the work.
8. **No gradients, glassmorphism, or nested cards.**
9. **No "Pro" badges, "New" badges, version stickers, or "delight" animations.**
10. **Empty states are one serif-italic sentence only** — no illustrations, no "You don't have any...".

---

## 2. Component impact

### Classification table

| Component | Verdict | Reuse target (if any) | Churn estimate | Notes |
|---|---|---|---|---|
| **Shared primitives** | | | | |
| `StatusBadge` | Adapt | `frontend/src/components/ingestion/StatusBadge.tsx:1` | S | Same six statuses, same glyphs, same color map. Adapt only if token names change. |
| `ChannelGlyph` | New | — | S | 16px square, 1px border, mono letter; neutral fg + border. |
| `BMark` (butler mark) | Reuse | `frontend/src/components/ui/ButlerMark.tsx:1` | S | Existing supports name/size/tone; alias only. |
| `Eyebrow` | Reuse | utility class | S | Mono 10px uppercase 0.14em; already defined. |
| `Mono` | Reuse | — | S | Inline mono span; tabular-nums. |
| `PillBtn` | Reuse | `frontend/src/components/ui/button.tsx:1` | S | shadcn `<Button variant="outline" size="sm">` + className. |
| `ReplayIcon` | New | — | S | Inline 14×14 SVG, stroke 1.3, no fill. |
| `FlameStrip` & `FlameAxis` | Adapt | `TimelineTab.tsx:109–140` | M | Refactor inline flame to SVG bars with opacity ramps for sub-steps. |
| **Timeline (V1_Ledger)** | | | | |
| `V1_Ledger` | Replace | `frontend/src/components/ingestion/TimelineTab.tsx:1` | L | Hour-grouped layout + drawer-on-click; preserve filter state structure. |
| `RangePicker` | New | — | M | Button-group + popover for custom range. |
| `SearchInput` | Reuse | `frontend/src/components/ui/input.tsx:1` | S | Standard HTML input. |
| `SavedViews` | Adapt | inferred from `TimelineTab` state | S | Add dropdown pill (errors/priority/spend). |
| `LiveStatusPill` | Reuse | `StatusBadge.tsx:1` | S | **Pin as `setInterval`-only — no LLM call** (Phase D guardrail). |
| `ChannelChip` | Adapt | inferred from connector filter | S | Tag-like pill: ChannelGlyph + label. |
| `StatusFilter` | Adapt | — | S | Multi-select checkbox set; state at `TimelineTab.tsx:16–19`. |
| `HourBlock` | New | — | S | Eyebrow row with mono hour label. |
| `SortedByCost` | Adapt | — | S | Sort indicator in toolbar. |
| `LedgerRow` | Replace | current TableRow components | M | Grid `24px 1fr 80px 100px auto` — checkbox + glyph/sender + chip + status + flame + cost. |
| `ExpandedDrawer` | Replace | (none today) | M | shadcn `<Sheet side="right">` recommended; tabs: Flame / Raw / Replay. |
| `DrawerFlame` | New | — | M | Vertical bars per butler, labeled left, time axis. |
| `SessionStepBlock` | Adapt | `TimelineTab.tsx:180–220` (SessionFlamegraph) | M | Step table instead of flame; reuse session-lineage logic. |
| `CopyableId` | Reuse | button + clipboard util | S | truncateId() + Button(ghost, copy-icon). |
| `DrawerRaw` | New | — | S | `<pre>` + monospace, scroll container. |
| `DrawerReplay` | Adapt | `TimelineTab.tsx:280–320` | S | Extend single replay button to history list. |
| `SessionIndex` | New | — | S | Tab/anchor list inside drawer. |
| `BulkActionBar` | New | — | M | Sticky bar on row select; Replay All / Copy IDs / Clear. |
| `RollupBand` | Adapt | `TimelineTab.tsx:260–280` (rollup card) | S | Re-style summary row. |
| **Connectors Roster** | | | | |
| `ConnectorsRoster` | Adapt | `frontend/src/components/ingestion/ConnectorsTab.tsx:1` | M | Replace card grid with hairline rows; keep summary + dormant. |
| `ConnectorRow` | New | — | M | Grid: health dot + glyph + label + description + 24h sparkline + sessions + cost + auth pill + menu. |
| `AttentionStrip` | Reuse | issue rows in `ConnectorsTab` | S | Rules-separated list. |
| `DormantList` | Adapt | (none today) | S | Serif-italic rows for unconfigured channels. |
| `Sparkline` | Reuse | `Spark` primitive or recharts | S | 80×18 minimal SVG; no axes. |
| **Connector Detail** | | | | |
| `ConnectorDetailSpotify` | Replace | `frontend/src/pages/ConnectorDetailPage.tsx:1` | L | Refactor to type-aware layout: Spotify-rich + generic stub for others. |
| `ReauthCallout` | New | — | S | Red alert banner: glyph + label + action link. |
| `ScopeList` | New | — | S | OAuth scopes ul/li with status dots. |
| `ConnectorHistogram` | Adapt | `VolumeTrendChart` | S | 24h bar chart; reuse recharts. |
| `IncidentsList` | New | — | S | Reverse-chron error log. |
| `RecentEventsList` | Adapt | filtered by connector | S | Latest N events from events API. |
| `ScheduleBlock` | Adapt | (none today) | M | Cron/schedule KV + edit controls. |
| `RoutingRulesList` | Reuse | `frontend/src/components/ingestion/ConnectorRulesSection.tsx:1` | S | Connector-scoped rules table exists. |
| `ConfigBlock` | Adapt | discretion + batch settings cards | S | Style adjustment only. |
| **Filters** | | | | |
| `IngestionFilters` | Adapt | `frontend/src/components/switchboard/FiltersTab.tsx:1` | M | Replace rules table with gate sections; keep rule editor. |
| `PipelineDiagram` | New | — | M | 5-column funnel; recharts or inline SVG. |
| `GateSection` | New | — | S | Gate header + rule rows. |
| `RuleRow` | Adapt | current table rows in `FiltersTab` | S | Mono priority + condition + action + toggle + menu. |
| `Toggle` | Reuse | `frontend/src/components/ui/switch.tsx:1` | S | shadcn switch. |
| `PrioritySendersBlock` | New | — | S | Priority contacts table. |
| `ChannelDefaultsBlock` | New | — | S | Per-channel default policy. |
| `ArchivedRules` | Adapt | current collapsed section | S | Disabled rules list. |
| `AddRuleFooter` | Adapt | current "Add Rule" button | S | Reuse editor. |
| `PageHeader` | Reuse | `frontend/src/components/ui/page.tsx:1` (as `<Page>`) | S | Already used in `ConnectorDetailPage`. |
| `TabsRow` | Reuse | `frontend/src/components/ui/tabs.tsx:1` | S | shadcn Tabs. |

### Stack delta

1. **Google Fonts addition to `frontend/index.html`** (S) — add `<link>` tags for Inter Tight, Source Serif 4, JetBrains Mono per `DESIGN_LANGUAGE.md` §6.
2. **CSS design tokens in `frontend/src/index.css`** (S) — add `:root` + `.dark` variables for font families, eyebrow/display sizes, tracking/leading, rule color, gutter, readable/headline column widths, `.tnum` class.
3. **Route restructuring** (L) — convert `/ingestion?tab=...` to nested routes (`/ingestion/timeline`, `/ingestion/connectors`, `/ingestion/filters`, optional `/ingestion/history`). Requires `<Outlet/>` in `IngestionPage.tsx`, route defs in `router.tsx`, and redirect rules for old `?tab=` bookmarks. **Decision needed:** keep nested routes (recommended per HANDOFF) vs. retain query-param approach.
4. **Recharts compatibility check** (S) — verify v3.7.0 supports flamegraph + histogram + funnel renderings.
5. **No new state library** (S) — TanStack Query already present.
6. **No new UI library** (S) — shadcn already in stack.
7. **Optional: responsive breakpoint audit** (S) — verify SM/MD/LG behavior for connector roster row reflow.

No blockers flagged — all stack deltas are additive or local to `/ingestion`.

---

## 3. Backend contract delta

### Affordance inventory

| Affordance | Sub-page(s) | Data needed (fields) | Source of fixture (if any) |
|---|---|---|---|
| ChannelGlyph | Timeline, Connectors, Filters | channel id, label, glyph | `ingestion-data.jsx` `CONNECTORS` |
| RangePicker | Timeline | range options (id, label) | `ingestion-v1.jsx` hardcoded |
| StatusFilter | Timeline | status codes (ingested, filtered, error, etc.) | `ingestion-v1.jsx` hardcoded |
| SavedViews | Timeline | view presets (all, errors, priority, spend) | `ingestion-v1.jsx` hardcoded |
| ChannelChip | Timeline | channel id, event count, error count, cost, active state | computed from events |
| LedgerRow | Timeline | event id, ts, channel, sender, summary, tokens, cost, status, tier, butlers[] | `ingestion-data.jsx` `EVENTS` |
| HourBlock | Timeline | hour ts, grouped events | computed from events |
| FlameStrip | Timeline, Drawer | butler sessions with steps (name, durMs, status, tokensIn, tokensOut, cost) | `ingestion-data.jsx` `EVENTS[*].butlers[*].steps` |
| DrawerFlame | Drawer | per-session step ledger w/ token distribution + cost | derived from steps |
| DrawerRaw | Drawer | raw inbound payload (JSON), size in bytes | `mockPayload()` |
| SessionIndex | Drawer | session id, butler name, duration, cost, status | `ingestion-data.jsx` `EVENTS[*].butlers` |
| DrawerReplay | Drawer | replay attempt history (ts, initiator, result, detail, cost), policy, retry logic | `mockReplayHistory()` |
| BulkActionBar | Timeline | selected event ids count, replay-all action, copy ids, clear | selection state |
| RollupBand | Timeline footer | totals: count, accepted, filtered, failed, sessions, tokens, cost | computed from visible events |
| ConnectorRow | Connectors Roster | id, label, kind, description, health, lastEventAt, events24h, rate1h, sessions24h, cost24h, spark24h (24 ints), filtered24h, routedPct, auth status, enabled | `ingestion-connectors-data.jsx` `CONNECTOR_DETAILS` |
| ConnectorHistogram | Connector Detail | 24h hourly bars (or 7d/30d), max value for scaling | `spark24h` fixture |
| RecentEventsList | Connector Detail | events for this connector (ts, summary, session counts, cost, status) | events filtered by channel |
| IncidentsList | Connector Detail | incidents[] (ts, kind, text) | `incidents` fixture |
| ReauthCallout | Connector Detail | auth status, expiry note, issue link, re-authorize button | `auth` fixture |
| ScopeList | Connector Detail | scopes[] (permission strings) | `scopes` fixture |
| ScheduleBlock | Connector Detail | cadence, endpoint, latency, config fields | `config` fixture |
| ConfigBlock | Connector Detail | endpoint, cadence, latency, enabled flag | `config` fixture |
| AttentionStrip | Connectors Roster header | issues list (auth/expired/degraded) | computed from connectors w/ status != ok |
| DormantList | Connectors Roster footer | disabled/unconfigured connectors (label, description, auth) | dormant fixture entries |
| Sparkline | Connectors Roster | 24h hourly event counts, normalized | `spark24h` fixture |
| PipelineDiagram | Filters | 5 stages (accept, dedupe, tier, route, execute) with in/out counts, drops, preserved | `PIPELINE_STATS` fixture |
| GateSection | Filters | stage key, label, gloss, in count, out count, drop breakdown, rules | `PIPELINE_STATS.stages` |
| RuleRow | Filters Gates | rule id, name, note, when (DSL), action, matches24h, enabled, owner, examples | `INGESTION_RULES` fixture |
| PrioritySendersBlock | Filters | priority contacts (name, handle, channel, butler, added, lastSeen) | `PRIORITY_CONTACTS` fixture |
| ChannelDefaultsBlock | Filters | per-channel defaults (channel, policy, note) | `CHANNEL_DEFAULTS` fixture |
| ArchivedRules | Filters | disabled rules w/ restore action | `INGESTION_RULES` filtered |
| AddRuleFooter | Filters | DSL description, add rule button, open DSL editor | hardcoded |

### API delta

> **Every row carries an `Evidence` column.** Fixture-only rows must be `status: unclear` and resolved before spec phase.

| Path | Method | Status | Existing handler (if any) | Request shape | Response shape | Evidence | Drives affordance(s) |
|---|---|---|---|---|---|---|---|
| `/api/ingestion/events` | GET | exists | `src/butlers/api/routers/ingestion_events.py:60` | `limit`, `offset`, `source_channel?`, `status?` | `PaginatedResponse[IngestionEventSummary]` | live-endpoint `ingestion_events.py:60–108` | Timeline ledger, HourBlock, LedgerRow, BulkActionBar |
| `/api/ingestion/events/{request_id}` | GET | exists | `ingestion_events.py:116` | path `request_id`, `include?` (list) | `ApiResponse[IngestionEventDetail]` (PII-gated decomposition) | live-endpoint `ingestion_events.py:116–198` | DrawerFlame, DrawerRaw, DrawerReplay metadata |
| `/api/ingestion/events/{request_id}/sessions` | GET | exists | `ingestion_events.py:206` | path `request_id` | `ApiResponse[list[IngestionEventSession]]` | live-endpoint `ingestion_events.py:206–219` | SessionIndex, SessionStepBlock |
| `/api/ingestion/events/{request_id}/rollup` | GET | exists | `ingestion_events.py:227` | path `request_id` | `ApiResponse[IngestionEventRollup]` | live-endpoint `ingestion_events.py:227–241` | RollupBand footer totals |
| `/api/ingestion/events/{event_id}/replay` | POST | exists | `ingestion_events.py:249` | path `event_id` | `{ status, id }` or 404/409 | live-endpoint `ingestion_events.py:249–296` | DrawerReplay, BulkActionBar |
| `/api/ingestion/events/replay-bulk` | POST | new | — | `{ event_ids: list[str] }` | `{ queued, failed, details[] }` | fixture (inferred from `BulkActionBar` in `ingestion-v1.jsx:193`) → **unclear** | BulkActionBar replay-all |
| `/api/ingestion/connectors` | GET | new (unclear) | — | `limit?`, `offset?`, `include_stats?`, `period? (24h|7d|30d)` | `PaginatedResponse[ConnectorSummary]` w/ health, auth, events24h, sessions24h, cost24h, spark24h[24], incidents, config | fixture (`CONNECTOR_DETAILS`) → **unclear** | ConnectorRow, AttentionStrip, DormantList, Sparkline |
| `/api/ingestion/connectors/{connectorType}/{endpointIdentity}` | GET | new (unclear) | — | path, `include? (scopes, incidents, config, recent_events)` | `ApiResponse[ConnectorDetailFull]` | fixture (`CONNECTOR_DETAILS` per-connector) → **unclear** | Detail header, ReauthCallout, ScopeList, ScheduleBlock, RecentEventsList, IncidentsList |
| `/api/ingestion/connectors/{connectorType}/{endpointIdentity}` | PATCH | new (unclear) | — | path + body `{ cadence?, enabled?, scopes? }` | `ApiResponse[ConnectorSummary]` | fixture → **unclear** | ScheduleBlock, ConfigBlock |
| `/api/ingestion/connectors/{connectorType}/{endpointIdentity}/reauth` | POST | new (unclear) | — | path | redirect or `{ auth_url }` | fixture (`ReauthCallout` in `ingestion-connector-detail.jsx:98`) → **unclear** | ReauthCallout button |
| `/api/ingestion/rules` | GET | new (unclear) | — | `enabled?`, `gate?` | `ApiResponse[list[IngestionRule]]` | fixture (`INGESTION_RULES`) → **unclear** | RuleRow, GateSection, ArchivedRules |
| `/api/ingestion/rules` | POST | new (unclear) | — | `{ name, note, when, action, gate }` | `ApiResponse[IngestionRule]` | fixture → **unclear** | AddRuleFooter |
| `/api/ingestion/rules/{rule_id}` | PATCH | new (unclear) | — | path + body | `ApiResponse[IngestionRule]` | fixture → **unclear** | RuleRow enable/disable/edit |
| `/api/ingestion/rules/{rule_id}` | DELETE | new (unclear) | — | path | `{ deleted: true }` or 404 | fixture → **unclear** | RuleRow archive |
| `/api/ingestion/pipeline-stats` | GET | new (unclear) | — | `period? (24h|7d)` | `ApiResponse[PipelineStats]` w/ stages[] (key, label, gloss, in, out, drops[], preserved) | fixture (`PIPELINE_STATS`) → **unclear** | PipelineDiagram, GateSection |
| `/api/ingestion/priority-contacts` | GET | new (unclear) | — | — | `ApiResponse[list[PriorityContact]]` | fixture (`PRIORITY_CONTACTS`) → **unclear** | PrioritySendersBlock |
| `/api/ingestion/priority-contacts` | POST | new (unclear) | — | `{ name, handle, channel, butler }` | `ApiResponse[PriorityContact]` | fixture → **unclear** | Add priority contact |
| `/api/ingestion/priority-contacts/{contact_id}` | PATCH | new (unclear) | — | path + body | `ApiResponse[PriorityContact]` | fixture → **unclear** | Edit priority contact |
| `/api/ingestion/channel-defaults` | GET | new (unclear) | — | — | `ApiResponse[list[ChannelDefault]]` | fixture (`CHANNEL_DEFAULTS`) → **unclear** | ChannelDefaultsBlock |
| `/api/ingestion/events/stream` | GET | unclear | — | `range? (24h|7d|live)`, `source_channel?` | SSE stream of event summaries | fixture (inferred from `LiveStatusPill` cadence in `ingestion-v1.jsx:374–375`) → **unclear** | Timeline live-mode |

**Note:** every fixture-only row is marked `unclear`. `/project-direction` Phase 1/2 must resolve each — confirm shapes, request examples from a live ingest, or downgrade affordances if a contract proves impractical.

### Schema migration impact

**General butler schema (`general.*`)** — new tables:

1. **`general.ingestion_rules`** — `id PK, name, note, when (DSL), action, matches24h, enabled, owner, examples (JSON), gate (enum), created_at, updated_at`. Index `(gate, enabled)`, `(enabled)`.
2. **`general.priority_contacts`** — `id PK, name, handle, channel, assigned_butler, added_at, last_seen_at`. Index `(channel)`, `(assigned_butler)`.
3. **`general.channel_defaults`** — `channel PK, policy, note, updated_at`. No index needed.
4. **`general.pipeline_stats_24h`** (optional cache) — `stage_key, in_count, out_count, drop_breakdown (JSON), sampled_at`. Pre-computed by cron every 5–10 min.

**Switchboard butler schema (`switchboard.*`)**:

5. **`switchboard.ingestion_events`** — extend with `tier (enum: priority|default)`, `rule_matched`, `filter_reason`, `preserved_without_dispatch (bool)`. Index `(tier, received_at)`.
6. **`switchboard.connector_incidents`** (new) — `id PK, connector_type, endpoint_identity, incident_at, kind, text, resolved_at`. Index `(connector_type, endpoint_identity, incident_at DESC)`.

**Cross-butler concerns:** ingestion rules, priority contacts, and channel defaults are **system-wide** (general butler only). Incident logs are cross-butler-visible (switchboard schema). **No direct DB reads between butlers** — inter-butler queries go through Switchboard MCP. The `route` rule action records the target butler name as a string; Switchboard validates and fans out.

**Pre-warming:** rule matching reads rules from in-memory cache (refreshed on CRUD), not per-event DB query. Pipeline stats served from cache table, not live aggregation.

### Proposed backend epic

**Epic title:** *Ingestion redesign backend: API surface, rules engine, and connector telemetry*

**Child beads** (dependency order):

1. **Bead 1 — Extend `GET /api/ingestion/events` with tier filtering** (M). Add `tier` column to `switchboard.ingestion_events`; add `(tier, received_at)` index; extend list endpoint. *Blockers: none.*
2. **Bead 2 — Implement connector roster API** (M). New router `src/butlers/api/routers/connectors.py`; join connector registry with heartbeat stats + incident log. *Blockers: heartbeat schema must exist.*
3. **Bead 3 — Create `general.ingestion_rules` table + GET endpoint** (S). Alembic migration; seed from fixtures. *Blockers: none.*
4. **Bead 4 — Ingestion rules CRUD (POST/PATCH/DELETE)** (M). DSL validation; audit log on mutations. *Blockers: Bead 3; DSL parser availability.*
5. **Bead 5 — `POST /api/ingestion/events/replay-bulk`** (M). Reuse existing replay utility in loop. *Blockers: Bead 1 (UI needs tier filter to drive selection).*
6. **Bead 6 — `GET /api/ingestion/events/stream` (SSE live feed)** (L). Pub/sub bus subscription. *Blockers: event-bus infra must exist.*
7. **Bead 7 — `general.priority_contacts` + `general.channel_defaults` tables + CRUD** (S). Two migrations; simple CRUD. *Blockers: none (parallel w/ Bead 3).*
8. **Bead 8 — Connector reauth flow (`POST .../reauth`)** (M). Coordinate with existing connector auth modules (Spotify router exists). *Blockers: Bead 2.*
9. **Bead 9 — `general.pipeline_stats_24h` cache + scheduler + read endpoint** (M). Migration; cron task every 5 min; read from cache. *Blockers: none (parallel w/ Bead 3).*
10. **Bead 10 — `switchboard.connector_incidents` table + logging hooks** (M). Update connector implementations to log incidents; expose via Bead 2's detail endpoint. *Blockers: none; improves Bead 2 + Bead 8.*

**Critical path:** Bead 3 → Bead 4 → (Beads 2, 1) → Bead 5. **Parallel tracks:** Beads 6, 7, 9, 10.

---

## 4. Guardrails

### LLM-cost feasibility

**Pricing freshness:** `references/llm-pricing.md` `last_verified: 2026-01` is ~4 months stale at brief authorship date. Live Anthropic pricing fetched 2026-05-17 during Phase D. **Drift detected:** Opus 4.5/4.6/4.7 now $5 in / $25 out (down from $15 / $75 for Opus 4 / 4.1). Sonnet 4.5/4.6 and Haiku 4.5 unchanged. All cost arithmetic uses live rates. Recommend opening a maintenance bead to refresh the pricing reference.

| Feature | Trigger model | tokens_in | tokens_out | Model class | $/call | Freq/user/day | $/user/day (v1, users=1) | $/user/day (sens., users=100) | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| `LiveStatusPill` (`ingestion-v1.jsx:360–389`) — cycles `composing…` / `fresh · 4s` every ~18s | **No LLM** — pure `setInterval`, hardcoded labels, no fetch | — | — | — | $0 | n/a | $0 | $0 | **green (no LLM)** |
| `LedgerRow.summary` (`ingestion-data.jsx:44–230`) — sender + summary text | Fixture-stored, sourced from inbound payload | — | — | — | $0 | n/a | $0 | $0 | **green (no LLM)** |
| `DrawerRaw` — pretty-printed JSON of inbound payload | Static render of stored payload | — | — | — | $0 | n/a | $0 | $0 | **green (no LLM)** |
| `DrawerReplay` — replay attempt history | DB read | — | — | — | $0 | n/a | $0 | $0 | **green (no LLM)** |
| `SessionStepBlock` — per-butler step tokens/cost | Derived from existing session traces | — | — | — | $0 | n/a | $0 | $0 | **green (no LLM)** |
| `PipelineDiagram` / 5-gate funnel counts | Cached SQL aggregates (Bead 9) | — | — | — | $0 | n/a | $0 | $0 | **green (no LLM)** |
| `IngestionRule` rendering (`when`/`action` DSL) | Stored text in `ingestion_rules` table | — | — | — | $0 | n/a | $0 | $0 | **green (no LLM)** |
| `PriorityContacts` / `ChannelDefaults` CRUD | DB writes | — | — | — | $0 | n/a | $0 | $0 | **green (no LLM)** |
| Timeline search (`?q=...`) | DB-indexed query (PII review open question) | — | — | — | $0 | n/a | $0 | $0 | **green (no LLM)** |
| OAuth `reauth`, `replay-bulk`, `run-now`, `rotate-token` | Connector lifecycle | — | — | — | $0 | n/a | $0 | $0 | **green (no LLM)** |

#### Red verdicts

None.

#### Recommended de-scopes before spec phase

None on cost grounds. Two **defensive guardrails** for `/project-direction` to lock in (pre-empt future LLM creep):

1. **`LiveStatusPill` spec must pin "no fetch, no LLM call"** — the eyebrow-pill pattern is a magnet for "let's make it real" creep. Spec it explicitly as `setInterval`-driven UI state derived from the last successful SSE `event: append` timestamp.
2. **`GET /api/ingestion/pipeline-stats` spec must be numeric-only** — response is `PipelineStats` numeric data; per-gate prose is the static `gloss` strings already declared in `ingestion-connectors-data.jsx` fixtures. No LLM-generated funnel commentary.

### Manifesto / identity preservation

| Butler | Manifesto file:line cited | What the redesign does that touches identity | Verdict | Specific drift (if any) |
|---|---|---|---|---|
| **Switchboard** | `roster/switchboard/MANIFESTO.md:9–23` (Purpose + Responsibilities); `:17` ("classifies and routes"), `:20` ("Durable buffer"), `:22` ("Connector registry") | New `/ingestion/filters` exposes the 5-gate funnel + rules CRUD; `/ingestion/connectors` exposes connector registry; Timeline exposes ingestion-event lifecycle; Phase C adds `pipeline-stats`, `rules` CRUD, `priority-contacts`, `channel-defaults` endpoints — all in Switchboard's existing domain. | **identity preserved** | None. Manifesto lists "Message classification" `:18`, "Routing execution" `:19`, "Durable buffer" `:20`, "Agent registry" `:21`, "Connector registry" `:22`. Making them visible is observability, not identity drift. The dashboard doctrine (`about/heart-and-soul/design-language.md:25–35`) explicitly requires owner visibility into butler liveness. |
| **Relationship** | `roster/relationship/MANIFESTO.md` (no declared hue) | Letter-mark hue `--category-1` (`primitives.jsx:58`) | **identity preserved** | None. |
| **Memory** | Manifesto path does **not exist** (`roster/memory/MANIFESTO.md` absent) | Letter-mark hue `--category-2` (`primitives.jsx:62`) | **n/a — manifesto missing** | Memory likely subsumed by Relationship (entity-graph + `memory_*` tools live there). Recommend dropping `memory` from BUTLER_HUE, or aliasing to Relationship. |
| **Calendar** | Manifesto path does **not exist** (`roster/calendar/MANIFESTO.md` absent) | Letter-mark hue `--category-3` (`primitives.jsx:60`) | **n/a — manifesto missing** | No `roster/calendar/`. Calendar lives as shared tool surface inside other butlers. Recommend removing `calendar` from BUTLER_HUE. |
| **Health** | `roster/health/MANIFESTO.md` (no hue declared; voice = "patient and non-judgmental") | Letter-mark hue `--category-4` (`primitives.jsx:59`) | **identity preserved** | None. Letter-mark hue does not touch voice; redesign generates no Health-attributed prose. |
| **Household / Home** | Manifesto at `roster/home/MANIFESTO.md` (not `household/`) | Letter-mark hue `--category-5` (`primitives.jsx:65` under key `household`) | **identity preserved** (naming nit) | Fixture key `household` does not match roster dir `home`. Manifesto declares no hue. Recommend renaming `household → home` in BUTLER_HUE. |
| **Education** | `roster/education/MANIFESTO.md` (no hue declared) | Letter-mark hue `--category-6` (`primitives.jsx:63`) | **identity preserved** | None. |
| **QA** | `roster/qa/MANIFESTO.md:54` ("QA Staffer does **not** respond to user messages"), `:17–21` (operator-facing dossier at `/qa`) | Letter-mark hue `--category-7` (`primitives.jsx:61`); connector-detail reauth callout deep-links into `/qa/investigations/:id` (HANDOFF §3d) | **identity preserved** | None. QA-deep-link from connector incident matches "operator-facing surface" model. |
| **Chronicler** | `roster/chronicler/MANIFESTO.md:34` ("I do not plan / schedule / nudge / notify"), `:38` ("I do not claim the operational `/api/timeline` route... I live at `/api/chronicler/*`"), `:40` ("I do not invoke an LLM per event.") | Letter-mark hue `--category-8` (`primitives.jsx:64`); appears in session-step blocks as fan-out participant | **identity preserved** | None. Crucial: Timeline lives at `/api/ingestion/events` — **not** `/api/timeline` and **not** `/api/chronicler/*`. The `:38` boundary is respected. Drawer treats Chronicler only as participant, never narrative originator. |

#### Drift write-ups

No identity drift on any existing butler manifesto.

The only soft signal flagged was a prompt-time "Switchboard = invisible plumbing" framing — this framing is **not** anchored in `roster/switchboard/MANIFESTO.md`. The manifesto's identity is **infrastructure-critical** (uptime, correctness), not **invisible**. The dashboard doctrine (`about/heart-and-soul/design-language.md:25–35`) requires the owner to "trust that butlers are alive and behaving" and "investigate when one of them isn't" — that mandate **requires** exposing Switchboard's pipeline visually. The redesign therefore strengthens, not violates, the doctrine.

#### Recommended manifesto updates

None required. Two **fixture/code corrections** during the port (not manifesto updates):

1. **`BUTLER_HUE` map** (`primitives.jsx:55–65` → target `frontend/src/lib/butler-hue.ts`): drop `memory` and `calendar` keys (no roster manifestos exist); rename `household → home` to match `roster/home/`. Keep all other category hues as-is.
2. **Optional polish** on `roster/switchboard/MANIFESTO.md`: add a one-line "Operator visibility" clarifier ("Switchboard's pipeline state is exposed to the owner via the `/ingestion` dashboard surface; this is observability, not control."). Non-blocking.

### Intent compliance

No red verdicts. No drift verdicts. Intent compliance is trivially satisfied: the SOFT gate from Section 0 (no LLM-driven affordances, no card chrome / gradients / glassmorphism / scale-on-hover, butler hues on letter-marks only) is respected by the bundle's own architecture. The bundle's `DESIGN_LANGUAGE.md` "Hard do not" list is internally consistent with Section 0's soft-gate substitutions.

---

## 5. Open questions

Consolidated from Phases A, B, C, D. These are inputs for `/project-direction` Phase 1 (doctrine) and Phase 2 (spec) to resolve.

1. **Route structure decision** (Phase B blocker candidate) — accept nested-route refactor (recommended; L effort; breaks `?tab=` bookmarks unless redirects added) or adapt redesign to query-param routes (medium effort; component rewiring). HANDOFF specifies nested.
2. **`/ingestion/connectors` route in live router** (Phase A Q1) — currently absent from `frontend/src/router.tsx`; redesign assumes it exists as a nested route.
3. **Connector detail generalization** (Phase A Q2) — only Spotify fully designed; other channels need a generic template or fall-back stub. Decision needed: build generic + Spotify reference, or per-connector layouts?
4. **Per-step token/cost provenance** (Phase A Q3, HANDOFF §8) — natively tracked per step by butler trace, or derived proportionally server-side from step duration? Affects data model.
5. **Search semantics** (Phase A Q4, HANDOFF §8) — metadata-only (sender, summary, channel, kind, id, session/butler/model names) or full-text inbound-payload search (PII review required)?
6. **SSE live-mode stream** (Phase A Q5, Phase C unclear) — `GET /api/ingestion/events/stream` does not exist today. Confirm: build (Bead 6) or fall back to polling for v1?
7. **Replay idempotency boundary** (Phase A Q6, HANDOFF §8) — email "send drafted reply" should not replay the send itself. Define re-emit vs. resume boundary per channel.
8. **Funnel range scope** (Phase A Q7, HANDOFF §8) — `PipelineDiagram` fixed at 24h (prototype) or range-scoped (matches Timeline picker)?
9. **DSL editor location** (Phase A Q8, HANDOFF §8) — `+ add rule` and `open DSL` footer pills imply a separate editor UI. Where does `/ingestion/filters/new` live?
10. **Raw payload audit-log gating** (Phase A Q9, HANDOFF §6:673) — drawer Raw tab exposes inbound JSON (potentially PII). Pattern exists at `ingestion_events.py:163` (`decomposition_disclosed`). Adopt for drawer Raw tab.
11. **Breadcrumb deep-link state** (Phase A Q10) — connector detail breadcrumb to roster — does it preserve filtered state on return, or land on roster root?
12. **Drawer modal vs. sheet** (Phase B Risk #2) — recommended `<Sheet side="right">` for consistency with existing rule-editor pattern; confirm.
13. **Saved views persistence** (Phase B Risk #10) — recommended client-side only (sessionStorage). Confirm no server persistence required.
14. **Bulk replay endpoint shape** (Phase C unclear) — confirm `{ event_ids[] } → { queued, failed, details[] }` envelope.
15. **Connector roster + detail endpoints shape** (Phase C unclear; fixture-only) — `ConnectorSummary` and `ConnectorDetailFull` models exist (`src/butlers/api/models/connector.py`) but no router serves them. Confirm wire shape against models.
16. **Rules CRUD endpoints shape** (Phase C unclear) — no ingestion-pipeline-rules router exists. Confirm wire shape, DSL syntax validation strategy.
17. **Pipeline stats endpoint shape** (Phase C unclear; fixture-only) — confirm 5-stage funnel `{stages[]: {key, label, gloss, in, out, drops[], preserved}}` shape; staticness of per-gate `gloss` strings.
18. **Priority contacts + channel defaults endpoint shapes** (Phase C unclear; fixture-only) — confirm wire shapes.
19. **Heartbeat schema for connectors** (Phase C Bead 2 prerequisite) — does a heartbeat or connector_instances state table exist for the live-health rollup? If not, blocked work.
20. **DSL parser availability** (Phase C Bead 4 prerequisite) — does the project have a DSL parser for rule `when`/`action` strings, or is one out-of-scope?
21. **Event-bus infrastructure for SSE** (Phase C Bead 6 prerequisite) — does a pub/sub bus (Redis or in-memory) exist that a SSE handler can subscribe to?
22. **Pricing-reference staleness** (Phase D housekeeping) — `references/llm-pricing.md` `last_verified` is 4 months old; Opus 4.x rates dropped. Open a maintenance bead to refresh.
23. **BUTLER_HUE map corrections** (Phase D fixture nit) — drop `memory` + `calendar` keys; rename `household → home` during the port to match `roster/`.

---

## 6. Handoff to `/project-direction`

This brief is the input to a `/project-direction` run with **feature evaluation focus** scoped to `ingestion`.

Concrete invocation:

```
/project-direction --focus=feature \
  --brief=docs/redesigns/2026-05-17-ingestion-brief.md \
  --bundle=pr/overview/ingestion-redesign/ \
  --binding-design-language=pr/overview/ingestion-redesign/DESIGN_LANGUAGE.md \
  --binding-design-intent=docs/redesigns/2026-05-17-ingestion-brief.md#0-design-intent \
  --red-flag-policy=descope-or-escalate
```

Carry-forward instructions:

- `DESIGN_LANGUAGE.md` is **binding**. Every spec section must preserve it.
- Section 0 of this brief is **binding**. Spec drift away from intent fails reconciliation.
- All `red`-verdict LLM features must be de-scoped or escalated before being specced. (None in this brief.)
- All `identity drift flagged` items must be resolved (redesign tweak or manifesto update) before being specced. (None in this brief.)
- All `unclear` evidence rows in Section 3 API delta must be resolved by Phase 2 — confirm wire shapes against either a live endpoint (preferred) or a user-acknowledged fixture.
- After `/project-direction` Phase 3 produces the beads graph, Phase G of `butlers-redesign-prompt` will split out the backend epic per Section 3.
