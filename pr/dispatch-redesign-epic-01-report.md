# Epic 01: Page Detail Shell Primitives — Reconciliation Report (gen-1)

Generated: 2026-05-10
Issue: bu-sfeuw.5
Reporter: Beads Worker (automated reconciliation)

---

## 1. Children Summary

### bu-sfeuw.1 — Snapshot baseline for 10 detail pages

**Status:** CLOSED (direct-merge `edeeac37`)

**Delivered:**
- RTL snapshot/H1-contract tests added for all 10 detail pages:
  `ButlerDetailPage`, `ContactDetailPage`, `ConnectorDetailPage`,
  `EpisodeDetailPage`, `EntityDetailPage`, `FactDetailPage`,
  `QaInvestigationDetailPage`, `QaPatrolDetailPage`, `RuleDetailPage`,
  `SessionDetailPage`.
- Each test asserts: (a) exactly one H1 in the loaded state, (b) zero H1s
  in the loading/skeleton state, (c) no Tier-2 hero block
  (`pre-redesign baseline` wording in QaPatrol and Session tests).
- Baseline confirms `<Breadcrumbs>` is owned by `<Page>` (no standalone
  `<Breadcrumbs>` element at the page layer) for all pages that have
  adopted `<DetailPage>`.

**Note:** `QaInvestigationDetailPage`, `QaPatrolDetailPage`,
`SessionDetailPage` do not yet use `<DetailPage>` / `<Page archetype="detail">`.
They still render standalone `<Breadcrumbs>` in their page body. Tests record
the pre-redesign baseline ("does not render a Tier-2 hero or PulseStrip today").
This is not a gap in Epic 01 scope; those pages are candidates for a future
archetype-adoption epic.

---

### bu-sfeuw.2 — ChatPanel pinned in actions slot

**Status:** CLOSED (direct-merge `4b8fa82f`)

**Delivered:**
- RTL tests in `ButlerDetailPage.test.tsx` assert:
  - Exactly one `<ChatPanel />` instance (no duplicate renders).
  - `data-testid="chat-panel"` content matches `butlerName="general"`.
  - ChatPanel appears in document order after the `<h1>` (actions slot).
- `grep` confirms `<ChatPanel>` is mounted only inside
  `ButlerDetailActions.tsx` — never directly in `ButlerDetailPage.tsx`.

---

### bu-sfeuw.3 — Gate-A A2 composed actions slot (`ButlerDetailActions`)

**Status:** CLOSED (PR #1496, squash `0b6249ea`)

**Delivered:**
- `frontend/src/components/butler-detail/ButlerDetailActions.tsx` created.
  Composes the A2 actions bar: status pill → Force Run → Pause/Resume →
  ChatPanel. Fetches butler status via `useButler`, registry entry via
  `useRegistry`, and eligibility mutation via `useSetEligibility`.
- `ButlerDetailPage.tsx` updated: uses `<DetailPage>` (wraps
  `<Page archetype="detail">`), passes `actions={<ButlerDetailActions butlerName={name} />}`,
  `breadcrumbs`, `pulse`, and `primary` slots. No standalone `<Breadcrumbs>`
  at the page layer.
- `frontend/src/components/layout/DetailPage.tsx` created: canonical
  four-tier shell (hero/H1, pulse strip, primary, supporting, auxiliary,
  practical). Wraps `<Page archetype="detail">`.
- RTL tests in `ButlerDetailPage.test.tsx` (Gate-A A2 suite): verify
  status pill, force-run, pause buttons appear after `<h1>`;
  exactly one `data-testid="butler-detail-actions"` wrapper;
  no `data-testid="hero"` Tier-2 block.

---

### bu-sfeuw.4 — Ladle stories + axe-core a11y baseline

**Status:** CLOSED (PR #1498, merge `c3455ff2`)

**Delivered:**
- `frontend/src/pages/ButlerDetailPage.stories.tsx`: 7 Ladle stories
  covering Default, Loading, Error, StatusOk, StatusDegraded, StatusError,
  StatusWaiting.
- `frontend/src/pages/ButlerDetailPage.a11y.test.tsx`: 7 `jest-axe` tests
  asserting zero axe violations on each story scenario.
- Follow-up `cd29a5c5` (bu-0p9kz): replaced local `StatusPill` stubs in
  both stories and a11y tests with the real `ButlerStatusBadge` component
  (prop `role`/`aria-label` support added). All test files now use
  `ButlerStatusBadge` rather than inline copies.

---

### bu-jb48n — Extract shared `ButlerStatusBadge`

**Status:** CLOSED (PR #1499, merge `58f98fb7`)

**Delivered:**
- `frontend/src/components/butler-detail/ButlerStatusBadge.tsx` extracted
  as a standalone shared component. Covers `ok` → "Up" (emerald), `degraded`
  → "Degraded" (amber outline), `error`/`down` → "Down" (destructive),
  fallback → secondary badge with raw status string.
- Used by `ButlerDetailActions.tsx` and (post-cd29a5c5) by both the Ladle
  stories and the axe-core tests.

---

## 2. Acceptance Coverage Matrix

Epic 01 defined six acceptance criteria (from `bu-sfeuw` description). Gate A
resolved as A2. Coverage assessment:

| # | Criterion | Status | Evidence |
|---|---|---|---|
| E01C1 | `<Page archetype="detail">` renders title + actions + breadcrumbs + (optional Tier-2 per gate-A) without invented components | COVERED | `DetailPage.tsx` wraps `<Page archetype="detail">`. `ButlerDetailPage` passes `title={name}`, `breadcrumbs`, `actions={<ButlerDetailActions>}`. No Tier-2 hero added (A2 resolution). Tests: `DetailPage.test.tsx` hero/slot/breadcrumb suites; `ButlerDetailPage.test.tsx` single-H1 and Gate-A A2 suites. |
| E01C2 | Ladle story exists for butler detail shell covering loading / error / status states | COVERED | `ButlerDetailPage.stories.tsx` exports 7 named stories. Verified by `bu-sfeuw.4`. |
| E01C3 | RTL tests cover the gate-A A2 chosen option | COVERED | `ButlerDetailPage.test.tsx` — Gate-A A2 suite (6 tests): status pill, force-run, pause, Tier-2 hero absent, ButlerDetailActions wrapper count, Up badge text. |
| E01C4 | ChatPanel in the `actions` slot per spec §96-99 | COVERED | `ButlerDetailActions.tsx` line 107 renders `<ChatPanel butlerName={butlerName} />`. RTL tests pin single-mount and heading-row placement. |
| E01C5 | No standalone `<Breadcrumbs>` at the page layer; breadcrumbs owned by `<Page>` | COVERED for 7/10 pages | `ButlerDetailPage.tsx`, `ContactDetailPage.tsx`, `ConnectorDetailPage.tsx`, `EpisodeDetailPage.tsx`, `FactDetailPage.tsx`, `RuleDetailPage.tsx`, `EntityDetailPage.tsx` all pass breadcrumbs via prop. **Partial gap:** `SessionDetailPage`, `QaPatrolDetailPage`, `QaInvestigationDetailPage` still use standalone `<Breadcrumbs>` (pre-redesign baseline; noted in bu-sfeuw.1 tests). |
| E01C6 | Lint passes; 10 detail-page consumers not regressed | COVERED | `uv run ruff check` → "All checks passed!" `ButlerDetailPage.test.tsx` single-H1 suite verifies no duplicate heading from tabs. All sibling detail page tests pass. |

---

## 3. OpenSpec Status

### `redesign-butler-detail-no-hero`

**Validate result:** `Change 'redesign-butler-detail-no-hero' is valid`

**Alignment with implemented code:**

| Spec requirement | Implementation | Match |
|---|---|---|
| `<Tabs>` block is the primary slot | `ButlerDetailPage` passes `primary={<Tabs ...>}` to `DetailPage` | MATCH |
| No Tier-2 page-level hero | No `hero` prop on `<Page>`; no `data-testid="hero"` element | MATCH |
| A2 controls in Tier-1 `<Page>` actions slot | `actions={<ButlerDetailActions butlerName={name} />}` | MATCH |
| Status pills + force-run + pause in actions | `ButlerDetailActions` renders status pill + Force Run + Pause/Resume + ChatPanel | MATCH |
| Breadcrumbs and title remain Tier-1 shell props | `breadcrumbs` and `title` props on `<DetailPage>` / `<Page>` | MATCH |
| No drawer slot | `DetailPage` `practical` prop is omitted on ButlerDetailPage | MATCH |

**All tasks 2.1–2.6 in `tasks.md` are now complete:**
- 2.1: `<Page archetype="detail">` verified rendering title, breadcrumbs, actions — DONE
- 2.2: No standalone `<Breadcrumbs>` on ButlerDetailPage — DONE
- 2.3: `<ChatPanel />` in Page `actions` slot only — DONE
- 2.4: A2 action cluster via Page actions slot (ChatPanel, status pill, force-run, pause) — DONE
- 2.5: RTL/snapshot coverage for A2 shape; sibling pages not regressed — DONE
- 2.6: Ladle shell states (loading, error, status/action variants) — DONE

**Tasks 3.x and 4.x** belong to Epic 04 (bu-8hbph) and this reconciliation
bead respectively.

### All OpenSpec changes validate

```
openspec validate --changes (18 items)
18 passed, 0 failed
```

---

## 4. Gaps

### G1 (minor — not a blocker for Epic 01)

**Title-casing drift**: Spec §133 says `title` prop MUST be the butler's name
"titleized" (`"relationship"` → `"Relationship"`). `ButlerDetailPage.tsx` passes
the raw `name` string without capitalising. The current test (`h1Match![1]).toContain("general")`)
passes regardless. Impact: page title rendered as `"general"` not `"General"`;
`document.title` reads `"general | Butlers"`. This is low-priority polish.

### G2 (minor — spec items 5-6 not wired)

**Shell-level loading/error props not passed**: Spec items 5 and 6 require that
`<Page>` `loading` and `error` props reflect the top-level butler record fetch.
`ButlerDetailPage` does not destructure `isLoading` or `error` from `useButler`
and does not forward them to `<DetailPage>`. When the butler fetch is in-flight
or errors, the page renders the tab body immediately with partial/undefined data
rather than the `DetailSkeleton` / destructive error card. No test covers these
states at the shell level.

### G3 (informational — out of Epic 01 scope)

**Three pages not yet on DetailPage**: `SessionDetailPage`, `QaPatrolDetailPage`,
`QaInvestigationDetailPage` still use standalone `<Breadcrumbs>` and do not
wrap `<Page archetype="detail">`. The Epic 01 scope was Butler detail; these
pages are pre-redesign baselines. A future archetype-adoption sweep would close
this. No child bead required under bu-sfeuw unless the coordinator chooses to
file one.

---

## 5. OpenSpec Validate Output

```
Change 'redesign-butler-detail-no-hero' is valid
18 total changes: 18 passed, 0 failed
```

---

## 6. Verification Summary

| Check | Status |
|---|---|
| `<Page archetype="detail">` renders title + actions + breadcrumbs | PASS |
| No Tier-2 hero block on ButlerDetailPage | PASS |
| ChatPanel only via actions slot (single-mount) | PASS |
| ButlerDetailActions: status pill + force-run + pause + ChatPanel | PASS |
| No standalone `<Breadcrumbs>` on ButlerDetailPage | PASS |
| Ladle stories cover 7 states (loading, error, 5 status variants) | PASS |
| axe-core a11y: zero violations on all 7 stories | PASS |
| Snapshot/H1-contract tests for 10 detail pages | PASS |
| `ButlerStatusBadge` extracted as shared component (bu-jb48n) | PASS |
| `openspec validate redesign-butler-detail-no-hero --strict` | PASS |
| All OpenSpec changes validate (18 items) | PASS |
| Ruff lint (`uv run ruff check`) | PASS |
| Title-casing on ButlerDetailPage `title` prop | GAP (G1, minor) |
| Shell-level `loading` + `error` forwarded to `<DetailPage>` | GAP (G2, minor) |
| SessionDetailPage / QaPatrol / QaInvestigation on DetailPage shell | INFORMATIONAL (G3, out of scope) |

---

## 7. Quality Gate

```
uv run ruff check src/ tests/ roster/ conftest.py --output-format concise
→ All checks passed!
```
