# Vertical G (voice and copy pass) — Gen-1 Reconciliation

**Issue:** bu-scahb.6
**Date:** 2026-05-06
**Epic:** bu-scahb — Frontend redesign G: voice and copy pass
**Status:** Gaps found; gap beads filed below.

---

## Epic Acceptance Criteria vs. Implementation

| # | Epic Acceptance Criterion | Bead | Status | Notes |
|---|---|---|---|---|
| 1 | design-language.md has a 'Voice and Copy' section with rules and examples | bu-scahb.7 | CLOSED (PR #1428) | PASS — Section exists at `## Voice and Copy` under Settled Direction; 10 before/after examples drawn from codebase |
| 2 | A catalog of user-facing strings exists (committed inventory file or doc section) | bu-scahb.2 | CLOSED (PR #1448) | PASS — `about/lay-and-land/frontend-copy-inventory.md` exists (2429 lines, 1887 strings); `scripts/extract-frontend-copy.py` committed |
| 3 | Every button label in the dashboard follows sentence case + owner-direct verb (auditable via grep) | bu-scahb.3 | CLOSED (PR #1451) | PARTIAL GAP — ~30 button labels fixed in 21 files; but 3 non-compliant labels remain: `"Force Patrol Now"` (QaOverviewPage.tsx:682), `"Sync Now"` (QASettingsCard.tsx:324), `"Set Value"` (ButlerStateTab.tsx:184 + StateBrowser.tsx:140) |
| 4 | Every empty-state in src/pages renders via shared `<EmptyState>`; no inline empty markup remains | bu-scahb.4 | CLOSED (PR #1450) | PARTIAL GAP — 18 empty-states refit; inline empty-state markup still present in components (`MedicationTracker.tsx`, `MeasurementChart.tsx`, `AggregatePieChart.tsx`) and in pages (`DashboardPage.tsx`, `EntityDetailPage.tsx`); some are contextually distinct but a few are true empty states not using the shared primitive |
| 5 | Zero em-dashes in user-facing strings under frontend/src/pages and frontend/src/components | bu-scahb.5 | CLOSED (PR #1446) | NEAR-PASS — 35 em-dashes replaced in 22 files; 2 remaining em-dashes are in `sr-only` spans in `SourceStateBadgeStrip.tsx` (screen-reader text: `" — has recent error"`, `" — no recent data"`); these are accessibility-only and not rendered visually, but still appear in the ARIA tree |
| 6 | gen-1 reconciliation closed clean | bu-scahb.6 | IN PROGRESS | This bead. 3 beads filed below: 2 gap beads (bu-65j6j, bu-77sy5) + 1 gen-2 reconciliation bead (bu-jj1ts). |

**Additional AC from bu-scahb.7 (subsumed G1):**

| # | AC | Status | Notes |
|---|---|---|---|
| 7 | Em-dash ban appears as a numbered non-negotiable in design-language.md | bu-scahb.7 | PASS — Non-negotiable #6 added |
| 8 | `scripts/check-no-em-dashes.py` exits 0 on close | bu-scahb.7 | FAIL — Script exits 1; `frontend-copy-inventory.md` contains 3 em-dashes: one in the auto-generated header (`— re-run the script`) and two mirroring `SourceStateBadgeStrip.tsx` sr-only strings |
| 9 | about/heart-and-soul/README.md updated to surface Voice and Copy section | bu-scahb.7 | PASS — README.md index row 6 links directly to `design-language.md#voice-and-copy` |

---

## Per-Bead Audit

### bu-scahb.7 — Voice and copy doctrine + em-dash ban

**Closed:** PR #1428

| AC | Status | Finding |
|---|---|---|
| 1. design-language.md has 'Voice and Copy' section under Settled Direction | PASS | `## Voice and Copy` section at line 310; covers register, capitalization, buttons, empty states, errors, bans |
| 2. Em-dash ban as numbered non-negotiable | PASS | Non-negotiable #6 added at line 252 |
| 3. 6-10 before/after examples from codebase | PASS | 10 examples (Examples 1-10) drawn from IngestionPage, QaInvestigationDetailPage, EntitiesPage, EducationPage, EntityDetailPage, SettingsPage, CalendarWorkspacePage |
| 4. `scripts/check-no-em-dashes.py` exits 0 | FAIL | Exits 1; flags 3 em-dashes in `about/lay-and-land/frontend-copy-inventory.md` (see Gap 1 below) |
| 5. README.md updated | PASS | Index row 6 and "Touching the dashboard?" tip both surface the Voice and Copy section |

**Gap:** Checker script fails due to em-dashes in the auto-generated inventory file (see Gap 1).

---

### bu-scahb.1 — Extend design-language.md (superseded)

**Closed:** Superseded by bu-scahb.7 with reason "Superseded by bu-scahb.7". No gap.

---

### bu-scahb.2 — Catalog all user-facing strings

**Closed:** PR #1448

| AC | Status | Finding |
|---|---|---|
| 1. `about/lay-and-land/frontend-copy-inventory.md` exists, strings grouped by file | PASS | File exists at 2429 lines; strings grouped per TSX file |
| 2. Script committed under `scripts/` | PASS | `scripts/extract-frontend-copy.py` present |
| 3. Spot-check coverage: strings from 5 representative pages present | PASS (per close reason) | Close reason confirms 1887-string inventory with spot-check |

**Gap:** None. Clean close on implementation. Note: inventory file itself contains 3 em-dashes (auto-generated artifact) — addressed in Gap 1.

---

### bu-scahb.3 — Standardize button labels

**Closed:** PR #1451

| AC | Status | Finding |
|---|---|---|
| 1. Every button label in src/pages and src/components matches sentence case + owner-direct verb | PARTIAL | ~30 labels fixed; 3 non-compliant remain (see below) |
| 2. Inventory file regenerated; button-label entries pass voice rules | NOT VERIFIED | No regeneration noted in close reason |
| 3. Tests pass after relabeling | PASS (per close reason) | 1150 tests pass |

**Remaining non-compliant button labels:**
- `"Force Patrol Now"` at `frontend/src/pages/QaOverviewPage.tsx:682` — should be `"Run patrol"` or `"Trigger patrol"`
- `"Sync Now"` at `frontend/src/components/settings/QASettingsCard.tsx:324` — should be `"Sync now"` (lowercase "now")
- `"Set Value"` at `frontend/src/components/butler-detail/ButlerStateTab.tsx:184` and `frontend/src/components/state/StateBrowser.tsx:140` — should be `"Set value"` (lowercase "v")

**Gap:** 3 non-compliant button labels missed in G3 sweep (see Gap 2 below).

---

### bu-scahb.4 — Standardize empty-state copy via shared `<EmptyState>`

**Closed:** PR #1450

| AC | Status | Finding |
|---|---|---|
| 1. Zero inline empty-state markup remains in src/pages | PARTIAL | `DashboardPage.tsx:196` renders inline `<div><p>No failed notifications. All systems healthy.</p></div>`. `EntityDetailPage.tsx:619` and `:1094-1097` have inline `<p>` text empty states. SecretsPage.tsx:425 has a contextual inline `<p>`. |
| 2. Every `<EmptyState>` instance follows fact-then-action pattern | PASS (spot-check) | Random sample of 6 EmptyState usages confirms `title=` states the fact, `description=` offers context or action |
| 3. Voice guide compliance verified | PASS (spot-check) | No exclamation marks, no em-dashes in EmptyState props found |

**Component-level gaps (in scope per AC):**
- `frontend/src/components/health/MedicationTracker.tsx` — local `function EmptyState()` renders inline `<div><p>No medications found.</p></div>`, does not use shared component or offer an action
- `frontend/src/components/health/MeasurementChart.tsx` — local `function EmptyState()` renders inline `<div><p>No measurements found for this type and date range.</p></div>`, does not use shared component
- `frontend/src/components/chronicles/AggregatePieChart.tsx` — local `function EmptyState()` renders inline `<div><span>No activity recorded for this window.</span></div>` (no action — acceptable as chart zero-state)

**Gap:** Inline empty-state markup in components outside src/pages was not fully covered (see Gap 2).

---

### bu-scahb.5 — Replace em-dashes

**Closed:** PR #1446

| AC | Status | Finding |
|---|---|---|
| 1. `grep -r '—' frontend/src/pages frontend/src/components` returns zero hits in JSX text nodes | NEAR-PASS | Two sr-only spans remain: `<span className="sr-only"> — has recent error</span>` and `<span className="sr-only"> — no recent data</span>` in `SourceStateBadgeStrip.tsx` (lines 81, 118). These are rendered to the ARIA tree. |
| 2. `grep -r ' -- '` returns zero hits | PASS | No double-hyphens found in user-facing strings |
| 3. Manual reading confirms replacements make sense | PASS (per close reason) | 35 contextual replacements (colons/commas/parens/periods) |

**Note:** Null-display fallback `"—"` (e.g., `SecretsPage.tsx:305`, `EntitiesPage.tsx:394`, etc.) is explicitly permitted by non-negotiable #6 ("typographic convention, not prohibited prose"). The sr-only em-dashes are borderline; they convey semantic state to screen readers and do not violate the spirit of the ban (they were never prose). Logged as a minor gap for the next pass.

**Gap:** None critical. The sr-only em-dash instances are noted as discovered follow-up (Gap 2).

---

## Gaps Found

### Gap 1 — `check-no-em-dashes.py` fails on auto-generated inventory file

**File:** `about/lay-and-land/frontend-copy-inventory.md`
**Lines:**
- Line 4: `Do **not** edit manually — re-run the script to refresh.` (header prose)
- Line 1200: `- — has recent error` (mirror of SourceStateBadgeStrip.tsx sr-only text)
- Line 1201: `- — no recent data` (mirror of SourceStateBadgeStrip.tsx sr-only text)

The inventory header itself uses an em-dash in prose (`— re-run the script`). The script AC requires exit 0, which blocks CI/pre-commit adoption.

**Fix options:**
1. Rewrite inventory header to use a colon: `Do **not** edit manually: re-run the script to refresh.`
2. Make the checker script skip auto-generated files (fragile)
3. Fix the sr-only strings in SourceStateBadgeStrip.tsx so they don't propagate into inventory

Option 1 + 3 together clean both root causes. Option 1 is a one-line doc fix.

### Gap 2 — Residual non-compliant labels and inline empty states

Three button labels missed in the G3 sweep, and inline empty states remain in health and chronicle components. Scope for the next cleanup pass.

---

## Gap Beads Filed

Three beads were created from this reconciliation:

**bu-65j6j** — Fix `check-no-em-dashes.py` failure: rewrite inventory file header and fix sr-only em-dashes in SourceStateBadgeStrip.tsx so the script exits 0.
(Filed; dep: discovered-from bu-scahb.6)

**bu-77sy5** — G3/G4 residual cleanup: fix 3 non-compliant button labels (`"Force Patrol Now"`, `"Sync Now"`, `"Set Value"`) and migrate inline empty states in health/chronicle components to shared `<EmptyState>`.
(Filed; dep: discovered-from bu-scahb.6)

**bu-jj1ts** — Gen-2 reconciliation for vertical G, to run after bu-65j6j and bu-77sy5 are closed.
(Filed; deps: blocked-by bu-65j6j, blocked-by bu-77sy5)

---

## Epic Closeability

The epic **bu-scahb is NOT closeable** at gen-1. The following AC items are unresolved:

- AC #3: 3 non-compliant button labels remain
- AC #4: Inline empty-state markup in health/chronicle components
- AC #5 (soft): sr-only em-dashes in SourceStateBadgeStrip.tsx
- AC (G7): `check-no-em-dashes.py` exits 1 (blocks CI wiring)

A gen-2 reconciliation bead (bu-jj1ts) should be filed once gap beads bu-65j6j and bu-77sy5 are closed.

---

## Audit Summary

| Bead | Title | Close Status | AC Coverage |
|---|---|---|---|
| bu-scahb.7 | Voice doctrine + em-dash ban | CLOSED PR #1428 | PASS (checker script gap) |
| bu-scahb.1 | Design-language voice guide | CLOSED (superseded by .7) | N/A |
| bu-scahb.2 | Catalog user-facing strings | CLOSED PR #1448 | PASS |
| bu-scahb.3 | Standardize button labels | CLOSED PR #1451 | PARTIAL (3 labels missed) |
| bu-scahb.4 | Standardize empty-state copy | CLOSED PR #1450 | PARTIAL (health/chronicle components) |
| bu-scahb.5 | Replace em-dashes | CLOSED PR #1446 | PASS (sr-only noted, not critical) |
| bu-scahb.6 | Gen-1 reconciliation | IN PROGRESS | This report |
