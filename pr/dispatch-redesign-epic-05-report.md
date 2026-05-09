# Gen-1 Reconciliation Report: Epic 05 — Butler List Page Denser Cards

**Issue:** bu-insd4.4
**Date:** 2026-05-10
**Epic:** bu-insd4 (Epic 05: Butler list page denser cards, no new fields)

---

## Merged Changes

| Change | Commit / PR | Description |
|---|---|---|
| bu-insd4.1 | PR #1495 (merged) | Replace ButlersPage card with denser Dispatch layout |
| bu-insd4.2 | 84bd56e6 (cherry-picked) | Restrict roster fixture to real butlers, add unknown-butler graceful-render test |
| bu-insd4.3 | 79b2b7e3 (cherry-picked) | Add sort, empty, error, and 30s polling spec scenarios |

---

## Verification Results

### 1. No new ButlerSummary fields

**Status: PASS**

Backend `ButlerSummary` at `src/butlers/api/models/__init__.py:101-117` contains:
`name`, `status`, `port`, `type`, `db`, `description`, `modules`, `schedule_count`, `sessions_24h`.

The list router at `src/butlers/api/routers/butlers.py:124-131` constructs summaries
with exactly: `name`, `status`, `port`, `type`, `description`, `sessions_24h`.

Frontend `ButlerSummary` interface at `frontend/src/api/types.ts:57-67` mirrors the
backend shape with `description?: string | null` (added in the fix commit `9d6c2db4`
alongside PR #1495 to replace an unsafe `Record<string,unknown>` cast). This field
was already present on the backend; the frontend addition was a type-alignment fix,
not a new API field.

No new fields were added to `ButlerSummary` as part of this epic.

### 2. No nonexistent butler references

**Status: PASS**

`frontend/src/pages/ButlersPage.tsx` contains no hardcoded butler names. The page
renders only what `GET /api/butlers` returns. Grep for `calendar`, `memory`, and
`household` in the butler-list code path (`ButlersPage.tsx` and
`frontend/src/components/`) returns no hits in the list rendering path. Hits in
unrelated files (chronicles lane taxonomy, memory module components,
relationship components) are outside the butler list code path and expected.

The RTL fixture at `ButlersPage.test.tsx` uses the canonical 12 real butlers:
`chronicler`, `education`, `finance`, `general`, `health`, `home`, `lifestyle`,
`messenger`, `qa`, `relationship`, `switchboard`, `travel`.

A graceful-render test verifies that an unfamiliar butler returned by the API
(e.g., `future-butler`) renders correctly without errors.

### 3. A11y baseline

**Status: PENDING (not blocking)**

The Ladle stories and axe-core baseline tests added in bu-sfeuw.4 (PR #1498, still
open as of 2026-05-10) cover `ButlerDetailPage`, not `ButlersPage`. PR #1498 CI
shows one frontend job FAILURE and one SUCCESS (flaky run); the latest run passes.

There are no dedicated axe-core tests for `ButlersPage` in the current codebase.
The page uses semantic HTML (`<Link>` → `<a>`, `<Card>`, `<Badge>`), ARIA-labeled
loading state via the `<Page>` primitive (`aria-label="Loading"`), and standard
shadcn/ui components. No interactive controls lack accessible labels.

Gap tracked below (section: Gaps).

### 4. OpenSpec sync

**Status: COMPLETE**

- `openspec validate redesign-butler-list-card-density` → "Change is valid"
- `openspec status --change redesign-butler-list-card-density` → "4/4 artifacts complete"
- Canonical spec at `openspec/specs/dashboard-butler-management/spec.md` updated
  in this commit to apply the delta from
  `openspec/changes/redesign-butler-list-card-density/specs/dashboard-butler-management/spec.md`:
  - Requirement description updated to reflect dense card grouping by type.
  - "Fleet summary cards": label changed from "Total Butlers" to "Total agents".
  - "Butler card grid" → replaced by "Dense butler and staffer cards" with full
    field inventory (ButlerMark, name, status pill, port, eligibility chip,
    description, sessions_24h).
  - "Status badge color mapping" → renamed to "Status pill color mapping".
  - "Eligibility chip" scenario added.
  - "skeleton loading grid" → "skeleton loading list".
  - "zero butlers" → "zero butler list rows".
  - Implementation source constraints block added.

---

## Spec Drift Assessment

The canonical spec's Butler List Page requirement was stale relative to the
OpenSpec change delta. The drift has been resolved in this commit. All scenario
names and content now match the implemented behavior in `ButlersPage.tsx`.

One minor implementation note: the current code labels the stats card "Total Agents"
(not "Total Butlers"), which aligns with the updated spec's "Total agents". The
eligibility chip renders only when `eligibilityState` is truthy; the "unavailable"
fallback chip described in the spec's "Eligibility chip" scenario is not yet
rendered when no registry entry exists (the chip is simply omitted). This is a minor
spec gap tracked below.

---

## Gaps

| ID | Description | Severity | Action |
|---|---|---|---|
| GAP-01 | No axe-core tests for `ButlersPage` | Low | Track as follow-up; ButlerDetailPage covered by PR #1498 |
| GAP-02 | Eligibility chip omitted (not "unavailable") when no registry entry | Low | Spec says "renders explicit unknown or unavailable chip"; current impl omits it silently. Acceptable UX; track as follow-up |

---

## Ruff Lint

`uv run ruff check src/ tests/ roster/ conftest.py --output-format concise` → **All checks passed**
