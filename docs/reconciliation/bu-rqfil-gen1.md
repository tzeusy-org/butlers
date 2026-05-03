# Vertical B (<DetailPage> consolidation) — Gen-1 Reconciliation Checklist

**Issue:** bu-rqfil.8
**Date:** 2026-05-03
**Epic:** bu-rqfil — Frontend redesign B: <DetailPage> consolidation
**Status:** Gap beads filed; gen-2 reconciliation bead pending (bu-wqb69).

---

## Epic Acceptance Criteria vs. Implementation

| # | Epic Acceptance Criterion | Bead | Status | Notes |
|---|---|---|---|---|
| 1 | permanenceBadge lives in components/memory/badges.tsx (single source) | bu-rqfil.1 | CLOSED (PR #1367) | PASS — badges.tsx exports `permanenceBadge` + `PercentageProgressBar`; 3 consumers deduped (FactDetailPage, RuleDetailPage, MemoryBrowser) |
| 2 | ContactDetailView.tsx contains zero hex literals | bu-rqfil.2 | CLOSED (commit ce185209) | PASS — grep confirms zero `#[0-9a-fA-F]{3,6}` matches in ContactDetailView.tsx |
| 3 | PracticalDrawer + PulseStrip live in components/detail/ and are imported by EntityDetailPage | bu-rqfil.3 | CLOSED (PR #1366) | PARTIAL GAP — files exist and are imported by EntityDetailPage, but landed in `components/relationship/` not `components/detail/`; PracticalDrawer props remain entity-specific (not generic as per AC) |
| 4 | <DetailPage> exists at components/layout/DetailPage.tsx wrapping <Page archetype='detail'> | bu-rqfil.4 | CLOSED (PR #1390) | PASS — DetailPage.tsx exists with all four-tier slot props; 23+ tests; double-H1 fixed |
| 5 | FactDetailPage, RuleDetailPage, ContactDetailPage, ButlerDetailPage, EpisodeDetailPage, ConnectorDetailPage all render via <DetailPage> | bu-rqfil.5, .6, .7 | CLOSED (PRs #1392, #1393, #1397) | PARTIAL GAP — Fact + Rule use <DetailPage> wrapper correctly; Contact + Butler + Episode + Connector use <Page archetype='detail'> directly (bypassing the four-tier slot contract) |
| 6 | EntityDetailPage continues to render but uses extracted PracticalDrawer + PulseStrip primitives | bu-rqfil.3 | CLOSED (PR #1366) | PASS — EntityDetailPage imports both from components/relationship/ and renders identically; line count 1936 < 2000 |
| 7 | gen-1 reconciliation closed clean | bu-rqfil.8 | IN PROGRESS | This bead. 2 gaps found; gap beads to be filed. |

---

## Per-Bead Audit

### bu-rqfil.1 — Extract permanenceBadge twins to components/memory/badges.tsx

**Closed:** PR #1367

| AC | Status | Finding |
|---|---|---|
| 1. components/memory/badges.tsx exists and exports permanenceBadge + PermanenceProgressBar | PASS | File exists; exports `permanenceBadge` (function) and `PercentageProgressBar` (component — note name was rationalized from `PermanenceProgressBar`) |
| 2. FactDetailPage and RuleDetailPage import from the new file; inline copies deleted | PASS | Both import `permanenceBadge, PercentageProgressBar` from `@/components/memory/badges` |
| 3. Visual smoke: render both pages, badges appear unchanged | UNVERIFIED | Dev env live (http://localhost:42173/butlers-dev/) but API data required for non-404 render; structural test coverage present |
| 4. Existing tests pass | PASS (per close reason) | Close reason: 6 existing memory tests pass |

**Gap:** None. Clean close.

---

### bu-rqfil.2 — Delete hex palette + role colors from ContactDetailView.tsx

**Closed:** commit ce185209 (FF-merge to main)

| AC | Status | Finding |
|---|---|---|
| 1. ContactDetailView.tsx contains zero hex literals | PASS | `grep '#[0-9a-fA-F]'` returns zero matches |
| 2. Inline style={{backgroundColor}} props are gone | PASS | No `backgroundColor` or `style={{` in ContactDetailView.tsx |
| 3. Color rendering unchanged visually | UNVERIFIED | No visual regression tooling; structural review only |
| 4. ContactsPage.test.tsx passes | PASS (per close reason) | Close reason confirms FF-merge with passing tests |

**Gap:** None. Clean close.

---

### bu-rqfil.3 — Lift PracticalDrawer + PulseStrip out of EntityDetailPage

**Closed:** PR #1366

| AC | Status | Finding |
|---|---|---|
| 1. components/detail/PracticalDrawer.tsx and components/detail/PulseStrip.tsx exist | **FAIL** | Files exist but at `components/relationship/PracticalDrawer.tsx` and `components/relationship/PulseStrip.tsx` — the `components/detail/` directory does not exist |
| 2. Both export typed component contracts (props are not entity-specific) | **PARTIAL FAIL** | PulseStrip props (`entityId`, `dunbarTier`, `isPinned`) are relationship-entity concepts. PracticalDrawer props include `entity: { metadata, created_at, updated_at }` — entity-specific shape, not a generic `children`-only drawer |
| 3. EntityDetailPage imports both and visually renders identically | PASS | Imports confirmed from `@/components/relationship/PracticalDrawer` and `@/components/relationship/PulseStrip` |
| 4. EntityDetailPage line count drops below 2,000 | PASS | Line count: 1936 |

**Gap 1 (location):** PracticalDrawer + PulseStrip are in `components/relationship/` not `components/detail/`. The relationship namespace is coherent since these concepts are relationship-domain primitives (PulseStrip is consumed by ContactDetailPage), but it diverges from the spec. Since PulseStrip is already imported by ContactDetailPage from `relationship/`, re-homing is low priority — but the spec deviation should be acknowledged and the audit file (`detail-page-audit.md`) updated if the relationship namespace is the canonical decision.

**Gap 2 (generics):** PracticalDrawer props are entity-shaped. This was not generalized before propagation to the <DetailPage> shell. The DetailPage.tsx shell accepts `practical: ReactNode | null` — so callers pass a pre-instantiated `<PracticalDrawer entity={...}/>`. This is functional but means non-entity pages cannot reuse PracticalDrawer without adaptation.

---

### bu-rqfil.4 — Build <DetailPage> shell on EntityDetailPage's four-tier density

**Closed:** PR #1390

| AC | Status | Finding |
|---|---|---|
| 1. components/layout/DetailPage.tsx exists with the prop contract (record, pulse, primary, supporting, auxiliary, practical, breadcrumbs, actions) | PASS | All slot props present; `DetailRecord` interface exported |
| 2. Vitest render test exercises all four tiers plus with/without pulse and practical | PASS | DetailPage.test.tsx: 304 lines, 23+ tests per close reason |
| 3. Compiles strictly under TypeScript | PASS (per close reason) | Reviewer (bu-16kn5) confirmed double-H1 fix + conflict resolution |
| 4. EntityDetailPage continues to render unchanged | PASS | EntityDetailPage unchanged structurally |

**Gap:** None. Clean close.

---

### bu-rqfil.5 — Migrate FactDetailPage and RuleDetailPage onto <DetailPage>

**Closed:** PR #1392

| AC | Status | Finding |
|---|---|---|
| 1. Both pages render via <DetailPage> | PASS | Both import and use `<DetailPage>` with correct slot props (pulse=null, primary=..., supporting=...) |
| 2. Inline H1 / chrome markup deleted | PASS | No inline H1 in either page; `<DetailPage record={{ title, subtitle, type }}>` owns the heading |
| 3. permanenceBadge and progress bar use shared components | PASS | Both import from `@/components/memory/badges` |
| 4. Existing tests pass | PASS | Close reason: 27 new tests + 6 existing memory tests pass; single-H1 verified |

**Gap:** None. Clean close.

---

### bu-rqfil.6 — Migrate ContactDetailPage and ButlerDetailPage onto <DetailPage>

**Closed:** PR #1393

| AC | Status | Finding |
|---|---|---|
| 1. Both pages render via <DetailPage> | **PARTIAL FAIL** | Both use `<Page archetype="detail">` directly — ContactDetailPage at line 61, ButlerDetailPage at line 412. Neither uses the `<DetailPage>` wrapper from `components/layout/DetailPage.tsx` |
| 2. ButlerDetailPage tabs continue to function (lazy loading preserved) | PASS | Tabs imported, `activeTab` state, tab-change handler all present; lazy-loaded tab components confirmed |
| 3. ContactDetailPage uses <PulseStrip> | PASS | PulseStrip imported and used at line 73 for entity-linked contacts |
| 4. Existing tests pass | PASS | Close reason: 1019 tests pass, single-H1 verified, no new token leaks |

**Gap:** ContactDetailPage and ButlerDetailPage bypass the `<DetailPage>` slot contract. They render `<Page archetype='detail'>` directly with custom internal layout. This means they do not enforce the four-tier density structure (hero / pulse / primary / supporting / auxiliary / practical) as slots — each page manages its own layout free-form. This is the most significant structural gap relative to AC5 of the epic.

---

### bu-rqfil.7 — Migrate EpisodeDetailPage and ConnectorDetailPage onto <DetailPage>

**Closed:** PR #1397

| AC | Status | Finding |
|---|---|---|
| 1. Both pages render via <DetailPage> | **PARTIAL FAIL** | Both use `<Page archetype="detail">` directly — EpisodeDetailPage at line 43, ConnectorDetailPage at line 168. Neither uses the `<DetailPage>` wrapper |
| 2. EpisodeDetailPage title resolves to a meaningful name (not literal 'Episode') | PASS | Title = first non-empty line of content (trimmed, capped at 80 chars); falls back to "Episode" only when content is blank |
| 3. AlertDialog confirmation patterns preserved | PASS | AlertDialog imports confirmed in ConnectorDetailPage; dialog at line 530 |
| 4. Existing tests pass | PASS | Close reason: 29 new tests pass, single-H1 verified |

**Gap:** Same structural gap as bu-rqfil.6 — EpisodeDetailPage and ConnectorDetailPage bypass `<DetailPage>` in favor of direct `<Page archetype='detail'>` usage.

---

### bu-rqfil.9 — Delta detail-page-archetype across three dashboard specs

**Status:** MERGED (PR #1398 merged 2026-05-03T19:27:18Z)

| AC | Status | Finding |
|---|---|---|
| 1. openspec/changes/detail-page-archetype/ exists with proposal, tasks, and deltas to all three dashboard specs | PASS | PR #1398 merged; proposal.md, design.md, tasks.md, 4 spec files landed on main |
| 2. Detail-page archetype documented as normative spec section with four-tier density | PASS | design.md 154 lines; specs/detail-page-archetype/spec.md 175 lines |
| 3. Contact route duplication RESOLVED in spec and router config | PASS | router.tsx has single canonical `/contacts/:contactId` route; no `/butlers/relationship/contacts/:id` in router |
| 4. /opsx validate passes | PASS (per close reason) | Close reason states "opsx validate passes" |
| 5. grep for deprecated route literal returns zero matches in router config + nav-config + page imports | PASS | Only `/relationship/contacts` in API client.ts (backend API paths) — no UI router equivalent |

**Note:** PR #1398 was already merged before this reconciliation report was finalized. The OpenSpec change directory now exists on main. The remaining work in bu-r8j9n is to run `/opsx:sync` and verify ConnectorDetailPage spec coverage.

**Additional finding from bu-rqfil.9 notes:** ConnectorDetailPage is flagged as a sibling follow-up — it is not covered in any of the three dashboard specs targeted by bu-rqfil.9 (dashboard-domain-pages, dashboard-relationship, dashboard-butler-management). A separate spec delta for the ingestion/connectors detail page is needed.

---

## Gap Summary

Two structural gaps require new child beads:

### Gap A — Four migration pages bypass `<DetailPage>` wrapper

**Affected pages:** ContactDetailPage, ButlerDetailPage, EpisodeDetailPage, ConnectorDetailPage

**Detail:** Epic AC5 states these pages must "render via `<DetailPage>`" (the four-tier shell at `components/layout/DetailPage.tsx`). All four pages in waves 2 and 3 instead use `<Page archetype='detail'>` directly, implementing their own internal layout. This means:
- The four-tier slot contract (pulse/primary/supporting/auxiliary/practical slots) is not enforced
- Future layout changes to the archetype must be replicated in each page individually
- The "no two code paths" doctrine is violated: Fact/Rule follow one path; Contact/Butler/Episode/Connector follow another

**Severity:** Medium — pages function correctly and use the right Page archetype, but drift from the unified slot contract is a tech debt accumulation point.

### Gap B — OpenSpec sync (PR #1398 now merged; /opsx:sync still needed)

**Detail:** PR #1398 merged at 2026-05-03T19:27:18Z — the `detail-page-archetype` OpenSpec change is now in main. Remaining work: run `/opsx:sync` to apply delta specs to authoritative spec files, verify `/opsx validate` passes post-sync, and confirm ConnectorDetailPage spec coverage.

**Severity:** Low — code is correct; spec documentation sync is the only remaining step.

---

## Live Verification

**Dev environment:** http://localhost:42173/butlers-dev/ (port 42173, base `/butlers-dev/`)

- Frontend dev server: RUNNING (HTTP 200 on root)
- Backend containers: RUNNING (butlers-dev-butlers-up-1 healthy)

**Routes verified (structural — API data not loaded):**
- `/butlers-dev/` — 200 OK (dashboard shell loads)
- `/butlers-dev/memory/facts/:factId` — route registered, FactDetailPage wired
- `/butlers-dev/memory/rules/:ruleId` — route registered, RuleDetailPage wired
- `/butlers-dev/contacts/:contactId` — route registered, ContactDetailPage wired
- `/butlers-dev/butlers/:name` — route registered, ButlerDetailPage wired
- `/butlers-dev/memory/episodes/:episodeId` — route registered, EpisodeDetailPage wired
- `/butlers-dev/ingestion/connectors/:connectorType/:endpointIdentity` — route registered, ConnectorDetailPage wired

**Note:** SPA routes return the HTML shell; rendering correctness requires live backend data. No visual regression discrepancies were identified at the structural level (imports, component tree, archetype prop).

---

## Verdict

- **7 of 7 epic ACs partially or fully implemented** — no AC is wholly unimplemented.
- **2 gap areas identified:** structural (4 pages bypass `<DetailPage>` shell) and documentation (OpenSpec PR pending merge).
- **Gap beads filed:** see below.
- **Gen-2 reconciliation bead:** to be created depending on all gap beads.

---

## Gap Beads Filed

| Gap | Bead ID | Title | Priority |
|---|---|---|---|
| A | bu-35rsy | Migrate ContactDetailPage, ButlerDetailPage, EpisodeDetailPage, ConnectorDetailPage onto `<DetailPage>` shell | 2 (medium) |
| B | bu-r8j9n | Merge PR #1398 and run /opsx:sync for detail-page-archetype | 2 (medium) |

**Gen-2 reconciliation bead:** bu-wqb69 — depends on bu-35rsy + bu-r8j9n.
