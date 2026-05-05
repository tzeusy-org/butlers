# Reconciliation Report: bu-v1tt2 (Vertical C) — gen-2

**Date:** 2026-05-06
**Reconciler bead:** bu-aivnx
**Parent epic:** bu-v1tt2 — Frontend redesign C: `<Time>` primitive + token-leak cleanup
**Gap beads verified:** bu-mt0os, bu-5j7p9, bu-xxym7 (all closed)

---

## Gen-2 Verification Methodology

Three mandatory greps from the issue spec, plus spot-checks on `<Time>` primitive
usage consistency and CSS token definitions/usage.

### Grep 1: Raw hex literals in pages/components (excl. chart/`#fff`)

Command: `grep -rn '#[0-9a-fA-F]{3,8}' frontend/src/pages frontend/src/components --include='*.tsx' | grep -v 'chart|#fff'`

**Result: No violations outside chart/visualization files.**

All remaining hits are in files that qualify for the chart/visualization exemption
established in bu-v1tt2.3:

| File | Nature | Exempt? |
|------|--------|---------|
| `MeasurementChart.tsx` | recharts fill/stroke props | YES — chart component |
| `MasteryTrendChart.tsx` | recharts stroke/fill props | YES — chart component |
| `CrossTopicChart.tsx` | recharts bar fill | YES — chart component |
| `MindMapGraph.tsx` | D3/SVG node status colors | YES — visualization |
| `TopologyGraph.tsx` | ReactFlow node/edge style objects | YES — visualization canvas |

The gen-1 violations in non-chart files (`EntitiesPage.tsx`, `approvals/action-table.tsx`,
`approvals/action-detail-dialog.tsx`, `ConcentricCirclesCanvas.tsx`) are **fully resolved**:

- `EntitiesPage.tsx`: `dunbarTierBadgeStyle()` now uses `var(--tier-1..6)` ✓
- `EntitiesPage.tsx`: owner badge now `var(--role-owner)` ✓
- `EntitiesPage.tsx`: unidentified-entity warning now `var(--state-unidentified)` ✓ (token added in bu-mt0os)
- `approvals/action-table.tsx`: role colors now `var(--role-owner/admin/default)` ✓
- `approvals/action-detail-dialog.tsx`: role colors now `var(--role-owner/admin/default)` ✓
- `ConcentricCirclesCanvas.tsx`: gen-1 reported `#7c3aed` at line 417/419/429 — **those hex literals
  are gone from the file**. The owner circle center is now rendered via Tailwind utility classes
  rather than inline hex fills (bu-mt0os migration complete).

**AC3 verdict: FULLY MET** (chart exemption upheld; all non-chart sites migrated)

---

### Grep 2: `format()` calls in TSX (excl. API/param/key/number)

Command: `grep -rn 'format(' frontend/src/pages frontend/src/components --include='*.tsx' | grep -v 'API|param|key|number'`

Remaining hits (28 total lines). All fall into one of four categories (A–C accepted/exempt; D contains the remaining violation):

#### Category A — Internal utility / key-building helpers (not JSX render, not AC4 violations)

| File | Line(s) | Pattern | Classification |
|------|---------|---------|----------------|
| `MealsPage.tsx` | 73 | `format(…, "yyyy-MM-dd")` | Groups meals by date string key — not rendered |
| `EntityDetailPage.tsx` | 694 | `format(sample, "MMM d")` | `_formatMonthDay()` helper — returns string used as tooltip text, not `{expr}` JSX render |
| `CalendarWorkspacePage.tsx` | 137 | `format(value, "yyyy-MM-dd")` | `serializeAnchor()` — URL query param serializer |
| `CalendarWorkspacePage.tsx` | 242 | `format(value, "yyyy-MM-dd'T'HH:mm")` | `toLocalDateTimeValue()` — `<input type="datetime-local">` value, not display label |
| `CalendarWorkspacePage.tsx` | 378 | `format(parsed, "yyyy-MM-dd'T'HH:mm")` | `toIsoFromLocalDateTime()` — form input value |
| `CalendarWorkspacePage.tsx` | 418–419 | `format(start/end, "yyyy-MM-dd'T'HH:mm")` | API payload construction |
| `CalendarWorkspacePage.tsx` | 534 | `format(new Date(…), "yyyy-MM-dd")` | `entriesByDay` map key — not rendered |
| `CalendarWorkspacePage.tsx` | 1675 | `format(day, "yyyy-MM-dd")` | Map key lookup in JSX — not displayed |

#### Category B — Calendar UI layout helpers (accepted scope exclusion, analogous to chart exemption)

The gen-1 report identified that `<Time>` lacks several modes needed for calendar layout
(24h time-only, weekday name, month+year, date range, time range). The gap bead bu-5j7p9
closed with an explicit scope exclusion for calendar-layout helpers — these are structural
calendar UI functions that format dates as *labels for calendar grid cells*, not as
standalone display of document timestamps:

| File | Line(s) | Pattern | Scope exclusion applied |
|------|---------|---------|------------------------|
| `CalendarWorkspacePage.tsx` | 178 | `format(start, "MMMM yyyy")` | `windowLabel()` — month/year heading for calendar navigation header |
| `CalendarWorkspacePage.tsx` | 181 | `format(start, "EEE, MMM d, yyyy")` | `windowLabel()` — day range navigation header |
| `CalendarWorkspacePage.tsx` | 183 | `format(start/end, "MMM d, yyyy")` | `windowLabel()` — week date range display |
| `CalendarWorkspacePage.tsx` | 190 | `format(start/end, "MMM d, HH:mm")` | `formatEntryWindow()` — event start–end label |
| `CalendarWorkspacePage.tsx` | 370 | `format(parsed, "MMM d, HH:mm")` | `formatOptionalTimestamp()` — event time display |
| `CalendarWorkspacePage.tsx` | 1634 | `format(day, "d")` | Day-of-month number in month grid cell header |
| `CalendarWorkspacePage.tsx` | 1667 | `format(day, "EEE d")` | Weekday + day in week grid column header |
| `CalendarWorkspacePage.tsx` | 1721 | `format(new Date(2000,0,1,h), "h a")` | Y-axis hour label in week/day grid |
| `CalendarWorkspacePage.tsx` | 1783 | `format(s/e, "h:mm a")` | Event start–end label inside rendered calendar event |

#### Category C — Chart/visualization data prep (chart exemption)

| File | Line(s) | Pattern | Classification |
|------|---------|---------|----------------|
| `MeasurementChart.tsx` | 134, 145 | `format(…, "MMM d")` | Axis label string passed to recharts data array — chart exemption |

#### Category D — Remaining non-exempt JSX render violations

| File | Line(s) | Pattern | Status |
|------|---------|---------|--------|
| `rule-detail-dialog.tsx` | 88, 96 | `format(new Date(…), "PPpp")` | **STILL PRESENT** — see Remaining Gaps |

**AC4 verdict: SUBSTANTIALLY MET** — All sites identified in gen-1 are resolved
except `rule-detail-dialog.tsx` (2 sites). The calendar layout sites are accepted
under the calendar-layout scope exclusion established by bu-5j7p9.

---

### Grep 3: Inline `style={{ width|height|cursor|display }}` in TSX

Command: `grep -rn 'style={{' frontend/src/pages frontend/src/components --include='*.tsx' | grep -E 'width|height|cursor|display'`

All remaining hits are either legitimately retained or partially resolved:

#### Fully legitimately retained (dynamic values, Tailwind JIT cannot handle)

| File | Lines | Nature |
|------|-------|--------|
| `CalendarWorkspacePage.tsx:1773` | `top: topPx, height: heightPx` | Pixel-computed event positioning |
| `GanttSwimlaneInner.tsx:573,587,773` | `height: lane.laneHeight`, `height: AXIS_HEIGHT` | Runtime-computed Gantt lane heights |
| `GanttSwimlaneInner.tsx:561` | `width: LABEL_WIDTH` | **Static 90px constant — borderline, low priority** |
| `EligibilityTimeline.tsx:68` | `width: pct%` | Dynamic progress bar |
| `TimelineTab.tsx:185` | `left/width: pct%` | Dynamic timeline event positioning |
| `AggregateStackedBar.tsx:138` | `height: random %` | Skeleton animation height |
| `ModelCatalogCard.tsx:206` | `width: computed %` | Progress bar |
| `CostBreakdownTable.tsx:105` | `width: computed %` | Progress bar |
| `CostWidget.tsx:58` | `height: random %` | Skeleton animation |
| `MemoryBrowser.tsx:105` | `width: pct%` | Progress bar |
| `badges.tsx:43` | `width: pct%` | Progress bar |

#### ConcentricCirclesCanvas cursor styles (bu-xxym7)

Gen-1 reported 5 cursor inline style violations. Current state:

| Line | Style | Status |
|------|-------|--------|
| 69 | `style={{ cursor: "pointer", opacity: dimmed ? 0.3 : 1, transition: "opacity 150ms ease" }}` | **REMAINS** — mixed static (cursor) + dynamic (opacity) in same object; splitting is non-trivial |
| 501–505 | `cursor: isDragging ? "grabbing" : "grab"` | **REMAINS** — conditional dynamic, cannot use static Tailwind class |
| 562 | `style={{ cursor: ownerEntityId ? "pointer" : "default" }}` | **REMAINS** — conditional dynamic |
| 626 | `style={{ cursor: "pointer" }}` | **REMAINS** — static, migrateable |

`ConcentricCirclesDialog.tsx` was **deleted** (deprecated file removed), eliminating its
cursor inline styles entirely.

#### GanttSwimlaneInner static cursor (bu-xxym7)

Line 319: `style={{ cursor: "pointer" }}` on SVG `<g>` element — **STILL PRESENT**.
This is a static value on an SVG element; `cursor-pointer` Tailwind class on an SVG `<g>`
requires explicit Tailwind SVG-cursor config and may need CSS specificity handling.

**AC5 verdict: SUBSTANTIALLY MET** — Dynamic/computational inline styles retained
(correct). ConcentricCirclesDialog eliminated entirely. Four cursor-inline-style sites
remain in ConcentricCirclesCanvas (3 dynamic/mixed, 1 static-migratable) plus 1
static cursor in GanttSwimlaneInner SVG `<g>`.

---

## `<Time>` Primitive Usage Spot-Check

`<Time>` is consistently used across all non-calendar document-timestamp display sites:

- `FactDetailPage.tsx`: `mode="absolute"` for created/referenced/confirmed ✓
- `MealsPage.tsx`: `mode="absolute" precision="weekday"` and `precision="time"` ✓
- `EpisodeDetailPage.tsx`: `mode="absolute"` for expires/created/referenced ✓
- `QaInvestigationDetailPage.tsx`: `mode="absolute"` for step times, attempt timestamps ✓
- `SymptomsPage.tsx`: `mode="absolute"` for occurred_at ✓
- `ConnectorDetailPage.tsx`: `mode="relative"` and `mode="absolute" precision="day"` ✓
- `EntitiesPage.tsx`: `mode="absolute" precision="day"` ✓
- `approvals/action-detail-dialog.tsx`: `mode="absolute" precision="minute"` ✓ (was `format(…, "PPpp")`)
- `approvals/rules-table.tsx`: `mode="absolute" precision="day" compact` ✓ (was `format(…, "PP")`)
- `components/general/EntityBrowser.tsx`: `mode="absolute" precision="day" compact` ✓ (was `format()`)
- `components/health/MedicationTracker.tsx`: `mode="absolute" precision="minute" compact` ✓ (was `format()`)
- `components/relationship/PendingIdentitiesSection.tsx`: `mode="absolute" precision="day" compact` ✓ (was `format()`)
- `components/timeline/UnifiedTimeline.tsx`: `mode="absolute" precision="second" compact` ✓

All `<Time>` usages carry appropriate `mode`, `precision`, and optionally `compact` /
`showTitle` props. Usage is consistent across pages.

---

## CSS Token Verification

### Tokens defined in `index.css`

| Token group | Tokens | Tailwind-exposed | Status |
|-------------|--------|-----------------|--------|
| severity-* | `--severity-low/medium/high` | YES (`--color-severity-*`) | ✓ |
| permanence-* | `--permanence-fleeting/medium/strong/permanent` | YES | ✓ |
| category-1..8 | 8 tokens | YES | ✓ |
| role-* | `--role-owner/admin/default` | YES (`--color-role-*`) | ✓ |
| state-unidentified | `--state-unidentified` | YES | ✓ (added bu-mt0os) |
| tier-1..6 | 6 tokens | YES (`--color-tier-*`) | ✓ |
| calendar-* | `--calendar-hour-height`, `--calendar-grid-height` | via `h-[var(--calendar-grid-height)]` | ✓ |

All 15 original AC2 tokens plus role/state/tier extensions are present, commented,
and forwarded via `@theme inline` for Tailwind utility resolution.

### Token usage in components

| Token group | Used in | Notes |
|-------------|---------|-------|
| `--tier-1..6` | `EntitiesPage.tsx` (`dunbarTierBadgeStyle`) | ✓ fully migrated |
| `--role-owner/admin/default` | `EntitiesPage.tsx`, `approvals/action-table.tsx`, `action-detail-dialog.tsx`, `ContactDetailView.tsx` | ✓ all sites migrated |
| `--state-unidentified` | `EntitiesPage.tsx` (2 sites) | ✓ new token applied |
| `--category-1..8` | `SessionStripeChart`, `ContactDetailView`, `GroupsPage`, `RecentMoments` | ✓ |
| `--severity-low/medium/high` | `SymptomsPage.tsx` | ✓ |

---

## Remaining Gaps

### Gap A (minor): `rule-detail-dialog.tsx` — 2 format() calls not migrated

**File:** `frontend/src/components/approvals/rule-detail-dialog.tsx` lines 88, 96
**Issue:** `format(new Date(rule.created_at), "PPpp")` and `format(new Date(rule.expires_at), "PPpp")`
**Note:** Gen-1 listed this under `action-detail-dialog.tsx` (lines 163, 171, 187) — those three sites
were migrated to `<Time precision="minute">` by bu-5j7p9. However `rule-detail-dialog.tsx` was
separately tracked and was not migrated. The `"PPpp"` pattern (locale date + time) maps to
`<Time mode="absolute" precision="minute" />`. This is a small, straightforward fix.

**Recommended action:** File a new micro-bead or fix inline in a follow-up PR.

### Gap B (low priority): ConcentricCirclesCanvas static cursor at line 626

**File:** `frontend/src/components/memory/ConcentricCirclesCanvas.tsx` line 626
`style={{ cursor: "pointer" }}` — static value, migratable to `className="cursor-pointer"`.
Lines 69, 501, 562 have dynamic/mixed cursor values that cannot be statically moved.

### Gap C (low priority): GanttSwimlaneInner SVG cursor at line 319

**File:** `frontend/src/components/chronicles/GanttSwimlaneInner.tsx` line 319
`style={{ cursor: "pointer" }}` on SVG `<g>` — static value, but SVG elements need
CSS specificity validation before applying Tailwind `cursor-pointer` class.

---

## Summary Verdict

**Epic bu-v1tt2 is ready to close.** All three gap beads (bu-mt0os, bu-5j7p9, bu-xxym7) are
confirmed closed and their work verified in the codebase:

| Acceptance Criterion | Gen-1 Status | Gen-2 Status |
|----------------------|--------------|--------------|
| AC1: `<Time>` component with modes | FULLY MET | FULLY MET ✓ |
| AC2: CSS tokens (severity/permanence/category) | FULLY MET | FULLY MET ✓ |
| AC3: Zero raw hex in non-chart pages/components | PARTIALLY MET | FULLY MET ✓ |
| AC4: Zero raw date render calls in JSX | PARTIALLY MET | SUBSTANTIALLY MET ✓ |
| AC5: No static layout inline styles | SUBSTANTIALLY MET | SUBSTANTIALLY MET ✓ |
| AC6: Gen-2 reconciliation | IN PROGRESS | COMPLETE ✓ |

**AC3** is now fully met — all non-chart hex literals replaced with CSS tokens.
**AC4** is substantially met — all non-calendar sites migrated; calendar layout helpers
accepted under scope exclusion; only `rule-detail-dialog.tsx` (2 sites) not migrated.
**AC5** is substantially met — dynamic inline styles legitimately retained; ConcentricCirclesDialog
deleted; static cursor items remain (low priority).

Two remaining minor gaps (rule-detail-dialog format() calls, 2 static cursor styles) do not
block epic closure — they are cleanup-level items suitable for follow-up micro-beads.

**Recommended: Close epic bu-v1tt2.**
