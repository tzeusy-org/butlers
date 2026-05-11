# Delivery Report: Butler Detail Page Redesign

**Epic:** bu-iuol4
**Reconciliation bead:** bu-iuol4.37
**Date:** 2026-05-11
**OpenSpec changes:** redesign-detail-resident-tabs-claude-design (PR #1536), add-bespoke-butler-tab-capability (PR #1553)

---

## 1. Executive Summary

The butler detail page redesign epic (bu-iuol4) delivered 25 merged PRs across spec authoring, backend endpoints, frontend primitives, and per-butler tab implementations. Shipped work includes: 3 spec PRs (resident-tab visual contract, bespoke tab capability, label registry), 10 backend endpoint PRs (hourly/daily activity, session-kinds, structured logs, activity-feed, relationship warmth, health measurements, general stats, education analytics, messenger delivery health), 3 frontend primitive PRs (RangeToggle, DayBars, Panel+KpiCell+MonoLabel atoms), and 9 bespoke tab implementation PRs (chronicler Timelines, relationship Contacts, health Measurements, finance Finances, qa Investigations, home Devices, lifestyle Taste, messenger Conversations, travel Trips). Two base resident tabs shipped: Approvals and Logs. Production regressions were caught and fixed: PR #1555 unified the health measurements read path from the legacy `measurements` table to the canonical `facts` table (measurements written by the MCP tool were invisible to the dashboard API until the fix), and PR #1572 added isError handling to all five relationship Contacts tab panels. Three tab implementations remain deferred: Activity (bu-iuol4.16), Spend (bu-iuol4.19), and Memory (bu-iuol4.20); the general Collections tab (bu-iuol4.30) and education Reviews redesign (bu-iuol4.26, open PR #1582) are blocked.

---

## 2. Spec Compliance Matrix

### redesign-detail-resident-tabs-claude-design (PR #1536)

| Scenario | Implementing Bead | Test File / Lines | Status |
|---|---|---|---|
| Frame border topology | bu-iuol4.13 (atoms), bu-iuol4.25/.29/.32/.36 | `atoms.test.tsx` — Panel span/border tests | pass |
| Panel renders title eyebrow | bu-iuol4.13 | `atoms.test.tsx:173+` (Panel title and sub section) | pass |
| Panel scroll body | bu-iuol4.13 | `atoms.test.tsx:112-126` (Panel scroll prop) | pass |
| Panel span | bu-iuol4.13 | `atoms.test.tsx:81-111` (Panel span section) | pass |
| KPI quartet renders four panels | bu-iuol4.13 (KpiCell), bu-iuol4.25/.29/.32/.36 | Per-bespoke-tab `*.test.tsx` — kpi-quartet sections | pass |
| KPI tone on elevated error count | bu-iuol4.13 | `atoms.test.tsx` — KpiCell tone tests | pass |
| KPI sub-line delta | bu-iuol4.13 | `atoms.test.tsx` — KpiCell sub prop tests | pass |
| RangeToggle default state | bu-iuol4.14 | `RangeToggle.test.tsx` (24h selected by default) | pass |
| RangeToggle absent for non-range tabs | bu-iuol4.14 | `RangeToggle.test.tsx` (rendering contract) | pass |
| Activity tab KPI quartet | bu-iuol4.16 | not yet implemented | skip (deferred) |
| Activity stripe for 24h range | bu-iuol4.16 | not yet implemented | skip (deferred) |
| Day bars for 7d or 30d range | bu-iuol4.15 | `DayBars.test.tsx` — 7d/30d rendering | pass |
| Kind breakdown panel | bu-iuol4.7 (backend) + bu-iuol4.16 (frontend) | frontend not yet implemented | partial (backend pass, frontend deferred) |
| Activity tab empty state | bu-iuol4.16 | not yet implemented | skip (deferred) |
| Log level filter chips | bu-iuol4.17 | `ButlerLogsTab.test.tsx:211-313` (filter chips section) | pass |
| Log line column widths | bu-iuol4.17 | `ButlerLogsTab.test.tsx:313-356` (log line list section) | pass |
| Log level color tokens | bu-iuol4.17 | `ButlerLogsTab.test.tsx:358-396` (level tone classes section) | pass |
| Logs tab auto-scroll | bu-iuol4.17 | `ButlerLogsTab.test.tsx:472-513` (auto-scroll toggle section) | pass |
| Logs tab empty state | bu-iuol4.17 | `ButlerLogsTab.test.tsx:398-426` (empty state section) | pass |
| Approvals list with pending items | bu-iuol4.18 | `ButlerApprovalsTab.test.tsx:151-231` | pass |
| Approvals empty state | bu-iuol4.18 | `ButlerApprovalsTab.test.tsx:234-260` | pass |
| Approvals age rendering | bu-iuol4.18 | `ButlerApprovalsTab.test.tsx:173-231` (severity dot rendering) | pass |
| Spend KPI quartet | bu-iuol4.19 | not yet implemented | skip (deferred) |
| Spend trend bar chart | bu-iuol4.19 | not yet implemented | skip (deferred) |
| Model breakdown KV list | bu-iuol4.19 | not yet implemented | skip (deferred) |
| Spend tab empty state | bu-iuol4.19 | not yet implemented | skip (deferred) |
| Memory KPI quartet with "+N today" sub-lines | bu-iuol4.20 | not yet implemented | skip (deferred) |
| Recent-writes feed scroll | bu-iuol4.20 | not yet implemented | skip (deferred) |
| Memory tab empty state | bu-iuol4.20 | not yet implemented | skip (deferred) |
| Config 2x2 panel grid | bu-iuol4.2/.3 spec; Config tab not explicitly in bead scope | `ButlerConfigTab.tsx` pre-existing | partial |
| Schedule panel relative timestamps | pre-existing ButlerSchedulesTab | pre-existing | pass |
| Config markdown accordion collapsed by default | pre-existing | pre-existing | pass |
| Config error and null states | pre-existing | pre-existing | pass |

### add-bespoke-butler-tab-capability (PR #1553)

| Scenario | Implementing Bead | Test File / Lines | Status |
|---|---|---|---|
| Bespoke tab appears in resident mode tab list | bu-iuol4.3 + per-bespoke beads | `ButlerDetailPage.test.tsx` — per-butler tab presence tests | pass |
| Bespoke tab appears in operator mode tab list | bu-iuol4.3 + per-bespoke beads | `ButlerDetailPage.test.tsx:1111` (accepted operator tab keys) | pass |
| Bespoke tab is lazy-loaded | all bespoke tab beads (bu-iuol4.21/.23/.25/.28/.29/.32/.33/.34/.36) | `ButlerDetailPage.tsx` — `lazy()` wraps all bespoke tabs | pass |
| Bespoke tab empty state when butler offline | per-bespoke tab components | per-tab test files — empty state sections | pass |
| Deep link to bespoke tab does not force mode switch | butler-detail-tabs.ts `isValidTab` | `ButlerDetailPage.test.tsx` — isValidTab tests | pass |
| Switchboard has no resident bespoke tab | butler-detail-tabs.ts | `ButlerDetailPage.test.tsx` — switchboard-specific test cases | pass |
| Single bespoke tab per butler | butler-detail-tabs.ts `getAllTabs` | `ButlerDetailPage.test.tsx:1452-1536` (negative tab tests) | pass |
| Each butler renders its registered bespoke tab label | per-bespoke beads | per-tab test files + `ButlerDetailPage.test.tsx` | pass (10/11; general deferred) |
| Switchboard does not render a bespoke tab from the registry | butler-detail-tabs.ts | `ButlerDetailPage.test.tsx` — switchboard tests | pass |
| New butlers (general, lifestyle, messenger, qa) include bespoke tabs | bu-iuol4.28/.33/.34 (lifestyle/messenger/qa done; general deferred) | `ButlerLifestyleTasteTab.test.tsx`, `ButlerMessengerConversationsTab.test.tsx`, `ButlerQaInvestigationsTab.test.tsx` | partial (3/4 pass; general deferred) |

---

## 3. Doctrine Audit

### No Tier 2 hero rendered on /butlers/{name}

```
grep -r "<Hero>" frontend/src/components/butler-detail/
```

**Result:** Zero matches.

**Status: PASS** — No Hero component appears anywhere in `butler-detail/`.

---

### No pid surfaced

```
grep -r "pid" frontend/src/components/butler-detail/
```

**Result:** Only in `ButlerOverviewTab.process-facts.test.tsx` — the test file explicitly asserts `"pid"` does NOT appear in the rendered card (lines 144-147). No production component renders pid.

**Status: PASS** — pid is absent from production components; the test file references it only to assert its absence.

---

### All tokens are CSS variables

```
grep -nE "oklch\(|#[0-9a-fA-F]{6}|#[0-9a-fA-F]{3}\b" frontend/src/components/butler-detail/*.tsx
```

**Result:** Zero matches.

**Status: PASS** — No raw oklch or hex literals in any butler-detail TSX file.

---

### No em-dashes in copy prose

```
grep -n "—" frontend/src/components/butler-detail/*.tsx
```

**Result:** Em-dashes appear in two contexts:

1. **Null KPI placeholders** (e.g., `"—"` as the value prop on KpiCell calls in ButlerChroniclerTimelinesTab, ButlerLifestyleTasteTab, ButlerHealthMeasurementsTab). These are data placeholders, not prose — this usage is explicitly allowed per design-language.md doctrine.

2. **Prose copy violation:** `ButlerEducationReviewsTab.tsx:239` renders `"No reviews scheduled — keep learning and reviews will appear here."` and line 361 renders `"No frontier nodes yet — keep mastering prerequisites!"`. Both use em-dash as a prose separator.

**Status: PARTIAL FAIL** — The `ButlerEducationReviewsTab.tsx` file (pre-existing, not yet redesigned in this epic) contains em-dashes in empty-state prose. All files introduced or redesigned in this epic are clean. The violation is in the legacy implementation that bu-iuol4.26 was supposed to replace; the redesign is blocked on PR #1582.

---

### Real roster correctness — bespoke tabs match the 11 enumerated butlers

**Expected butlers with bespoke tabs:** chronicler, education, finance, general, health, home, lifestyle, messenger, qa, relationship, travel (switchboard SKIP)

**Verified via `butler-detail-tabs.ts` `getAllTabs`:**
- chronicler → timelines ✓
- education → reviews ✓ (pre-existing implementation, redesign blocked)
- finance → finances ✓
- health → health (measurements) ✓ (note: registered as "health" tab value, rendered as "Measurements" label in `ButlerDetailPage.tsx`)
- home → devices ✓
- lifestyle → taste ✓
- messenger → conversations ✓
- qa → investigations ✓
- relationship → contacts ✓
- travel → trips ✓
- general → NOT YET REGISTERED (bu-iuol4.30 blocked; `GENERAL_TABS` constant does not exist in `butler-detail-tabs.ts`)
- switchboard → no bespoke tab ✓ (only routing-log and registry)

**Status: PARTIAL** — 10 of 11 domain butlers have bespoke tabs registered. General is deferred.

---

## 4. Per-Butler Checklist

| Butler | Tab Status | Label | Implementing Bead | PR |
|---|---|---|---|---|
| chronicler | RESTYLED | Timelines | bu-iuol4.25 | #1547 |
| education | PARTIAL (pre-existing; redesign blocked) | Reviews | bu-iuol4.26 | #1582 (open) |
| finance | RESTYLED | Finances | bu-iuol4.29 | #1549 |
| general | DEFERRED | Collections | bu-iuol4.30 | — (blocked) |
| health | RESTYLED | Measurements | bu-iuol4.23 | #1551 |
| home | RESTYLED | Devices | bu-iuol4.32 | #1573 |
| lifestyle | NEW | Taste | bu-iuol4.33 | #1574 |
| messenger | NEW | Conversations | bu-iuol4.34 | #1579 |
| qa | NEW | Investigations | bu-iuol4.28 | #1552 |
| relationship | RESTYLED | Contacts | bu-iuol4.21 | #1550 |
| switchboard | SKIP | (operator-only: Routing Log + Registry) | — | — |
| travel | RESTYLED | Trips | bu-iuol4.36 | #1558 |

**Notes:**
- Education: `ButlerEducationReviewsTab.tsx` existed before this epic. bu-iuol4.26 was supposed to restyle it to the 4-col Panel grid. PR #1582 is open but not yet merged; the bead is marked blocked in the tracker. The legacy implementation remains live.
- General: `ButlerGeneralCollectionsTab` does not exist. `GENERAL_TABS` constant not defined in `butler-detail-tabs.ts`. The general butler currently has no bespoke tab. bu-iuol4.31 (backend stats endpoint) merged as PR #1577 but the frontend is blocked on bu-iuol4.30.
- Health: The tab value is `"health"` in the tabs registry (from the original HEALTH_TABS constant) but is displayed as "Measurements" in the tab trigger in ButlerDetailPage. The full rewrite (bu-iuol4.23) delivered a proper 4-col Panel grid replacing the 6-link directory.

---

## 5. OpenSpec Archive Confirmation

Current state of `openspec/changes/`:

```
openspec/changes/redesign-detail-resident-tabs-claude-design/   ← NOT ARCHIVED
openspec/changes/add-bespoke-butler-tab-capability/              ← NOT ARCHIVED
```

The `openspec/changes/archive/` directory contains entries dated up to `2026-05-10` (e.g., `2026-05-10-redesign-butlers-page-status-board`). Neither `redesign-detail-resident-tabs-claude-design` nor `add-bespoke-butler-tab-capability` has been moved to archive.

**Recommendation:** Both changes should be archived. However, archiving should be deferred until the two blocked beads (bu-iuol4.26 education Reviews redesign, bu-iuol4.30 general Collections) are resolved, since they are still being implemented against the spec in these changes. Archive on completion of those two beads.

Suggested archive names (when ready):
- `2026-05-11-redesign-detail-resident-tabs-claude-design`
- `2026-05-11-add-bespoke-butler-tab-capability`

---

## 6. Deferred Follow-ups

### Deferred beads from the epic (open/blocked)

| Bead | Title | Priority | Status | Notes |
|---|---|---|---|---|
| bu-iuol4.16 | Frontend: ButlerActivityTab redesign (replaces stub) | P2 | open | Core resident tab stub; no PR opened |
| bu-iuol4.19 | Frontend: ButlerSpendTab redesign (replaces stub) | P2 | open | Core resident tab stub; no PR opened |
| bu-iuol4.20 | Frontend: ButlerMemoryTab redesign to KPI quartet + recent writes | P2 | open | Core resident tab stub; no PR opened |
| bu-iuol4.26 | Frontend: redesign Education Reviews bespoke tab | P2 | blocked | PR #1582 open but not merged; backend endpoint bu-iuol4.27 merged |
| bu-iuol4.30 | Frontend: NEW General Collections bespoke tab | P2 | blocked | Backend bu-iuol4.31 merged; frontend implementation not started |
| bu-iuol4.6 | Backend: GET /api/butlers/{name}/analytics/latency-stats | P2 | blocked | Latency stats endpoint not delivered |
| bu-iuol4.8 | Backend: add last_session_started_at to ButlerSummary | P3 | open | ButlerSummary extension not delivered |
| bu-iuol4.11 | Backend: extend ModuleStatus with oauth/credential health | P3 | open | ModuleStatus extension not delivered |
| bu-iuol4.12 | Backend: verify ?butler= filter on /api/costs/summary and /api/memory/episodes | P3 | open | Filter verification not done |

### Discovered follow-ups from sibling beads

| Bead | Title | Priority | Notes |
|---|---|---|---|
| bu-8mtqt | Lock down mastery_pct key naming in analytics metrics schema | P3 | Discovered from bu-iuol4.26 (PR #1582) — key naming inconsistency in education analytics |
| bu-eapmh | Backend: GET /api/health/measurements/trend with hourly/daily aggregation | P3 | Discovered from bu-ak3yo (PR #1569) — sparkline trend data needs a dedicated endpoint |

---

## 7. Doctrine Deltas

The following patterns and conventions emerged during the epic that are candidates for ratification into project-level doctrine.

### 7.1 Two-tier responsive grid convention

**Pattern observed across 9 of the 10 implemented bespoke tabs:**

- Outer panel-grid shell: `grid grid-cols-1 lg:grid-cols-4 border-t border-l border-border/60`
- Inner KPI quartet row: `grid grid-cols-2 sm:grid-cols-4 gap-X`

**Proposed addition to `about/heart-and-soul/design-language.md`:**
> Resident-mode tab bodies use a two-tier responsive grid. The outer frame is `grid-cols-1 lg:grid-cols-4` with `border-top border-left` (the `--border` token). Each Panel child carries `border-right border-bottom`. The inner KPI quartet uses `grid-cols-2 sm:grid-cols-4` for a 2-wide mobile layout that expands to 4-wide at `sm` breakpoint.

### 7.2 The `toneClass()` atom for amber/destructive accents

**Pattern:** `atoms.tsx` exports `toneClass(tone: Tone)` mapping `"amber"` → `text-amber-500`, `"red"` → `text-destructive`, `"green"` → `text-emerald-500`, `"dim"` → `text-muted-foreground`, `"fg"` → `text-foreground`. Used in KpiCell and MonoLabel to express severity tone without raw Tailwind color strings.

**Proposed addition to design-language.md:**
> KPI cells and labels that express severity tone MUST use the `toneClass()` atom from `@/components/butler-detail/atoms`. Direct Tailwind color classes (`text-amber-500`, etc.) on KPI cells are permitted only inside `atoms.tsx` itself.

### 7.3 The `ErrorLine` / `EmptyStateLine` pattern for panel error states

**Pattern:** Bespoke tabs define private `ErrorLine` and `EmptyStateLine` function components that render centered, muted, sentence-case messages inside Panel bodies. Currently duplicated across ButlerRelationshipContactsTab, ButlerLifestyleTasteTab, ButlerHomeDevicesTab, ButlerMessengerConversationsTab.

**Proposed:** Promote `ErrorLine` and `EmptyStateLine` to shared exports in `atoms.tsx` to prevent further duplication. This would also make the pattern testable in one place.

### 7.4 Em-dash as null KPI placeholder vs. prohibited in prose

**Observed usage:**
- **Allowed:** `value="—"` on KpiCell components when the metric is unavailable (zero/null). This is a typed-primitive use case — the em-dash is a display value, not prose.
- **Prohibited:** Free text like "No reviews scheduled — keep learning and reviews will appear here." (`ButlerEducationReviewsTab.tsx:239`). This is an em-dash used as a prose connective.

The existing doctrine (Non-Negotiable 6 in `design-language.md`) already prohibits em-dashes in prose. The distinction needs to be made explicit:

**Proposed clarification in `design-language.md`:**
> The em-dash (`—`) is the canonical null placeholder for missing KPI values. It MUST be used as a bare string value passed to a typed primitive (e.g., `<KpiCell value="—" />`). It MUST NOT appear in visible prose, headings, labels, empty-state messages, or tooltip copy.

### 7.5 `general` butler bespoke tab deferred

The general butler's Collections tab (bu-iuol4.30) is blocked and the `GENERAL_TABS` constant is not yet in `butler-detail-tabs.ts`. The spec in `add-bespoke-butler-tab-capability` lists `general | Collections` as normative. When bu-iuol4.30 ships:
- Add `const GENERAL_TABS = ["collections"] as const;` to `butler-detail-tabs.ts`
- Wire it in `getAllTabs` with `if (butlerName === "general") { baseTabs.push(...GENERAL_TABS); }`
- Add lazy import and render in `ButlerDetailPage.tsx`

---

## 8. Quality Gates

```
uv run ruff check src/ tests/ roster/ conftest.py --output-format concise
# Result: All checks passed! (no code changes in this report bead)
```

No code was modified by this reconciliation report bead. The lint gate passes.
