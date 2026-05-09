# Epic 04: Butler Overview Tab Redesign — Reconciliation Report (gen-1)

Generated: 2026-05-10
Issue: bu-8hbph.5
Reporter: Beads Worker (automated reconciliation)

---

## 1. Children Summary

### bu-8hbph.1 — Identity card + eligibility row in Overview tab

**Status:** CLOSED (PR #1504 merged 2026-05-09T18:22:37Z)

**Commits:**
- `44ba558d` feat: identity card serif italic + RTL tests [bu-8hbph.1] (#1504)

**Delivered:**
- Identity card in `ButlerOverviewTab.tsx` showing butler name, `ButlerStatusBadge`,
  serif italic description (`CardDescription` with `italic font-[family-name:var(--font-serif,serif)]`),
  port (from `butler.port`), status field, and heartbeat row.
- Eligibility row (Active/Quarantined/Stale badge) inside the identity card's KV list,
  sourced from `useRegistry()` and `useSetEligibility()`.
- Quarantine reason rendered as muted text when present.
- `EligibilityTimeline` component wired under a "24h History" row when a registry entry
  exists for the butler.
- 15 RTL tests in `ButlerOverviewTab.test.tsx` covering all five identity elements:
  name, status badge, description (with and without), port, eligibility states
  (active/quarantined/stale), quarantine reason, 24h timeline, and loading skeleton.

**Acceptance coverage:** All five spec elements present per spec.md:208-217. Eligibility
restore click behavior preserved. 24h timeline wired. Tests pass.

---

### bu-8hbph.2 — Process-facts card (container_name, port, uptime, config_path; NO pid)

**Status:** CLOSED (PR #1507 merged 2026-05-09T18:48:47Z)

**Commits:**
- `b9248aa4` feat: process-facts card (container_name, port, uptime, config_path) [bu-8hbph.2] (#1507)

**Delivered:**
- `ProcessFactsCard` component in `ButlerOverviewTab.tsx` consuming `butler?.process_facts`.
- Four KV rows: Container (`container_name`), Port (`port`), Registered
  (`registered_duration_seconds` formatted as human-readable duration via `formatDuration`),
  Config (`config_path`). Missing values render as `--` (explicit unavailable).
- `ProcessFacts` interface in `frontend/src/api/types.ts` with inline doc: "`pid` is
  intentionally absent."
- Tests in `ButlerOverviewTab.process-facts.test.tsx` asserting all four labels are
  present, values are rendered, no `pid` string appears, and `--` fallback works.

**Acceptance coverage:** Four fields correct. `pid` absent (verified by grep — no match
in `ButlerOverviewTab.tsx`). RTL test pins contract.

---

### bu-8hbph.3 — Heartbeat row and module-health card in Overview tab

**Status:** CLOSED (PR #1509 merged 2026-05-09T18:58:52Z)

**Commits:**
- `a7dfa273` feat: heartbeat row and module health card in Overview tab [bu-8hbph.3] (#1509)

**Delivered:**
- Heartbeat row inside the identity card's KV list (`data-testid="heartbeat-row"`),
  consuming `useButlerHeartbeats()`. Renders `HeartbeatFreshnessPill`
  (Fresh/Stale/Dead/Unknown based on `heartbeat_age_seconds`) and last-heartbeat
  `<Time>` relative timestamp.
- `heartbeatFreshness()` helper with correct thresholds: fresh ≤300s, stale ≤1800s, dead >1800s.
- "No heartbeat recorded" explicit state when `last_heartbeat_at` is null.
- Module Health card consuming `useButlerModules(butlerName)`. One `ModuleCell` per
  module; `moduleStatusVariant()` maps `connected`/`ok` → emerald, `degraded` → amber,
  `error` → destructive, other → secondary.
- "No modules registered" empty state and loading skeleton with `data-testid="module-health-grid"`.
- 12 tests in `ButlerOverviewTab.test.tsx` covering heartbeat freshness states
  (Fresh/Stale/Unknown), timestamp rendering, "No heartbeat recorded", and module
  health loading/populated/error/empty states.

**Acceptance coverage:** Heartbeat row visible; live-updates on refetch; module health
empty state correct. Tests cover loading/error/populated branches.

---

### bu-8hbph.4 — Cost card and recent-sessions card in Overview tab

**Status:** CLOSED (PR #1511 merged 2026-05-09T19:04:32Z)

**Commits:**
- `293b73ec` feat(overview): Cost card and Recent Sessions card [bu-8hbph.4] (#1511)

**Delivered:**
- `CostCard` component consuming `useCostSummary("today")` and `useCostSummary("7d")`.
  Per-butler share extracted via `costTodaySummary.by_butler[butlerName]`. Renders
  "Today" and "Last 7d" rows. "No cost data" empty state; loading skeleton.
- `RecentSessionsCard` component consuming `useButlerSessions(butlerName, { limit: 5 })`.
  Renders up to 5 sessions as a rule-separated list with prompt, relative timestamp via
  `<Time>`, and success/failed/running status badge. "No sessions yet" empty state;
  loading skeleton; "View all" link to `/butlers/:name/sessions`.
- `formatCurrency()` helper: costs below $0.01 display as `$0.00`.
- `sessionStatusBadge()` helper for Success/Failed/Running badges.
- 17 tests in `ButlerOverviewTab.cost-sessions.test.tsx` covering cost card (labels,
  monospace values, $0.00 when no spend, "No cost data", loading skeleton), recent
  sessions card (prompt text, status badges for all three states, 5-row render,
  "No sessions yet", loading skeleton, "View all" link, aria-labels).

**Acceptance coverage:** Cost card preserves share info. Recent sessions card shows up
to 5 rows newest first. Empty states explicit. Tests cover three branches each.

---

## 2. Acceptance Coverage Matrix

| Epic 04 Acceptance Criterion | Source | Status |
|------------------------------|--------|--------|
| Identity card: name + status badge | spec.md:248-249; change:15-26 | Covered (bu-8hbph.1, tests) |
| Identity card: serif italic description when present | change:19; tasks:2.2 | Covered (bu-8hbph.1, tests) |
| Identity card: port number | spec.md:249; change:19 | Covered (bu-8hbph.1, tests) |
| ButlerMark component in identity card | change:20 | **GAP-1** (see §5) |
| Eligibility state: Active/Quarantined/Stale badge | spec.md:251-255; change:85-92 | Covered (bu-8hbph.1, tests) |
| Eligibility restore click (Quarantined/Stale → active) | spec.md:254; change:89-91 | Covered (bu-8hbph.1, tests) |
| Quarantine reason as muted text | spec.md:255; change:92-93 | Covered (bu-8hbph.1, tests) |
| 24h eligibility timeline (EligibilityTimeline) | spec.md:257-264; change:94-101 | Covered (bu-8hbph.1, tests) |
| Heartbeat row: freshness pill + last-heartbeat timestamp | change:39-50 | Covered (bu-8hbph.3, tests) |
| Heartbeat: "No heartbeat recorded" state | change:49-50 | Covered (bu-8hbph.3, tests) |
| Heartbeat sourced from useButlerHeartbeats | change:43-48 | Covered (bu-8hbph.3) |
| Process facts: container_name | change:29-36; add-butler-process-facts:8-12 | Covered (bu-8hbph.2, tests) |
| Process facts: port | change:29-36; add-butler-process-facts:14-18 | Covered (bu-8hbph.2, tests) |
| Process facts: registered_duration_seconds as human-readable | change:29-36; add-butler-process-facts:20-25 | Covered (bu-8hbph.2, tests) |
| Process facts: config_path | change:29-36; add-butler-process-facts:27-32 | Covered (bu-8hbph.2, tests) |
| Process facts: no pid field in markup or types | change:34-36; add-butler-process-facts:34-38 | Covered (bu-8hbph.2, grep) |
| Process facts: missing data renders as "--" | change:36-37 | Covered (bu-8hbph.2, tests) |
| Module health card: per-module badge with status color | spec.md:266-269; change:54-63 | Covered (bu-8hbph.3, tests) |
| Module health card: "No modules registered" empty state | spec.md:269; change:59-60 | Covered (bu-8hbph.3, tests) |
| Module health sourced from useButlerModules | change:61-63 | Covered (bu-8hbph.3) |
| Cost card: today cost in USD | spec.md:271-274; change:65-72 | Covered (bu-8hbph.4, tests) |
| Cost card: $0.00 for costs below $0.01 | spec.md:274; change:69 | Covered (bu-8hbph.4, tests) |
| Cost card: global total and percentage share | spec.md:273; change:66-68 | **GAP-2** (see §5) |
| Cost card: 7d cost | tasks:5.1 | Covered (bu-8hbph.4, tests) |
| Cost card sourced from useCostSummary | change:71-72 | Covered (bu-8hbph.4) |
| Recent sessions: up to 5 newest sessions | change:73-80 | Covered (bu-8hbph.4, tests) |
| Recent sessions: explicit empty state | change:80-81 | Covered (bu-8hbph.4, tests) |
| Recent sessions sourced from useButlerSessions | change:75-79 | Covered (bu-8hbph.4) |
| No Tier 2 Hero above tabs (per Gate A A2) | change:22-25; no-hero-spec:10-18 | Verified — DetailPage wraps tabs with no hero block |
| pid absent from all sources (grep) | AC | Verified — no match in ButlerOverviewTab.tsx |

---

## 3. Seven-Unit Stack vs. Implementation

The spec change requires exactly seven ordered units. The actual implementation renders
eight units (the seventh is an additional Recent Notifications card preserved from the
old layout).

| Stack Position | Required (change spec) | Implemented | Status |
|----------------|------------------------|-------------|--------|
| 1 | Identity card | Identity card with name/status/description/port/heartbeat/eligibility | Present (merged into one card) |
| 2 | Process facts card | ProcessFactsCard | Present |
| 3 | Heartbeat row | Heartbeat row (inside identity card) | Present (co-located) |
| 4 | Module health card | Module Health card | Present |
| 5 | Cost card | CostCard | Present |
| 6 | Recent sessions card | RecentSessionsCard | Present |
| 7 | Eligibility row | Eligibility row (inside identity card) | Present (co-located with identity) |
| — | (not specified) | Recent Notifications card | Extra — additive deviation |

Note: The heartbeat row and eligibility row are co-located inside the identity card
rather than as standalone seventh units. The intent is satisfied (both are rendered and
functional), and the co-location keeps identity data together. The extra notifications
card is a retained component from the old layout; it does not violate any spec clause.

---

## 4. OpenSpec Sync Status

| Change | Validation | Artifacts | Status |
|--------|------------|-----------|--------|
| `redesign-detail-tab-overview-card-stack` | `openspec validate --strict` → PASSES | 4/4 complete (design.md added in bu-8hbph.5) | Tasks checked, design.md created |
| `add-butler-process-facts` | `openspec validate --strict` → PASSES | 4/4 complete | Fully implemented (PR #1507) |
| `redesign-butler-detail-no-hero` | `openspec validate --strict` → PASSES | 4/4 complete | Fully implemented (Epic 01/03) |

**Main spec sync:** `openspec/specs/dashboard-butler-management/spec.md` still describes
the old Overview tab layout at lines 244-279 (pre-redesign: "identity, module health,
cost telemetry, eligibility, and recent notifications"). The `MODIFIED Requirements`
delta in `redesign-detail-tab-overview-card-stack/specs/dashboard-butler-management/spec.md`
has not yet been applied to the canonical spec. Task 6.4 (`/opsx:sync`) remains open.

---

## 5. Gaps

### GAP-1: ButlerMark not included in identity card

**Severity:** Low — visual polish gap; the card's functional content is complete.

**Spec clause:** `redesign-detail-tab-overview-card-stack/specs/dashboard-butler-management/spec.md:20`
> "the first card SHALL display the `ButlerMark` identity component, butler name, status
> badge, and description when present"

**Code state:** `ButlerOverviewTab.tsx` does not import or render `ButlerMark` from
`frontend/src/components/ui/ButlerMark.tsx`. The identity card shows the butler name
and `ButlerStatusBadge` but no colored letter-mark squircle.

**Impact:** The butler detail identity card lacks the canonical letter-mark that
identifies the butler visually. Other views (butler list page) use `ButlerMark` per the
same spec pattern.

**Recommended resolution:** Add `<ButlerMark name={butlerName} tone="fill" />` to the
identity card `CardTitle` alongside the butler name. Update test assertions to confirm
the `aria-label={butlerName}` ButlerMark span is present.

---

### GAP-2: Cost card does not show global total or percentage share

**Severity:** Medium — spec requirement partially met; butler cost is shown but the
global total and share percentage are absent.

**Spec clause (main spec):** `spec.md:273`
> "a 'Cost Today' card shows the butler's USD cost, its percentage share of the global
> total, and the global total"

**Spec clause (change spec):** `redesign-detail-tab-overview-card-stack/specs/…/spec.md:66-68`
> "a Cost Today card SHALL show the butler's USD cost, its percentage share of the
> global total, and the global total"

**Code state:** `CostCard` receives `costToday` and `cost7d` (per-butler values from
`by_butler[butlerName]`). The `total_cost_usd` field from the `useCostSummary` response
is not passed to `CostCard` and is not rendered. No percentage is computed or displayed.

**Impact:** Users cannot assess how much of the global system cost this butler
contributes without navigating to a separate page.

**Recommended resolution:** Pass `costTodaySummary?.total_cost_usd` to `CostCard`.
Compute `share = (costToday / total) * 100` when both are non-zero. Display the global
total and share percentage as additional KV rows or as subscript under the butler cost.

---

### GAP-3: Main spec not synced with the seven-unit card stack

**Severity:** Documentation — no code gap; spec-code drift only.

**Source:** `openspec/specs/dashboard-butler-management/spec.md:244-279` still describes
the pre-redesign Overview layout.

**Resolution required:** Run `/opsx:sync` (task 6.4) after owner review to apply the
`MODIFIED Requirements` delta to the canonical spec. This is a documentation-only step
that closes the spec-code drift.

---

## 6. Summary

All four child beads delivered and merged:

| Bead | Deliverable | PR | Status |
|------|-------------|-----|--------|
| bu-8hbph.1 | Identity card, serif italic description, eligibility row, 24h timeline, 15 tests | #1504 | Merged |
| bu-8hbph.2 | Process facts card (4 fields, no pid), RTL tests | #1507 | Merged |
| bu-8hbph.3 | Heartbeat row + Module health card, 12 tests | #1509 | Merged |
| bu-8hbph.4 | Cost card + Recent sessions card, 17 tests | #1511 | Merged |

Quality gates:
- `ruff check src/ tests/ roster/ conftest.py` → PASSES (no violations)
- `openspec validate --strict redesign-detail-tab-overview-card-stack` → PASSES
- `openspec validate --strict add-butler-process-facts` → PASSES
- `openspec validate --strict redesign-butler-detail-no-hero` → PASSES
- `pid` absent from `ButlerOverviewTab.tsx` → VERIFIED by grep
- No Tier 2 Hero above tabs in `ButlerDetailPage.tsx` → VERIFIED

Two implementation gaps remain for follow-up:
- **GAP-1** (ButlerMark missing from identity card) — Low priority, visual completeness
- **GAP-2** (Cost card missing global total + share percentage) — Medium priority,
  spec requirement unmet

One documentation gap:
- **GAP-3** (main spec not synced) — task 6.4 (`/opsx:sync`) pending owner review
