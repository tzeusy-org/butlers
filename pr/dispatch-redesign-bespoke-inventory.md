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
