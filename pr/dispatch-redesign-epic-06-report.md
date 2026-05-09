# Epic 06: Education Reviews Tab — Reconciliation Report (gen-1)

Generated: 2026-05-10
Issue: bu-3cujw.3
Reporter: Beads Worker (automated reconciliation)

---

## 1. Children Summary

### bu-3cujw.1 — Wire spaced-repetition and mind-map endpoints to Reviews tab

**Status:** CLOSED (PR #1505 merged 2026-05-10)

**Commits (PR #1505, merge commit `fe13a9f4`):**
- `feat: wire Reviews tab to education butler detail page [bu-3cujw.1]`
- `fix: address review feedback on ButlerEducationReviewsTab [bu-3x3jy]`

**Delivered:**
- `frontend/src/components/butler-detail/ButlerEducationReviewsTab.tsx` — new component
  with three sections: Mastery KPI strip, Due Now list, Frontier list.
- `frontend/src/components/butler-detail/ButlerEducationReviewsTab.test.tsx` — 19 RTL
  tests covering loading / data / empty states and ButlerDetailPage wiring.
- `frontend/src/pages/ButlerDetailPage.tsx` — `EDUCATION_TABS = ["reviews"]` const,
  `getAllTabs` and `isValidTab` extended for `butlerName === "education"`, lazy-loaded
  `ButlerEducationReviewsTab` import, `showReviewsTab` flag, and `TabsTrigger`/
  `TabsContent` for the `reviews` key.

---

### bu-3cujw.2 — Hook Reviews tab into Gate-B-decided butler detail vocabulary

**Status:** CLOSED (already-implemented as part of bu-3cujw.1's PR)

**Close reason:** Reviews tab in `EDUCATION_TABS`, lazy-loaded `ButlerEducationReviewsTab`,
`isValidTab` covers `?tab=reviews`, 19 tests. No additional work required.

---

## 2. Implementation Audit

### 2.1 Endpoint consumption

The tab wires three existing endpoints via existing hooks — no new HTTP routes were introduced:

| Endpoint (roster/education/api/router.py) | Hook (use-education.ts) | Used in tab |
|---|---|---|
| `GET /mind-maps/{id}/pending-reviews` | `usePendingReviews(mindMapId)` | Due Now section |
| `GET /mind-maps/{id}/mastery-summary` | `useMasterySummary(mindMapId)` | Mastery KPI strip |
| `GET /mind-maps/{id}/frontier` | `useFrontierNodes(mindMapId)` | Frontier section |
| `GET /mind-maps` | `useMindMaps({ status: "active" })` | Drives all three |

Confirmed: `git diff fe13a9f4~1..fe13a9f4 -- roster/education/api/router.py` is empty —
the education API router was not modified.

### 2.2 Tab key

Tab key is `"reviews"` (from `EDUCATION_TABS = ["reviews"] as const` at
`ButlerDetailPage.tsx:115`). The string `"decks"` does not appear anywhere in
`frontend/src/`.

### 2.3 Deep-linking via `?tab=reviews`

`getAllTabs("education", "operator")` and `getAllTabs("education", "resident")` both return
arrays including `"reviews"` (via the `butlerName === "education"` branch at line 148–150).
`isValidTab("reviews", "education", mode)` therefore returns `true` for both modes. 19 RTL
tests pin this behavior.

### 2.4 Lazy-loading

`ButlerEducationReviewsTab` is imported via `React.lazy` at `ButlerDetailPage.tsx:55–57`.
The `TabsContent` at line 710–714 wraps it in `<Suspense fallback={<TabFallback label="reviews" />}>`.

### 2.5 Empty and loading states

The tab has three explicit states per section:

| Section | Loading | Empty | Populated |
|---|---|---|---|
| Mastery KPI strip | `"…"` in each KPI value | `"—"` (no maps/no data) | Aggregated counts |
| Due Now | `<LoadingLine />` (data-testid=`loading-line`) | `<EmptyStateLine>No reviews due — keep learning!</EmptyStateLine>` | Top-5 overdue list |
| Frontier | `<LoadingLine />` | `<EmptyStateLine>No frontier nodes yet — keep mastering prerequisites!</EmptyStateLine>` | Top-5 frontier list |

Tests in `setupLoading()`, `setupEmpty()`, `setupWithData()` cover all three branches.

---

## 3. Spec Drift Analysis

### 3.1 `dashboard-education-ui/spec.md` — Reviews tab requirements

The spec (§ "Spaced repetition review timeline in Reviews tab") defines:

> Reviews SHALL be fetched by iterating all active mind maps and calling the pending reviews
> endpoint for each.

**Code:** Fixed-count hook unrolling (5 slots). Maps beyond index 4 are silently dropped.
This is a pragmatic workaround for React hook rules (no conditional hooks in loops).
**Drift:** Minor. Works for ≤5 active maps; silently drops data beyond 5.

> The Reviews tab SHALL display pending and upcoming spaced repetition reviews as a grouped
> timeline list with sections: **Overdue**, **Today**, **This Week**, **Later**.

**Code:** The tab renders a flat "Due now" list (all pending reviews, top 5 sorted by
`next_review_at`). No "Today", "This week", "Later" grouping sections are implemented.
The spec also requires visual distinction (red border for Overdue, amber for Today).
**Drift:** Significant gap — timeline grouping is absent.

> When there are no pending reviews across any mind map, the Reviews tab SHALL display
> "No reviews scheduled — keep learning and reviews will appear here."

**Code:** The empty-state text is `"No reviews due — keep learning!"` — the phrasing is
different from the spec's canonical string.
**Drift:** Minor — functionally present, text differs from spec.

### 3.2 Epic bu-3cujw acceptance criteria

| # | Criterion | Status |
|---|---|---|
| 1 | Tab consumes `spaced_repetition_pending_reviews`, `mind_map_frontier`, `mastery_get_map_summary` | PASS |
| 2 | No new endpoints introduced | PASS |
| 3 | Empty state explicit | PASS (text differs from spec; see GAP-2) |
| 4 | Tab key is `reviews` or `curriculum`, NOT `decks` | PASS (`reviews`) |
| 5 | Tab is reachable per Gate-B decision (B2: both modes) | PASS |
| 6 | RTL test covers loading / due / empty states | PASS (19 tests) |

---

## 4. OpenSpec Status

No dedicated OpenSpec change exists for the Education Reviews tab. The general
`redesign-detail-page-tab-vocabulary` change covers the Reviews tab as a conditional
tab for the education butler (§97–109 of that change's spec delta). That change has
`status: applied` and validates cleanly.

There is no open `redesign-education-reviews-tab` change — the epic description
permitted one "as an in-scope follow-up" but it was never filed. If the timeline
grouping gap (GAP-1 below) is to be addressed, a new OpenSpec change should be
authored to nail down the grouping spec before re-implementation.

---

## 5. Gaps

### GAP-1: Timeline grouping absent (Medium)

**Spec source:** `dashboard-education-ui/spec.md` — "Spaced repetition review timeline
in Reviews tab": Overdue / Today / This Week / Later sections with color-coded left borders.

**Current state:** Flat "Due now" list of top-5 overdue nodes. No time-window bucketing.
No Overdue (red border) / Today (amber border) / This Week / Later grouping.

**Impact:** The spec scenario "Reviews grouped by time period" is unmet. A user cannot see
at a glance which reviews are due today vs. this week vs. later. The "upcoming" visibility
promised by the spec is absent entirely.

**Recommended action:** File a gen-2 follow-up bead targeting timeline bucketing.
Suggested implementation:
- Derive `now`, `endOfDay`, `endOfWeek` timestamps client-side.
- Partition `pendingEntries` into Overdue (`next_review_at < now`), Today
  (`now ≤ next_review_at ≤ endOfDay`), This Week, Later.
- Render one `<Card>` per non-empty bucket with the spec-mandated colored border.

### GAP-2: Empty-state text differs from spec (Low)

**Spec source:** `dashboard-education-ui/spec.md` — "No reviews scheduled — keep learning
and reviews will appear here."

**Current code:** `"No reviews due — keep learning!"`

**Impact:** Cosmetic; the empty state is present and correctly clears the due-now list.
The text is shorter and omits the forward-looking "reviews will appear here" framing.

**Recommended action:** Align the text in a minor polish bead or in the same bead as
GAP-1. No OpenSpec update needed unless the exact string is to be normative.

### GAP-3: 5-map cap on hook aggregation (Low)

**Spec source:** `dashboard-education-ui/spec.md` — "Reviews SHALL be fetched by iterating
all active mind maps and calling the pending reviews endpoint for each."

**Current code:** Fixed 5-slot hook unrolling (`r0`–`r4`, `s0`–`s4`, `f0`–`f4`). Maps at
index 5+ are silently excluded.

**Impact:** Users with more than 5 active mind maps see incomplete review data with no
error or warning. In practice, 5 maps may suffice for most users, but it is a silent
data truncation.

**Recommended action:** Either document the 5-map limit explicitly (code comment + spec
note), or file a follow-up to refactor to a `useQueries` pattern (TanStack Query v5
supports a variable-length `useQueries` call that is safe to call with a dynamic array
length, avoiding the hook-rules limitation).

---

## 6. Summary

Epic 06 core deliverables are complete and merged:

| Item | State |
|---|---|
| `ButlerEducationReviewsTab` component (3 sections) | Merged (PR #1505) |
| Hooks reuse existing endpoints — no new HTTP routes | Confirmed |
| Tab key `reviews` (not `decks`) | Confirmed |
| `EDUCATION_TABS` const in `ButlerDetailPage.tsx` | Merged (PR #1505) |
| `getAllTabs` / `isValidTab` extended for education butler | Merged (PR #1505) |
| Deep-link `?tab=reviews` works (both modes) | Merged + tests |
| Lazy-loaded via `React.lazy` + `Suspense` | Merged (PR #1505) |
| Loading / due / empty states explicit | Merged + 19 RTL tests |
| 19 RTL tests cover all three render branches | Merged (PR #1505) |

Three gaps remain for follow-up:
- **GAP-1** (timeline grouping) — Medium; `dashboard-education-ui/spec.md` scenario unmet
- **GAP-2** (empty-state text) — Low; text differs from spec's canonical string
- **GAP-3** (5-map cap) — Low; silent data truncation for users with >5 active mind maps
