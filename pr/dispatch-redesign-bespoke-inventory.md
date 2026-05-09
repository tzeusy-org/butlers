# Dispatch Redesign: Bespoke Tab Inventory

Inventory of bespoke-tab opportunities across all 12 real butlers.
Each section records a tab decision, justification, file:line citations, and an indicative panel sketch.

Workers append to this file in delimited sections.
Batch 1 (bu-dg5qc.1): chronicler, finance, general.
Batch 2 (bu-dg5qc.2): TBD.
Batch 3 (bu-dg5qc.3): TBD.

---

<!-- BEGIN bu-dg5qc.1 -->

## chronicler

**Tab decision:** `Timelines` (bespoke tab warranted)

**Justification:**
The chronicler owns a unique retrospective timeline data model — episodes and point events
reconstructed from multiple sources — that has no equivalent in the base resident tab set.
The existing mockup in `pr/overview/butler-detail-bespoke.jsx:578` demonstrates a
`TimelinesBespoke` component already designed for this slot, and the API backend supplies
all necessary data: a dedicated KPI endpoint, day-level aggregate buckets, episode lists,
and a day-close prose cache. A `Timelines` tab surfaces lived past time as a day-by-day
Gantt-style view without duplicating any base-tab content (Activity, Sessions, etc.).

**Supporting endpoints:**

| Endpoint | File:line | Role in the tab |
|---|---|---|
| `GET /api/chronicler/kpi` | `roster/chronicler/api/router.py:2207` | KPI strip — hours by top lanes, sleep, streaks |
| `GET /api/chronicler/briefing` | `roster/chronicler/api/router.py:2137` | Day-close prose and headline for the date banner |
| `GET /api/chronicler/aggregate/by-category` | `roster/chronicler/api/router.py:1080` | Category donut / bar chart across a chosen window |
| `GET /api/chronicler/aggregate/by-day` | `roster/chronicler/api/router.py:1303` | Spark-bar timeline for the trailing N days |
| `GET /api/chronicler/episodes` | `roster/chronicler/api/router.py:266` | Episode feed driving the Gantt swimlane rows |
| `GET /api/chronicler/aggregate/day-close` | `roster/chronicler/api/router.py:1599` | Day-close cache — fresh prose or stale marker |
| `GET /api/chronicler/source-state` | `roster/chronicler/api/router.py:883` | Source health widget |

**Indicative panel composition (4-col grid):**

- **KPI strip (full-width, 4 cols):** Top-3 lanes by hours, sleep minutes, longest episode,
  sleep + exercise streaks. Backed by `GET /api/chronicler/kpi` → `ChroniclesKpi`.
- **Today · Timeline (3 cols):** Scrollable episode list rendered as a vertical spine with
  time label + category dot + episode title + source annotation. Backed by
  `GET /api/chronicler/episodes` with a day-boundary window. Privacy-aware: sensitive
  bars show hatched fill and masked title per `roster/chronicler/AGENTS.md` privacy contract.
- **Sources · today (1 col):** Per-source row count, last-run timestamp, and freshness state.
  Backed by `GET /api/chronicler/source-state`.
- **Category breakdown · 7d (2 cols):** Bar chart of episode duration by category across
  the trailing 7 days. Backed by `GET /api/chronicler/aggregate/by-category`.
- **Day-close prose (2 cols):** Cached editorial paragraph for the selected day (fresh or
  stale marker). Backed by `GET /api/chronicler/aggregate/day-close`.

---

## finance

**Tab decision:** `Finances` (bespoke tab warranted)

**Justification:**
The finance butler owns a rich, purpose-built financial data model — transactions, subscriptions,
bills, accounts, spending summaries, and upcoming-bill urgency classification — that cannot be
surfaced through any base resident tab (Activity, Logs, Approvals, Spend, Config, Memory).
The `Spend` base tab is cross-butler operational cost, not personal finance data, so a dedicated
`Finances` bespoke tab is the only correct home for this content. There is no prior mockup
precedent; the decision here is grounded entirely in the API surface.

A `Transactions` sub-section within a broader `Finances` tab is the natural primary panel, with
subscriptions and bills as companion panels. A top-level tab named `Finances` (rather than
`Transactions` or `Accounts`) is preferred because the API covers all four financial entity types
and the tab should reflect the butler's full domain, not one entity type.

**Supporting endpoints:**

| Endpoint | File:line | Role in the tab |
|---|---|---|
| `GET /api/finance/spending-summary` | `roster/finance/api/router.py:389` | KPI strip — total spend, top categories |
| `GET /api/finance/upcoming-bills` | `roster/finance/api/router.py:475` | Bills urgency panel (overdue / due_soon / upcoming) |
| `GET /api/finance/transactions` | `roster/finance/api/router.py:77` | Transaction feed — paginated, filterable by category/merchant/account |
| `GET /api/finance/subscriptions` | `roster/finance/api/router.py:185` | Subscription roster, next renewal dates |
| `GET /api/finance/bills` | `roster/finance/api/router.py:251` | Bill obligations, status, due dates |
| `GET /api/finance/accounts` | `roster/finance/api/router.py:329` | Account list for contextual filtering |
| `GET /api/finance/merchants/distinct` | `roster/finance/api/router.py:661` | Distinct merchant list for filter typeahead |

**Indicative panel composition (4-col grid):**

- **KPI strip (full-width, 4 cols):** Current-month total spend, largest single transaction
  (merchant + amount), active subscription count, next due bill with urgency glyph.
  Backed by `GET /api/finance/spending-summary` (current month) and
  `GET /api/finance/upcoming-bills?days_ahead=1`.
- **Recent transactions (3 cols):** Paginated table — date, merchant, category, amount,
  direction indicator. Filterable by category or merchant inline. Backed by
  `GET /api/finance/transactions`.
- **Upcoming bills (1 col):** Compact urgency list — payee, amount, due date, urgency chip
  (overdue / due_today / due_soon / upcoming). Backed by
  `GET /api/finance/upcoming-bills`.
- **Subscriptions (2 cols):** Roster table — service, amount, frequency, next renewal,
  status chip (active / paused / cancelled). Backed by `GET /api/finance/subscriptions`.
- **Spending by category · 30d (2 cols):** Bar chart of spend grouped by category for
  the trailing 30 days. Backed by `GET /api/finance/spending-summary?group_by=category`.

---

## general

**Tab decision:** No bespoke (existing `+Collections` and `+Entities` conditional tabs are sufficient)

**Justification:**
The general butler's API surface consists of exactly four endpoints — collections list,
collection-scoped entity list, cross-collection entity search, and entity detail — all of which
map directly to the two conditional tabs already mandated by the spec. The `spec.md:104-106`
(`openspec/specs/dashboard-butler-management/spec.md`) and confirmed in the tab vocabulary
change (`openspec/changes/redesign-detail-page-tab-vocabulary/design.md:57`) require:

> **WHEN** the butler name is `general` **THEN** two additional tabs are shown:
> "Collections" and "Entities"

These two tabs fully cover the domain:
- **Collections** tab: backed by `GET /api/general/collections` (`roster/general/api/router.py:63`)
  and `GET /api/general/collections/{collection_id}/entities` (`roster/general/api/router.py:114`).
- **Entities** tab: backed by `GET /api/general/entities` (`roster/general/api/router.py:180`)
  and `GET /api/general/entities/{entity_id}` (`roster/general/api/router.py:253`).

There is no domain content in the general butler's API that falls outside these two tabs.
Adding a third bespoke tab would be redundant. The general butler's purpose is intentionally
broad (catch-all freeform storage), so a single-level tab decomposition into Collections and
Entities is both necessary and sufficient.

**Supporting endpoints (confirming the two existing tabs are fully grounded):**

| Endpoint | File:line | Existing tab |
|---|---|---|
| `GET /api/general/collections` | `roster/general/api/router.py:63` | Collections |
| `GET /api/general/collections/{collection_id}/entities` | `roster/general/api/router.py:114` | Collections (drill-down) |
| `GET /api/general/entities` | `roster/general/api/router.py:180` | Entities |
| `GET /api/general/entities/{entity_id}` | `roster/general/api/router.py:253` | Entities (detail) |

**Panel sketch for the existing Collections tab (for completeness):**

- **KPI strip:** Total collection count, total entity count, most recently updated collection.
  Backed by `GET /api/general/collections` (aggregated on client from `entity_count` field).
- **Collections list (3 cols):** Name, description, entity count, created date. Row-click
  expands to entity list for that collection. Backed by `GET /api/general/collections`.
- **Tag cloud / search (1 col):** Cross-collection entity search box with tag filter.
  Backed by `GET /api/general/entities?q=&tag=`.

<!-- END bu-dg5qc.1 -->

<!-- BEGIN bu-dg5qc.2 -->

## health

**Tab decision:** No additional bespoke tab (spec-mandated `+Health` conditional tab is sufficient)

**Justification:**
The health butler already carries a spec-mandated conditional `+Health` tab
(`openspec/changes/archive/2026-02-24-alpha-release-mvp/specs/dashboard-butler-management/spec.md:61-63`).
That tab is defined to show a card grid linking to six health sub-pages: Measurements, Medications,
Conditions, Symptoms, Meals, and Research — which directly maps to the six entity types the API exposes.
The `+Health` conditional tab therefore already IS the bespoke surface for this butler. Adding a second
bespoke tab alongside the spec-mandated one would create an ambiguous duplicate rather than a coherent
augmentation.

All seven API endpoints serve data that belongs under the existing `+Health` tab sub-pages. There is no
domain content that falls outside what the spec-mandated conditional tab already covers. The decision is
therefore: honour the existing spec commitment rather than layer a redundant bespoke tab on top of it.
If the `+Health` tab body needs to be upgraded from navigation cards to data panels (KPI strip, trend
charts, adherence timeline), that is a spec-amendment task against the `+Health` tab itself, not a
new bespoke-tab decision.

**Supporting endpoints (confirming the existing +Health tab fully covers the domain):**

| Endpoint | File:line | Existing +Health sub-page |
|---|---|---|
| `GET /api/health/measurements` | `roster/health/api/router.py:68` | Measurements |
| `GET /api/health/medications` | `roster/health/api/router.py:136` | Medications |
| `GET /api/health/medications/{id}/doses` | `roster/health/api/router.py:195` | Medications (dose log) |
| `GET /api/health/conditions` | `roster/health/api/router.py:246` | Conditions |
| `GET /api/health/symptoms` | `roster/health/api/router.py:290` | Symptoms |
| `GET /api/health/meals` | `roster/health/api/router.py:388` | Meals |
| `GET /api/health/research` | `roster/health/api/router.py:458` | Research |

**Panel sketch for the existing +Health tab (for completeness — as a data-panel upgrade, not a new tab):**

- **KPI strip (full-width, 4 cols):** Latest measurement per type (weight, blood pressure, glucose),
  active medication count, medication adherence rate (doses taken vs. scheduled this week).
  Backed by `GET /api/health/measurements?limit=1&type=<type>` per type and
  `GET /api/health/medications?active=true`.
- **Measurement trends · 30d (3 cols):** Sparkline per tracked metric (weight, BP systolic/diastolic,
  glucose) over the trailing 30 days. Backed by `GET /api/health/measurements?since=<30d ago>`.
- **Active conditions & recent symptoms (1 col):** Compact list of active conditions and the three
  most recent symptoms with severity dots. Backed by `GET /api/health/conditions` and
  `GET /api/health/symptoms?limit=3`.
- **Recent meals · today (2 cols):** Breakfast / lunch / dinner / snack entries for today with
  calorie totals where available. Backed by `GET /api/health/meals?since=<today 00:00>`.
- **Research notes (2 cols):** Most recently saved research entries with tag chips and source link.
  Backed by `GET /api/health/research?limit=5`.

---

## home

**Tab decision:** `Devices` (bespoke tab warranted)

**Justification:**
The home butler owns a rich, purpose-built smart-home data model — device inventory (HA entity
snapshot cache), energy consumption time-series, maintenance items, and command audit log — that
has no equivalent in any base resident tab (Activity, Logs, Approvals, Spend, Config, Memory).
The Logs base tab covers session/MCP logs, not HA command history. The Activity base tab covers
butler session activity, not smart-home device state.

A `Devices` bespoke tab is the correct surface for three reasons:

1. **Device health is uniquely actionable**: The `GET /api/home/devices?health=offline` filter
   surfaces unhealthy devices; the `GET /api/home/maintenance` endpoint surfaces overdue maintenance
   items with computed urgency status. This is operator-actionable data with no base-tab home.
2. **Energy is domain-specific and visual**: `GET /api/home/energy` and `/energy/top-consumers`
   provide time-series data suited to a chart panel. This is the home butler's analytics surface.
3. **Command audit is distinct from session audit**: `GET /api/home/command-log` records HA service
   calls (domain, service, target, result) — a different audit dimension from the Sessions base tab.

The tab name `Devices` (rather than `Home` or `Smart Home`) reflects the primary lens: the user's
primary question when opening this tab is "what are my devices doing?" Device inventory, health
status, energy consumption, and maintenance all answer that question.

**Supporting endpoints:**

| Endpoint | File:line | Role in the tab |
|---|---|---|
| `GET /api/home/devices` | `roster/home/api/router.py:353` | KPI strip + device inventory table (domain/area/health filters) |
| `GET /api/home/snapshot-status` | `roster/home/api/router.py:303` | Freshness widget — total entities, per-domain counts, oldest/newest captured_at |
| `GET /api/home/energy` | `roster/home/api/router.py:470` | Energy time-series chart (day/hour granularity) |
| `GET /api/home/energy/top-consumers` | `roster/home/api/router.py:607` | Top-10 consumers bar chart (entity_id, friendly_name, total_kwh, percentage) |
| `GET /api/home/maintenance` | `roster/home/api/router.py:801` | Maintenance items panel (overdue/due/upcoming/ok status chips) |
| `GET /api/home/command-log` | `roster/home/api/router.py:229` | HA command audit panel (domain, service, target, result, timestamp) |
| `GET /api/home/areas` | `roster/home/api/router.py:196` | Area filter list for device table |

**Indicative panel composition (4-col grid):**

- **KPI strip (full-width, 4 cols):** Total device count, offline device count (with destructive badge
  if > 0), overdue maintenance item count (with amber badge), latest snapshot freshness
  (`newest_captured_at` relative time). Backed by `GET /api/home/snapshot-status` and
  `GET /api/home/devices?health=offline` (count only) and
  `GET /api/home/maintenance?status=overdue` (count only).
- **Device inventory (3 cols):** Paginated table — entity_id, friendly_name, area, domain, state,
  health_status chip. Filterable by domain, area, and health status inline. Row-click opens
  entity detail drawer. Backed by `GET /api/home/devices`.
- **Maintenance queue (1 col):** Compact urgency list — name, category, next_due_at, status chip
  (overdue / due / upcoming / ok). Backed by `GET /api/home/maintenance`.
- **Energy · 7d (2 cols):** Area chart of total kWh per day over the trailing 7 days, with
  top-3 consumer legend. Backed by `GET /api/home/energy?period=day` and
  `GET /api/home/energy/top-consumers`.
- **HA command log (2 cols):** Recent HA service calls — timestamp, domain.service, target, result
  chip (success / error). Backed by `GET /api/home/command-log?limit=20`.

---

## lifestyle

**Tab decision:** No bespoke (no API router; all domain data is memory-module facts)

**Justification:**
The lifestyle butler has no `api/` directory and exposes no dashboard API endpoints. Its entire data
surface — taste preferences, consumption state, Spotify-enriched facts, hobby records — lives
exclusively in the memory module's SPO facts store, not in butler-specific REST endpoints.

The Memory base tab already provides access to the facts store through the shared memory browser.
There is no domain-specific structured data (no transactions, no device inventory, no episode
timeline, no subscription roster) that would require a dedicated bespoke panel beyond what the
Memory tab already offers.

The lifestyle butler's scheduled outputs (weekly taste digest) are delivered via `notify()` and
are therefore visible in the Notifications surface rather than a dashboard tab. The Spotify and
Steam integration tools are agent-side MCP tools, not dashboard-facing endpoints.

Until a dedicated `roster/lifestyle/api/router.py` is added with domain-specific aggregate
endpoints (e.g. a taste-trend summary, top-artists-this-month, current-consumption-state query),
a bespoke tab cannot be backed by any API and would have nothing to render.

**Confirming absence of API surface:**

| What was checked | Finding |
|---|---|
| `roster/lifestyle/api/` directory | Does not exist |
| `roster/lifestyle/butler.toml` modules | `memory`, `calendar`, `contacts`, `spotify`, `steam` — all standard modules, no custom API |
| Domain data storage | Memory module SPO facts only (`memory_store_fact` / `memory_search`) |

**Panel sketch if a lifestyle API were added in future:**

- **KPI strip:** Taste facts logged this week, current listening artist (from `listens_to` volatile
  fact), currently watching (from `watches` volatile fact).
- **Taste timeline · 30d:** Horizontal list of logged preferences grouped by week — restaurants,
  genres, artists, hobbies. Backed by a future `GET /api/lifestyle/facts/recent` endpoint.
- **Genre & cuisine heatmap:** Frequency map of `likes_genre` and `likes_cuisine` facts over time.

---

## messenger

**Tab decision:** No bespoke (infrastructure staffer; no domain content)

**Justification:**
The messenger butler is a `type = "staffer"` infrastructure component, not a domain butler.
It has no `api/` directory and exposes no dashboard API endpoints beyond core infrastructure
routes. Its entire function is delivery execution: accept `notify.v1` payloads from the Switchboard
and emit them to Telegram, Email, and WhatsApp channel APIs.

The messenger has no domain-specific data model, no user-facing content surface, and no
accumulation of domain state that would warrant a bespoke tab. The Logs base tab covers session
history; the Activity base tab covers delivery session activity. The MCP base tab surfaces the
channel egress tools. No bespoke panel would add meaningful information beyond what these base
tabs already provide.

Unlike domain butlers (chronicler, finance, health, home) whose data models are rich and unique,
the messenger's data model is intentionally minimal: delivery outcomes and channel credentials.
Delivery outcomes belong in the Logs/Sessions base tab; credentials belong in the Config/State
base tab. Neither warrants a dedicated bespoke panel.

**Confirming absence of API surface:**

| What was checked | Finding |
|---|---|
| `roster/messenger/api/` directory | Does not exist |
| `roster/messenger/butler.toml` type | `type = "staffer"` — excluded from user-message routing |
| Domain data storage | Session log + state store only; no domain entity tables |
| Butler-specific data model | None — delivery outcomes are operational, not domain content |

<!-- END bu-dg5qc.2 -->
