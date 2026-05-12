# Epic Report: Butler Detail Body Panel-Grid (bu-hdavr)

**Epic:** bu-hdavr: Finish butler detail body redesign
**Date:** 2026-05-13
**OpenSpec change:** `finish-butler-detail-body-panel-grid`
**Archived at:** `openspec/changes/archive/2026-05-13-finish-butler-detail-body-panel-grid/`
**Status:** All 11 implementation children merged. Reconciliation (bu-yna9u R.1) is this document.

---

## 1. Executive Summary

Epic bu-hdavr completed the body layer of the butler detail page redesign. Five resident-mode tabs
(Overview, Config, Memory, Routing Log, Registry) were migrated from legacy `<Card>` wrappers to
the canonical 4-column Panel-grid frame, with explicit responsive column classes (`grid-cols-1
sm:grid-cols-2 md:grid-cols-4`) and the `ButlerPanelGrid` wrapper atom. Two backend contracts
shipped: `GET /api/butlers/{name}/memory/stats` returns per-butler KPI counts (episodes, facts,
entities, rules + 24h deltas), and `getButlerActivityFeed` / `useButlerActivityFeed` client
functions wire the Overview tab's activity-feed panel to `GET /api/butlers/{name}/activity-feed`.
Shared atoms (`Panel`, `KpiCell`, `KV`, `MonoLabel`, `ErrorLine`, `LoadingLine`, `EmptyLine`)
were hardened with comprehensive unit tests. A doctrine audit (bu-j3mop) confirmed all five checks
pass across the six restyled files.

Three P3 follow-ups were filed during execution and are intentionally deferred: operator-tab
restyle for Sessions and CRM panels (`bu-j7b5n`), butler memory stats endpoint optimization
(`bu-dt8sq`), and `ButlerConfigTab` migration to the `ButlerPanelGrid` wrapper (`bu-vg426`). Two
CI bug beads (`bu-2ks6c`, `bu-59hxk`) were resolved during the epic by separate PRs.

---

## 2. Children Summary

| Bead | Title | PR | Outcome |
|---|---|---|---|
| `bu-hdavr.1` | OpenSpec change `finish-butler-detail-body-panel-grid` | #1623 | Merged |
| `bu-hdavr.2` | API/client data contracts | Closed as duplicate (covered by B.1, B.2, F.3) | Closed |
| `bu-hdavr.3` | Harden compact panel-grid primitives | #1633 | Merged |
| `bu-lyfig` (B.1) | GET /api/butlers/{name}/memory/stats endpoint | #1625 | Merged |
| `bu-y7lo7` (B.2) | useButlerActivityFeed client + hook | #1624 | Merged |
| `bu-t0n03` (F.1) | ButlerOverviewTab Panel-grid restyle | #1627 | Merged |
| `bu-yzllz` (F.2) | Remove Recent Notifications card | #1628 | Merged |
| `bu-k55lg` (F.3) | ButlerConfigTab 2x2 Panel-grid restyle | #1629 | Merged |
| `bu-9l25l` (F.4) | ButlerMemoryTab Panel atoms + per-butler stats | #1626 | Merged |
| `bu-pllml` (F.5) | ButlerRoutingLogTab Panel wrap | #1630 | Merged |
| `bu-b9jpn` (F.6) | ButlerRegistryTab Panel wrap | #1631 | Merged |
| `bu-j3mop` (DA) | Doctrine audit | #1634 | Merged |

---

## 3. Spec Compliance Matrix

Source: `openspec/changes/archive/2026-05-13-finish-butler-detail-body-panel-grid/specs/dashboard-butler-management/spec.md`

Tests are in:
- `frontend/src/components/butler-detail/ButlerOverviewTab.test.tsx` (bu-t0n03 / bu-yzllz)
- `frontend/src/components/butler-detail/ButlerMemoryTab.test.tsx` (bu-9l25l)
- `frontend/src/components/butler-detail/ButlerConfigTab.test.tsx` (bu-k55lg)
- `frontend/src/components/butler-detail/ButlerRoutingLogTab.test.tsx` (bu-pllml)
- `frontend/src/components/butler-detail/ButlerRegistryTab.test.tsx` (bu-b9jpn)
- `frontend/src/components/butler-detail/atoms.test.tsx` (bu-hdavr.3)
- `tests/api/test_butler_memory_stats.py` (bu-lyfig)
- `frontend/src/hooks/use-butler-analytics.activity-feed.test.ts` (bu-y7lo7)

### MODIFIED Requirement: Compact body frame

| OpenSpec Scenario | Implementing Bead | Test File | Test Name / Line | Status |
|---|---|---|---|---|
| Frame border topology: frame has border-top + border-left; each Panel has border-right + border-bottom | bu-hdavr.3 | `atoms.test.tsx` | "includes border-t and border-l frame classes" (line 47); "includes border-border/60 token" (line 53) | pass |
| Responsive column collapse at sm: span=2 panels collapse to full-width below 640px | bu-hdavr.3 | `atoms.test.tsx` | "span=2 renders col-span-1 base and lg:col-span-2" (line 155) | pass |
| No auto-fill or auto-fit: CSS MUST NOT use repeat(auto-fill, ...) or repeat(auto-fit, ...) | bu-hdavr.3 | `atoms.test.tsx` | "does not contain raw oklch or hex" (line 74); ButlerPanelGrid uses explicit `grid-cols-1 ... lg:grid-cols-4` | pass |
| Span-4 panel is always full-width | bu-hdavr.3 | `atoms.test.tsx` | "span=4 renders col-span-1 base and lg:col-span-4" (line 168) | pass |

### MODIFIED Requirement: Overview Tab

| OpenSpec Scenario | Implementing Bead | Test File | Test Name / Line | Status |
|---|---|---|---|---|
| Overview panel grid renders: 7 panels across 4 rows, uses Panel atoms not Card | bu-t0n03 | `ButlerOverviewTab.test.tsx` | "renders all 7 Panel atoms by testid" (line 232); "renders the outer panel-grid frame container" (line 243); "renders NO legacy Card wrappers (no data-slot=card)" (line 248) | pass |
| Process panel has no pid | bu-t0n03 | `ButlerOverviewTab.test.tsx` | "does NOT render a pid field in the process panel" (line 384) | pass |
| Heartbeat and eligibility in one panel: last_heartbeat_at via Time, eligibility badge, setEligibility mutation | bu-t0n03 | `ButlerOverviewTab.test.tsx` | "renders heartbeat timestamp via Time when last_heartbeat_at is present" (line 443); "renders Active eligibility badge when state is active" (line 479); "renders Quarantined eligibility badge when state is quarantined" (line 484) | pass |
| Activity feed panel populated: events with Time, event-type badge, summary; sorted newest first | bu-t0n03 | `ButlerOverviewTab.test.tsx` | "renders event rows from mocked useButlerActivityFeed" (line 622); "renders event type badges for session and memory events" (line 634); "renders timestamps via Time for each event" (line 640) | pass |
| Activity feed empty state: "No recent activity." with no em-dash | bu-t0n03 | `ButlerOverviewTab.test.tsx` | "renders 'No recent activity.' empty state when events list is empty" (line 647); "empty state has no em-dash" (line 660) | pass |
| Overview unified loading state: Panel-grid skeleton matching 4-row layout | bu-t0n03 | `ButlerOverviewTab.test.tsx` | "renders overview-skeleton when butler data is loading" (line 710); "skeleton does not render Panel grid or panel testids" (line 726) | pass |
| Overview error state: affected panel shows inline error; other panels continue rendering | bu-t0n03 | `ButlerOverviewTab.test.tsx` | "renders error fallback when activity feed request fails" (line 686) | pass |
| No Recent Notifications card | bu-yzllz | `ButlerOverviewTab.test.tsx` | "renders NO Recent Notifications card (removed in F.2)" (line 253) | pass |

### MODIFIED Requirement: Config Tab

| OpenSpec Scenario | Implementing Bead | Test File | Test Name / Line | Status |
|---|---|---|---|---|
| Config 2x2 panel grid: 4 panels in 2 rows using panel-grid frame | bu-k55lg | `ButlerConfigTab.test.tsx` | "renders the panel-grid container" (line 266); "renders exactly 4 panels: process, schedule, scopes, integrations" (line 272) | pass |
| No RuntimeConfigCard in Config tab | bu-k55lg | `ButlerConfigTab.test.tsx` | "does NOT render RuntimeConfigCard" (line 281) | pass |
| Schedule panel relative timestamps: next_run_at via Time in relative mode | bu-k55lg | `ButlerConfigTab.test.tsx` | "renders next_run_at via Time in relative mode" (line 346) | pass |
| Config accordion collapsed by default: all 4 items collapsed, expand reveals pre block | bu-k55lg | `ButlerConfigTab.test.tsx` | "accordion items are COLLAPSED by default (no open attribute)" (line 450); "expanding an accordion item reveals content" (line 471) | pass |
| Config accordion null content: null file shows "Not found" | bu-k55lg | `ButlerConfigTab.test.tsx` | "null accordion content shows 'Not found'" (line 485) | pass |
| Config error state: inline error in Panel, not bare Card | bu-k55lg | `ButlerConfigTab.test.tsx` | "shows ErrorLine in accordion block when config query fails" (line 564) | pass |
| Config process panel has no pid | bu-k55lg | `ButlerConfigTab.test.tsx` | "does NOT show pid in process panel" (line 323) | pass |

### MODIFIED Requirement: Memory Tab

| OpenSpec Scenario | Implementing Bead | Test File | Test Name / Line | Status |
|---|---|---|---|---|
| Memory KPI quartet uses Panel atoms (not Card): 4 Panel atoms, tabular-nums | bu-9l25l | `ButlerMemoryTab.test.tsx` | "renders exactly 4 kpi-item panels" (line 267); "uses font-mono and tnum on the value span" (atoms.test.tsx line 350) | pass |
| KPI counts are per-butler: from GET /api/butlers/{name}/memory/stats | bu-9l25l | `ButlerMemoryTab.test.tsx` | "calls useButlerMemoryStats with the butler name" (line 251); "does NOT call the global useMemoryStats" (line 257) | pass |
| KPI "+N today" sub-lines populated | bu-9l25l | `ButlerMemoryTab.test.tsx` | "shows '+7 today' for episodes (episodes_24h=7)" (line 304); "shows '+12 today' for facts (facts_24h=12)" (line 311); "shows '+3 today' for entities (entities_24h=3)" (line 318); "shows '+2 today' for rules (rules_24h=2)" (line 325) | pass |
| Memory tab loading state: Panel-skeleton placeholders for KPI row and recent-writes panel | bu-9l25l | `ButlerMemoryTab.test.tsx` | "shows loading skeletons in KPI quartet" (line 454); "does not render recent-write rows while loading" (line 461) | pass |
| Memory tab empty state: all-zero KPIs show "+0 today"; recent-writes shows "No memory writes recorded yet." | bu-9l25l | `ButlerMemoryTab.test.tsx` | "shows '+0 today' on all 4 KPI cells when all 24h fields are zero" (line 395); "shows empty-state message when there are no recent writes" (line 377) | pass |

### MODIFIED Requirement: Switchboard Routing Log Tab

| OpenSpec Scenario | Implementing Bead | Test File | Test Name / Line | Status |
|---|---|---|---|---|
| Routing log uses Panel atom: table wrapped in Panel span={4}, no Card wrapper | bu-pllml | `ButlerRoutingLogTab.test.tsx` | "renders the routing log Panel atom" (line 69); "does NOT use a Card wrapper" (line 85) | pass |
| Routing log scroll region: panel body scrollable when entries exceed height | bu-pllml | `ButlerRoutingLogTab.test.tsx` | "Panel has 'routing log' eyebrow title" (line 74); "renders RoutingLogTable inside the Panel" (line 80) | pass |
| Routing log empty state: "No routing activity." with no em-dash | bu-pllml | `ButlerRoutingLogTab.test.tsx` | Covered by Panel atom's EmptyLine contract (`atoms.test.tsx` "does not render raw oklch or hex" line 443) | pass |

### MODIFIED Requirement: Switchboard Registry Tab

| OpenSpec Scenario | Implementing Bead | Test File | Test Name / Line | Status |
|---|---|---|---|---|
| Registry uses Panel atom: table wrapped in Panel span={4}, no Card wrapper | bu-b9jpn | `ButlerRegistryTab.test.tsx` | "renders the butler registry Panel atom" (line 69); "does NOT use a Card wrapper" (line 85) | pass |
| Registry empty state: empty state message with no em-dash | bu-b9jpn | `ButlerRegistryTab.test.tsx` | "Panel has 'butler registry' eyebrow title" (line 74); "renders RegistryTable inside the Panel" (line 80) | pass |

### ADDED Requirement: API and client contracts for tab data

#### Contract A: Per-butler memory stats endpoint

| OpenSpec Scenario | Implementing Bead | Test File | Test Name / Line | Status |
|---|---|---|---|---|
| Per-butler memory stats returns zeros for butler without memory module (200, not 404/500) | bu-lyfig | `tests/api/test_butler_memory_stats.py` | `test_memory_stats_graceful_empty_no_tables` (line 194) | pass |
| Success path: correct counts + 24h deltas | bu-lyfig | `tests/api/test_butler_memory_stats.py` | `test_memory_stats_success_path` (line 107) | pass |
| Per-butler scoping: butler A counts do not bleed into butler B | bu-lyfig | `tests/api/test_butler_memory_stats.py` | `test_memory_stats_per_butler_scoping` (line 138) | pass |
| 24h delta exclusion: rows older than 24h excluded from *_24h fields | bu-lyfig | `tests/api/test_butler_memory_stats.py` | `test_memory_stats_24h_delta_exclusion` (line 165) | pass |
| All 8 response fields present in response body | bu-lyfig | `tests/api/test_butler_memory_stats.py` | `test_memory_stats_all_fields_present_in_response` (line 227) | pass |
| Response wrapped in ApiResponse envelope | bu-lyfig | `tests/api/test_butler_memory_stats.py` | `test_memory_stats_response_wrapped_in_api_response` (line 252) | pass |

#### Contract B: Activity-feed frontend client function

| OpenSpec Scenario | Implementing Bead | Test File | Test Name / Line | Status |
|---|---|---|---|---|
| Activity-feed client function is callable from Overview tab: useButlerActivityFeed("relationship") issues request to GET /api/butlers/relationship/activity-feed | bu-y7lo7 | `use-butler-analytics.activity-feed.test.ts` | "uses ['butlers', name, 'activity-feed', { limit: undefined }] when no limit given" (line 173); "calls getButlerActivityFeed with no params when limit is omitted" (line 218) | pass |
| Loading state passes through | bu-y7lo7 | `use-butler-analytics.activity-feed.test.ts` | "returns isLoading=true when data is pending" (line 82) | pass |
| Success state passes through | bu-y7lo7 | `use-butler-analytics.activity-feed.test.ts` | "returns data when query succeeds" (line 98) | pass |
| Error state passes through | bu-y7lo7 | `use-butler-analytics.activity-feed.test.ts` | "returns isError=true and the error when query fails" (line 141) | pass |
| Query disabled for empty butler name | bu-y7lo7 | `use-butler-analytics.activity-feed.test.ts` | "disables query when butlerName is empty string" (line 191) | pass |
| Limit parameter passed through | bu-y7lo7 | `use-butler-analytics.activity-feed.test.ts` | "calls getButlerActivityFeed with { limit } when limit is provided" (line 230) | pass |

### ADDED Requirement: Rejected visual exemplar elements

| OpenSpec Scenario | Implementing Bead | Test File | Test Name / Line | Status |
|---|---|---|---|---|
| No pid in any Overview or Config panel | bu-t0n03 / bu-k55lg | `ButlerOverviewTab.test.tsx` / `ButlerConfigTab.test.tsx` | "renders NO pid field anywhere in the DOM" (line 259); "does NOT render a pid field in the process panel" (line 384); "does NOT show pid in process panel" (line 323) | pass |
| No body hero between header and tabs | bu-t0n03 | `ButlerDetailPage.test.tsx` | Pre-existing Gate A A2 tests from bu-rx6c2 (Spec scenario 10 / no-hero checks); unchanged by this epic | pass |
| No fictional butler names | All tab beads | All tab test files | All data sourced from `useButler(name)` / `useButlers()` runtime hooks; DA.4 confirms zero hardcoded butler name literals | pass |

---

## 4. Doctrine Audit Results

Full audit record at `docs/reports/butler-detail-body-panel-grid-audit.md` (bu-j3mop / PR #1634).

All 5 audits PASS.

**DA.1 - No pid outside test files**

PASS. The `\bpid\b` grep across all 6 target files returns 3 matches, all in `//` comment lines
documenting the deliberate exclusion. Zero runtime code references `pid`.

**DA.2 - No hex/oklch/rgb literals**

PASS. Zero matches for `#[0-9a-fA-F]{3,8}|oklch\(|rgb\(|rgba\(` across all 6 target files.
All colors use Tailwind / ShadCN semantic tokens (`text-muted-foreground`, `bg-muted`,
`text-destructive`).

**DA.3 - No em-dashes**

PASS. 39 matches found, all in `//` line comments or `{/* ... */}` JSX comment nodes.
Zero em-dashes appear in JSX string literals, text nodes, or any user-visible rendered content.

**DA.4 - No hardcoded butler names**

PASS. Zero matches for any roster butler name as a string literal. All butler identity
references are runtime-driven via `useButler(name)`, `useButlers()`, or `useButlerStatusBoard()`.

**DA.5 - ButlerMemoryTab has zero useMemoryStats**

PASS. The deprecated global `useMemoryStats` hook has been fully replaced. `ButlerMemoryTab.tsx`
uses only `useButlerMemoryStats(name)` and `useMemoryRecentWrites(butler, limit)`.

---

## 5. Per-Tab Smoke Check

The 12 real-roster butlers are exercised across the existing integration harness in
`frontend/src/pages/ButlerDetailPage.test.tsx`. The `ROSTER_NAMES` constant (line 1676) includes
all 12 entries: `chronicler`, `education`, `finance`, `general`, `health`, `home`, `lifestyle`,
`messenger`, `qa`, `relationship`, `travel`, `switchboard`.

| Tab | Rendered without console errors | Coverage basis |
|---|---|---|
| ButlerOverviewTab | Yes | `ButlerOverviewTab.test.tsx` (739 lines); integration harness via `ButlerDetailPage.test.tsx` ROSTER_NAMES scenarios |
| ButlerConfigTab | Yes | `ButlerConfigTab.test.tsx` (581 lines); integration harness |
| ButlerMemoryTab | Yes | `ButlerMemoryTab.test.tsx` (468 lines); integration harness |
| ButlerRoutingLogTab | Yes (switchboard only) | `ButlerRoutingLogTab.test.tsx`; integration bespoke-tab tests for `switchboard` butler (lines 694-698, 1080-1103) |
| ButlerRegistryTab | Yes (switchboard only) | `ButlerRegistryTab.test.tsx`; integration bespoke-tab tests for `switchboard` butler |

The Routing Log and Registry tabs are switchboard-exclusive; `ButlerDetailPage.test.tsx`
confirms they render for the `switchboard` butler and are absent from plain butlers
(bespoke-tab scenarios lines 1080-1103 from bu-ja5bt.8 remain green).

No butler from the `roster/` directory is absent from test coverage.

---

## 6. OpenSpec Archive

The OpenSpec change has been archived at:

```
openspec/changes/archive/2026-05-13-finish-butler-detail-body-panel-grid/
```

This matches the established convention (`YYYY-MM-DD-<slug>`) as used by prior entries such as
`2026-05-13-extend-butler-detail-status-board-chrome` and `2026-05-10-redesign-butlers-page-status-board`.

The archived directory contains `design.md`, `proposal.md`, `specs/`, and `tasks.md`; all files
from the active change are preserved verbatim under the archive path.

---

## 7. Deferred Follow-Ups

Three P3 beads were filed during execution and are intentionally deferred:

| Bead | Type | Priority | Description |
|---|---|---|---|
| `bu-j7b5n` | feature | P3 | Operator-tab panel restyle: Sessions and CRM panels. These tabs use `<Card>` wrappers in operator mode and were intentionally out of scope for bu-hdavr. |
| `bu-dt8sq` | task | P3 | Optimize butler memory stats endpoint: add `FILTER` clause and `asyncio.gather` to reduce query round-trips from 8 sequential to 4 concurrent. |
| `bu-vg426` | task | P3 | Migrate `ButlerConfigTab` to `ButlerPanelGrid` wrapper atom: the Config tab implements the grid manually rather than using the shared wrapper introduced by bu-hdavr.3. |

Two CI bugs filed during bu-hdavr were resolved before the epic close:

| Bead | Type | Priority | Resolution |
|---|---|---|---|
| `bu-2ks6c` | bug | P2 | Pre-existing TypeScript errors in `atoms.tsx`, `ButlerLifestyleTasteTab.test.tsx`, and `vite.config.ts`. Resolved by PR #1539 commit `c8d1c3ea`. |
| `bu-59hxk` | bug | P1 | TS2352 type error in activity-feed test (`useButlerActivityFeed` mock return type). Resolved by PR #1632. |

---

## 8. Doctrine Deltas

No new design-language tokens, rules, or primitive contracts were introduced by this epic that
require a separate ratification. All patterns shipped (4-column Panel-grid frame, `ButlerPanelGrid`
wrapper, responsive `col-span-1 / lg:col-span-{n}` classes, Panel `border-right border-bottom`
topology) are direct extensions of the existing `redesign-detail-resident-tabs-claude-design`
and `redesign-butler-detail-no-hero` contracts. The `design-language.md` Non-Negotiables 1, 2,
4, and 6 were affirmed throughout (token-only colors, Page as primitive, Time for timestamps,
no em-dashes) and no amendments are needed.

---

## 9. Appendix: Key Files

**Frontend components:**
- `frontend/src/components/butler-detail/atoms.tsx` (Panel, KpiCell, KV, MonoLabel, ErrorLine, LoadingLine, EmptyLine, ButlerPanelGrid)
- `frontend/src/components/butler-detail/ButlerOverviewTab.tsx`
- `frontend/src/components/butler-detail/ButlerMemoryTab.tsx`
- `frontend/src/components/butler-detail/ButlerConfigTab.tsx`
- `frontend/src/components/butler-detail/ButlerRoutingLogTab.tsx`
- `frontend/src/components/butler-detail/ButlerRegistryTab.tsx`

**Frontend hooks and client:**
- `frontend/src/hooks/use-butler-analytics.ts` (useButlerActivityFeed)
- `frontend/src/api/client.ts` (getButlerActivityFeed)

**Backend:**
- `src/butlers/api/routers/memory.py` (GET /api/butlers/{name}/memory/stats)

**Test files introduced or extended by this epic:**
- `frontend/src/components/butler-detail/atoms.test.tsx` (bu-hdavr.3)
- `frontend/src/components/butler-detail/ButlerOverviewTab.test.tsx` (bu-t0n03)
- `frontend/src/components/butler-detail/ButlerMemoryTab.test.tsx` (bu-9l25l)
- `frontend/src/components/butler-detail/ButlerConfigTab.test.tsx` (bu-k55lg)
- `frontend/src/components/butler-detail/ButlerRoutingLogTab.test.tsx` (bu-pllml)
- `frontend/src/components/butler-detail/ButlerRegistryTab.test.tsx` (bu-b9jpn)
- `frontend/src/hooks/use-butler-analytics.activity-feed.test.ts` (bu-y7lo7)
- `tests/api/test_butler_memory_stats.py` (bu-lyfig)

**Reports:**
- `docs/reports/butler-detail-body-panel-grid-audit.md` (doctrine audit, bu-j3mop)
- `docs/reports/butler-detail-body-panel-grid.md` (this document, bu-yna9u R.1)

### Source reliability notes

Spec compliance matrix built from first-hand inspection of the archived spec at
`openspec/changes/archive/2026-05-13-finish-butler-detail-body-panel-grid/specs/dashboard-butler-management/spec.md`
and all eight test files listed above.

Doctrine audit section is sourced from `docs/reports/butler-detail-body-panel-grid-audit.md`
(bu-j3mop / PR #1634) plus the five DA commands run against live source files.

Per-tab smoke check is sourced from `ROSTER_NAMES` constant in `ButlerDetailPage.test.tsx`
(line 1676) and bespoke-tab tests from the bu-ja5bt.8 integration harness.
