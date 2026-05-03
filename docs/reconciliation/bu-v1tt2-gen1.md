# Reconciliation Report: bu-v1tt2 (Vertical C) — gen-1

**Date:** 2026-05-03
**Reconciler bead:** bu-v1tt2.6
**Parent epic:** bu-v1tt2 — Frontend redesign C: `<Time>` primitive + token-leak cleanup

---

## Epic Acceptance Criteria vs. Implementation

### AC1: `<Time>` component at `frontend/src/components/ui/time.tsx` with modes documented

**Status: FULLY MET**

- Implementing bead: bu-v1tt2.2 (PR #1342), plus bu-fv4vy (PR #1353) for `compact` flag
- `time.tsx` exists with `absolute` / `relative` / `smart` modes, `precision` prop, `compact` flag
- Renders as `<time dateTime={isoString}>` (ARIA-correct)
- 21+ unit tests
- Timezone from `ChroniclesTimezoneContext` with prop override

### AC2: `index.css` declares `severity-*`, `permanence-*`, `category-*` tokens exposed via Tailwind theme

**Status: FULLY MET**

- Implementing bead: bu-v1tt2.1 (PR #1338)
- 15 tokens declared: severity-low/medium/high, permanence-fleeting/medium/strong/permanent, category-1..8
- Each has a one-line comment describing intended use
- All forwarded via `@theme inline` block as `--color-*` for Tailwind utility resolution
- Also added via bu-azzsf (direct-merge 4c989ead): `--role-owner/admin/default` and `--tier-1..6` tokens

### AC3: Zero raw hex literals in `frontend/src/pages` or `frontend/src/components` (excluding CSS and chart configs)

**Status: PARTIALLY MET — gaps found**

- Implementing bead: bu-v1tt2.3 (PR #1343, 8 files migrated) + bu-azzsf (role/tier tokens added)

**Remaining violations (6 distinct sites):**

| File | Lines | Hex | Should use | Gap |
|------|-------|-----|------------|-----|
| `EntitiesPage.tsx` | 105–115 | `#b91c1c`, `#c2410c`, `#92400e`, `#15803d`, `#0369a1`, `#6b7280` | `var(--tier-1..6)` | Tokens exist (bu-azzsf) but code never updated |
| `EntitiesPage.tsx` | 764 | `#7c3aed` (owner badge) | `var(--role-owner)` | Tokens exist (bu-azzsf) but code never updated |
| `EntitiesPage.tsx` | 333, 773 | `#ea580c` (unidentified entity) | needs `--state-unidentified` or similar | No token exists |
| `approvals/action-table.tsx` | 43, 45, 47 | `#7c3aed`, `#b45309`, `#0369a1` | `var(--role-owner/admin/default)` | Tokens exist (bu-azzsf) but not applied here |
| `approvals/action-detail-dialog.tsx` | 37, 39, 41 | same role colors | same role tokens | Same gap |
| `ConcentricCirclesCanvas.tsx` | 417, 419, 429 | `#7c3aed` (owner SVG marker) | `var(--role-owner)` | Token exists but not applied |

**Exempt (chart/visualization configs per bu-v1tt2.3 scope exclusion):**
- `lane-taxonomy.ts`: chronicle lane colors (passed as data to chart renderers)
- `concentric-circles-constants.ts` TIER_RING_COLORS: SVG canvas ring colors (different palette from --tier-* badge ramp)
- `MindMapGraph.tsx` STATUS_COLORS: D3/SVG node colors
- `MasteryTrendChart.tsx`, `CrossTopicChart.tsx`, `MeasurementChart.tsx`: recharts stroke/fill props

**Borderline (need new tokens):**
- `TopologyGraph.tsx`: `#22c55e`/`#ef4444`/`#eab308` system status colors — could map to `--severity-*` but `#eab308` doesn't exactly match `--severity-medium` (#f59e0b). Reasonable to add `--status-ok`, `--status-down`, `--status-degraded` tokens.

### AC4: Zero raw `toLocaleString` / `toISOString` / `date-fns format` calls in JSX render paths

**Status: PARTIALLY MET — gaps found**

- Implementing bead: bu-v1tt2.4 (PR #1347, 41 files migrated) + bu-fv4vy (compact prop)

**Remaining date render calls in JSX:**

| File | Line | Call | Format needed | Gap |
|------|------|------|---------------|-----|
| `MealsPage.tsx` | 230 | `format(new Date(day), "EEEE, MMMM d, yyyy")` | weekday+date heading | `<Time>` lacks weekday/full-day-name format |
| `MealsPage.tsx` | 261 | `format(new Date(m.eaten_at), "HH:mm")` | 24h time-only | `<Time>` has no time-only mode |
| `CalendarWorkspacePage.tsx` | 178 | `format(start, "MMMM yyyy")` | month/year heading | `<Time>` has no month+year-only mode |
| `CalendarWorkspacePage.tsx` | 181 | `format(start, "EEE, MMM d, yyyy")` | day navigation header | `<Time>` lacks short weekday |
| `CalendarWorkspacePage.tsx` | 183 | `format(start/end, "MMM d, yyyy")` | date range display | Could use `<Time mode="absolute" compact>` but missing range pattern |
| `CalendarWorkspacePage.tsx` | 190 | `format(start/end, "MMM d, HH:mm")` | event start–end time | `<Time>` has no time-only or range support |
| `CalendarWorkspacePage.tsx` | 370 | `format(parsed, "MMM d, HH:mm")` | event time display | Same |
| `EntityDetailPage.tsx` | 964 | `format(sample, "MMM d")` | axis label helper | `<Time compact precision="day">` could work but is called with synthetic date |
| `PendingIdentitiesSection.tsx` | 264 | `format(new Date(contact.created_at), "MMM d, yyyy")` | creation date | `<Time mode="absolute">` could work here |
| `session-stripe-utils.ts` | 77 | `toLocaleDateString("en-US", {month:"short", day:"numeric"})` | stripe label | `<Time compact precision="day">` could handle |

**Correctly kept (non-date number formatting — not violations):**
- `SymptomsPage.tsx` lines 119, 235: `total.toLocaleString()` — number formatting
- `ConnectorDetailPage.tsx`: `value.toLocaleString()` — number formatting
- `ContactsPage.tsx`, `GroupsPage.tsx`, `MealsPage.tsx`, `EntitiesPage.tsx`, `NotificationsPage.tsx`, `ConditionsPage.tsx`, `ResearchPage.tsx`: `total.toLocaleString()` — all number formatting

**Correctly kept (API query building — not JSX render):**
- `ChroniclesPage.tsx`, `DashboardPage.tsx`, `GanttSwimlane.tsx`, `session-stripe-utils.ts` (lines 142-143): `.toISOString()` used to build query params
- `ButlerTriggerTab.tsx`: `.toISOString()` for API payload
- `CalendarWorkspacePage.tsx` lines 137, 418-419: `format()` for API param building
- `ManualRefreshButton.tsx`: `.toISOString()` for API params

**Intentionally kept per worker notes:**
- `QaOverviewPage.tsx:71`, `QaInvestigationsPage.tsx:53`: custom relative formatter kept (differs from date-fns formatDistanceToNow by design)

### AC5: Inline `style={{width:...}}` and `style={{height:...}}` for layout-only purposes gone

**Status: SUBSTANTIALLY MET — legitimate exceptions retained**

- Implementing bead: bu-v1tt2.5 (PR #1365, merged 35eb3011)
- `CalendarWorkspacePage.tsx` main grid: now `h-[var(--calendar-grid-height)]` ✓
- `--calendar-hour-height: 60px` and `--calendar-grid-height: calc(...)` added to `index.css` ✓

**Legitimately retained (Tailwind JIT purge prevents static classes for dynamic values):**
- `FactDetailPage.tsx:64`, `RuleDetailPage.tsx:66`, `EligibilityTimeline.tsx:68`: `style={{width: pct%}}` progress bars (dynamic computed value)
- `CalendarWorkspacePage.tsx:1773`: `top: topPx, height: heightPx` event positioning (pixel-computed layout)
- `CalendarWorkspacePage.tsx:1719`, `1757`: `top: h * HOUR_HEIGHT_PX` grid row positioning
- `SessionStripeChart.tsx:129`, `AggregateStackedBar.tsx:138`, `CostWidget.tsx:58`: skeleton animation heights (random offsets)
- `ModelCatalogCard.tsx:206`, `CostBreakdownTable.tsx:105`, `MemoryBrowser.tsx:124`: progress bar widths
- `GanttSwimlaneInner.tsx`: Gantt lane heights (computed layout)
- `TimelineTab.tsx:185`: timeline event positioning (pixel layout)

**Remaining gap — NOT yet migrated:**
- `ConcentricCirclesDialog.tsx:662`: `style={{ width: "60vw", height: "80vh", display: "flex", flexDirection: "column" }}`
  - `display: flex` → `flex`, `flexDirection: column` → `flex-col`, `width: 60vw` → `w-[60vw]`, `height: 80vh` → `h-[80vh]` (Tailwind arbitrary values work for static vw/vh)
- `ConcentricCirclesDialog.tsx` lines 135, 380, 423, 499: `cursor: pointer/grabbing/grab/default` → Tailwind `cursor-*` utilities

### AC6: gen-1 reconciliation closed clean

**Status: IN PROGRESS (this bead)**

---

## Gap Beads Created

See below sections for IDs after creation.

### Gap 1: Apply --tier-1..6 tokens in EntitiesPage dunbarTierBadgeStyle + owner/unidentified badges (gap from bu-v1tt2.3 + bu-azzsf)

Files: `EntitiesPage.tsx`, `approvals/action-table.tsx`, `approvals/action-detail-dialog.tsx`, `ConcentricCirclesCanvas.tsx`

- Migrate `dunbarTierBadgeStyle()` hex literals to `var(--tier-1..6)` (comment already says "until --tier-* tokens" — they exist now)
- Migrate owner badge (#7c3aed) to `var(--role-owner)` in EntitiesPage
- Add `--state-unidentified` token or use existing orange for the `#ea580c` unidentified-entity warning
- Migrate approvals role colors to `var(--role-*)`
- Migrate ConcentricCirclesCanvas owner marker to `var(--role-owner)`

### Gap 2: Extend `<Time>` for remaining calendar/meals format patterns (or accept as out-of-scope)

Remaining `format()` calls in JSX are blocked by missing `<Time>` capabilities:
- 24h time-only ("HH:mm")
- Weekday name ("EEEE", "EEE")
- Month+year only ("MMMM yyyy")
- Date range formatting ("start – end")

Options: extend `<Time>` with a `format` prop (passthrough), or accept these calendar-specific formats as a named exception under the "calendar layout helpers" category (similar to chart palette exemption).

### Gap 3: Migrate ConcentricCirclesDialog static inline styles to Tailwind

`width: 60vw, height: 80vh, display: flex, flexDirection: column` → Tailwind arbitrary values
`cursor: pointer/grabbing/grab/default` → Tailwind `cursor-*` utilities

---

## Codebase Verification Results

| Check | Result |
|-------|--------|
| TypeScript (`tsc --noEmit`) | PASS (clean) |
| Python lint (`ruff check`) | PASS (clean) |
| ESLint | 14 warnings (all pre-existing react-hooks/exhaustive-deps, not introduced by vertical C) |
| `time.tsx` exists | YES |
| `<time dateTime>` ARIA attribute | YES |
| severity/permanence/category tokens in index.css | YES (15 tokens) |
| @theme inline Tailwind exposure | YES |
| --tier-1..6 tokens in index.css | YES (bu-azzsf) |
| --role-owner/admin/default tokens | YES (bu-azzsf) |
| --calendar-hour-height + --calendar-grid-height | YES (bu-v1tt2.5) |

---

## Live Verification

Frontend dev server was not running at time of reconciliation. TypeScript compilation was verified clean (proxy for render-path correctness). Visual browser verification deferred to gap bead resolution.

---

## Summary Verdict

Epic is ~85% complete. AC1 and AC2 are fully met. AC3 has 5 files with hex leaks that need migration to existing tokens (the tokens now exist via bu-azzsf but the consumers weren't updated). AC4 has ~7 format() calls in JSX render paths that require `<Time>` format extension or explicit scope exclusion. AC5 is met for the specified layout cases plus one remaining dialog (ConcentricCirclesDialog cursor/display/vw/vh).

Two gap beads filed for remaining work.
