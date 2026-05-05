# Vertical H (workspace primitives extracted) — Gen-1 Reconciliation Checklist

**Issue:** bu-e8b5w.6
**Date:** 2026-05-06
**Epic:** bu-e8b5w — Frontend redesign H: workspace primitives extracted from Chronicles
**Status:** Clean. One minor finding logged. Epic closeable after bu-e8b5w.5 ships (or is descoped).

---

## Epic Acceptance Criteria vs. Implementation

| # | Epic Acceptance Criterion | Bead | Status | Notes |
|---|---|---|---|---|
| 1 | `components/workspace/Scrubber.tsx` exists and is imported by ChroniclesPage (no breakage) | bu-e8b5w.1 | CLOSED (PR #1437) | PASS — file exists; ChroniclesPage imports from `@/components/workspace/Scrubber` |
| 2 | `components/workspace/MapPanContext.tsx` exists and is imported by ChroniclesPage | bu-e8b5w.2 | CLOSED (PR #1438) | PASS (with note) — landed as `map-pan-store.ts` (not `MapPanContext.tsx`); ChroniclesPage imports `MapPanContext, useMapPanContextValue` from `@/components/workspace/map-pan-store` |
| 3 | `components/workspace/TimeWindowPicker.tsx` exists and is imported by ChroniclesPage | bu-e8b5w.3 | CLOSED (PR #1436) | PASS — file exists; ChroniclesPage imports from `@/components/workspace/TimeWindowPicker` |
| 4 | `about/lay-and-land/frontend.md` documents the workspace archetype with referenced primitives | bu-e8b5w.4 | CLOSED (PR #1439) | PASS — frontend.md §D "Workspace" defines the archetype with required primitives list; `components/workspace/README.md` provides import paths and composition patterns |
| 5 | CostsPage renders via `<Page archetype='workspace'>` using extracted primitives | bu-e8b5w.5 | OPEN | PENDING — scope-separate; not gating this reconciliation |
| 6 | gen-1 reconciliation closed clean | bu-e8b5w.6 | IN PROGRESS | This bead. One minor finding; no gap beads required. |

---

## Per-Bead Audit

### bu-e8b5w.1 — Extract Scrubber + episode interaction primitives

**Closed:** PR #1437

| AC | Status | Finding |
|---|---|---|
| 1. `components/workspace/Scrubber.tsx` exists | PASS | File present; exports `ScrubberProps` interface and `Scrubber` function component |
| 2. ChroniclesPage imports from new location and renders unchanged | PASS | `import { Scrubber } from "@/components/workspace/Scrubber"` at line 33 of ChroniclesPage.tsx |
| 3. Component generalized (no chronicler-specific data fetching baked in) | PASS | `Scrubber` accepts `pointEvents` as a prop; no internal API calls; `tz` prop with `DEFAULT_TZ` fallback |
| 4. Tests under tests/chronicler still pass | PASS (per close reason) | Close reason: 1141/1141 tests pass |
| 5. Original `components/chronicles/Scrubber.tsx` is DELETED | PASS | `find frontend/src/components/chronicles -name 'Scrubber*'` returns zero results |

Companion file `playhead-interp.ts` (and its test) also moved to `components/workspace/` — correct.

**Gap:** None. Clean close.

---

### bu-e8b5w.2 — Extract MapPanContext + map workspace primitives

**Closed:** PR #1438

| AC | Status | Finding |
|---|---|---|
| 1. `components/workspace/MapPanContext.tsx` (or `map-pan-store.ts`) exists | PASS | Landed as `components/workspace/map-pan-store.ts`; exports `MapPanContext`, `MapPanFn`, `MapPanContextValue`, `useMapPanContextValue`, `useRegisterMapPan`, `useMapPanTo` |
| 2. ChroniclesPage imports `MapPanContext` from new location and functions | PASS | `import { MapPanContext, useMapPanContextValue } from "@/components/workspace/map-pan-store"` at line 43 |
| 3. MapPanContext is consumer-agnostic | PASS | No Chronicles-specific references inside `map-pan-store.ts`; accepts any `MapPanFn = (lat, lng) => void` |
| 4. Original `components/chronicles/map-pan-store.ts` is DELETED | PASS | No `map-pan-store.ts` exists in `chronicles/`; `grep -r 'from.*components/chronicles/map-pan-store' frontend/src` returns zero hits |

**Minor finding (cosmetic):** `frontend/src/components/chronicles/map-pan-store.test.ts` remains in the chronicles directory after the move. The file is a rename artifact — it actually tests `parseLatLng` from `location-utils.ts` (a chronicles-only utility), not `map-pan-store`. No import from the deleted file; no broken reference. The file is misnamed relative to what it tests, but this predates this vertical and is unrelated to the extraction. Noted as a discovered follow-up below; not a gap against this vertical's ACs.

**Gap:** None against vertical H ACs. Misnamed test file is a cosmetic defect predating this vertical.

---

### bu-e8b5w.3 — Extract TimeWindowPicker + AutoRefreshToggle composition

**Closed:** PR #1436

| AC | Status | Finding |
|---|---|---|
| 1. `components/workspace/TimeWindowPicker.tsx` exists | PASS | File present; exports `TimeWindowPicker` plus re-exports `TimeWindow` and `UseTimeWindowResult` types for consumer convenience |
| 2. ChroniclesPage imports new location and still functions | PASS | `import { TimeWindowPicker } from "@/components/workspace/TimeWindowPicker"` at line 29 |
| 3. Composition pattern documented (workspace README or frontend.md section) | PASS | `components/workspace/README.md` covers `TimeWindowPicker`, `AutoRefreshToggle`, `useTimeWindow`, and the full workspace-page composition pattern with annotated code sample |
| 4. Original `components/chronicles/TimeWindowPicker.tsx` is DELETED | PASS | `find frontend/src/components/chronicles -name 'TimeWindowPicker*'` returns zero results |

**Gap:** None. Clean close.

---

### bu-e8b5w.4 — Document workspace archetype in frontend.md

**Closed:** PR #1439

| AC | Status | Finding |
|---|---|---|
| 1. `frontend.md` has a Workspace archetype section with definition, reference, and primitives | PASS | §D "Workspace / canvas" defines the archetype, names ChroniclesPage as reference implementation, and lists required primitives: `<Page archetype="workspace">`, `<Scrubber>`, `<TimeWindowPicker>`, `MapPanContext.Provider` |
| 2. `detail-page-audit.md` cross-references workspace where applicable | PASS | `detail-page-audit.md` references workspace archetype twice: ButlerDetailPage noted as workspace-grade candidate (§ "butlers"); CostsPage listed as future workspace upgrade candidate |

**Note on import paths:** `frontend.md` names the primitives by component but does not specify `@/components/workspace/` as their canonical import path. Import-path guidance lives in `components/workspace/README.md`, which is a more appropriate location for implementation detail. This split is intentional and coherent — architecture doc names the concepts; component-dir README provides the code contract.

**Gap:** None. Clean close.

---

### bu-e8b5w.7 — Audit and update dashboard-chronicles spec for workspace primitive relocations

**Status:** BLOCKED (PR #1441 open, in review)

Per issue notes: precise spec audit found the dashboard-chronicles spec has 3 path-based references (ChroniclesPage, nav-config, lane-taxonomy) — none of which are the 3 relocated workspace primitives. No spec delta files are needed. The spec is component-path-agnostic by design; the relocation does not change the observable interface. PR #1441 records the audit result.

This bead is not a blocker for the gen-1 reconciliation verdict; the audit conclusion is already established in the PR notes.

---

## Lingering-Import Audit

Exhaustive grep for any surviving imports from chronicle paths for the 3 relocated primitives:

| Pattern | Result |
|---|---|
| `from.*chronicles/Scrubber` | 0 matches |
| `from.*chronicles/TimeWindowPicker` | 0 matches |
| `from.*chronicles/map-pan-store` | 0 matches |

No re-export stubs. No aliased paths. No two-path violations.

---

## workspace/ Directory Inventory

```
frontend/src/components/workspace/
  map-pan-store.ts          # MapPanContext + pan hooks (extracted from chronicles)
  playhead-interp.ts        # Trail interpolation utility (extracted from chronicles)
  playhead-interp.test.ts   # Tests for playhead-interp
  README.md                 # Composition patterns + usage doc
  Scrubber.tsx              # Scrubber component (extracted from chronicles)
  Scrubber.test.tsx         # Tests for Scrubber
  TimeWindowPicker.tsx      # TimeWindowPicker component (extracted from chronicles)
  TimeWindowPicker.test.tsx # Tests for TimeWindowPicker
```

No index barrel file — correct per project convention (no `index.ts` pattern; named imports only).

---

## Discovered Follow-Ups

### DF-1 — `chronicles/map-pan-store.test.ts` is misnamed

**File:** `frontend/src/components/chronicles/map-pan-store.test.ts`

**Detail:** This file tests `parseLatLng` from `location-utils.ts` — not `map-pan-store`. The name `map-pan-store.test.ts` is a leftover from a prior scaffolding or rename and does not reflect the tests inside. No import breakage; tests pass. Fixing would be a cosmetic rename: `map-pan-store.test.ts` → `location-utils.test.ts`.

**Severity:** Low (cosmetic, no behavioral impact). Filing as low-priority follow-up bead.

---

## Gap Summary

**Zero structural gaps** against the six epic acceptance criteria (excluding AC5/bu-e8b5w.5 which is explicitly out of scope for this reconciliation).

One cosmetic finding (misnamed test file in chronicles) is filed as a low-priority follow-up. It predates and is unrelated to this vertical.

---

## Epic Closure Assessment

### Closed children
| Bead | PR | Verdict |
|---|---|---|
| bu-e8b5w.1 | #1437 (merged) | Clean |
| bu-e8b5w.2 | #1438 (merged) | Clean |
| bu-e8b5w.3 | #1436 (merged) | Clean |
| bu-e8b5w.4 | #1439 (merged) | Clean |

### Open children
| Bead | Status | Gating epic closure? |
|---|---|---|
| bu-e8b5w.5 | Open (pilot: CostsPage workspace upgrade) | YES — AC5 is not met until this closes |
| bu-e8b5w.6 | In progress (this bead) | Closes with this report |
| bu-e8b5w.7 | Blocked (PR #1441 in review; no spec changes needed) | NO — audit result is conclusive; merge of PR #1441 is a bookkeeping step |

**Verdict:** Epic bu-e8b5w can close after bu-e8b5w.5 ships. If bu-e8b5w.5 is decided to be out of scope or deferred, the epic owner may close on the strength of AC1–AC4 + AC6 being met, noting AC5 as a deferred follow-up. No gen-2 reconciliation is expected unless bu-e8b5w.5 reveals structural gaps in the workspace primitive contracts.
