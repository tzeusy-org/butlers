# Epic Report: Butler Detail Status-Board Chrome (bu-ja5bt)

**Epic:** bu-ja5bt: Extend Claude Design status-board chrome from /butlers/ index to /butlers/{name} detail page
**Date:** 2026-05-13
**OpenSpec change:** `extend-butler-detail-status-board-chrome`
**Archived at:** `openspec/changes/archive/2026-05-13-extend-butler-detail-status-board-chrome/`
**Status:** All 9 implementation children merged. Reconciliation (bu-ja5bt.10) is this document.

---

## 1. Executive Summary

Epic bu-ja5bt extended the Claude Design status-board archetype from the `/butlers/` index page to
the `/butlers/:name` detail page. Three new UI primitives shipped: `<SiblingButlerNav>`,
`<ButlerDetailHeader>`, and `<ButlerDetailFooter>`, alongside the `ButlerDetailPage` wiring that
mounts them in the correct `<Page archetype="status-board">` header and footer slots. A full test
suite covers the ARIA keyboard contract, responsive tab rail overflow, and integration scenarios
against all 12 real-roster butlers. A doctrine audit (bu-ja5bt.9) confirmed all four doctrine checks
(token policy, em-dash policy, butler-hue scope, real roster) pass against the live primitives.

Nothing regressed: the ButlerHeartbeatTile remains on SystemPage; the mode toggle and tab deep-linking
from the prior bu-iuol4 epic are fully preserved; no new API endpoints or database fields were
introduced.

Two pre-existing follow-up items were filed as separate beads and are not blocking this epic close:
`bu-2ks6c` (P2 bug: pre-existing TypeScript build errors on main involving `atoms.tsx`,
`ButlerLifestyleTasteTab.test.tsx`, and `vite.config.ts`) and `bu-0ofvc` (P3 polish task: add
`snap-start` to TabsTrigger to engage scroll-snap on the tab rail).

---

## 2. Children Summary

| Bead | Title | PR | Outcome |
|---|---|---|---|
| `bu-ja5bt.1` | OpenSpec change authoring | #1609 | Merged |
| `bu-ja5bt.2` | `<SiblingButlerNav>` primitive | #1612 | Merged |
| `bu-ja5bt.3` | `<ButlerDetailHeader>` primitive | #1613 | Merged |
| `bu-ja5bt.4` | `<ButlerDetailFooter>` primitive | #1611 | Merged |
| `bu-ja5bt.5` | `ButlerDetailPage` status-board wiring | #1614 | Merged |
| `bu-ja5bt.6` | Sibling nav a11y/keyboard tests | #1617 | Merged |
| `bu-ja5bt.7` | Responsive tab rail tests | #1618 | Merged |
| `bu-ja5bt.8` | Integration test harness | #1616 | Merged |
| `bu-ja5bt.9` | Doctrine audit | #1615 | Merged |

---

## 3. Spec Compliance Matrix

Source: `openspec/changes/archive/2026-05-13-extend-butler-detail-status-board-chrome/specs/dashboard-butler-management/spec.md`

Tests are in:
- `frontend/src/pages/ButlerDetailPage.test.tsx` (integration harness, bu-ja5bt.8 scenarios 1–14)
- `frontend/src/components/butler-detail/SiblingButlerNav.test.tsx` (bu-ja5bt.2 / bu-ja5bt.6)
- `frontend/src/components/butler-detail/ButlerDetailHeader.test.tsx` (bu-ja5bt.3)
- `frontend/src/components/butler-detail/ButlerDetailFooter.test.tsx` (bu-ja5bt.4)

### MODIFIED Requirement: Butler detail page outer chrome conforms to status-board archetype

| OpenSpec Scenario | Implementing Bead | Test File | Test Name | Status |
|---|---|---|---|---|
| Status-board archetype renders on every butler detail page | bu-ja5bt.5 | `ButlerDetailPage.test.tsx` | "Spec scenario 1 -- status-board archetype chrome on /butlers/{name}" (line 1695): "renders the butler-detail-header slot (status-board header primitive)" | pass |
| Header slot is the sibling-butler nav and detail header | bu-ja5bt.3 / bu-ja5bt.5 | `ButlerDetailPage.test.tsx` | "renders the butler-detail-header slot (ButlerDetailHeader data-testid)" (line 269); "the header slot (butler-detail-header) comes before the tab rail (role=tablist)" (line 1755) | pass |
| Footer slot is the per-butler KPI band | bu-ja5bt.4 / bu-ja5bt.5 | `ButlerDetailPage.test.tsx` | "Spec scenario 7 -- footer KPI band is scoped to the active butler" (line 1933), four tests | pass |
| Heartbeat tile is absent from detail page | bu-ja5bt.5 | `ButlerDetailPage.test.tsx` | "does NOT render ButlerHeartbeatTile on the butler detail page" (line 274); "Spec scenario 9 -- ButlerHeartbeatTile absent from detail page" (line 2037) | pass |
| Heartbeat tile is preserved on SystemPage | bu-ja5bt.5 | `ButlerDetailPage.test.tsx` | "Butler Heartbeats tile DOES render on SystemPage" (line 2092) | pass |

### ADDED Requirement: Sibling-butler navigation strip

| OpenSpec Scenario | Implementing Bead | Test File | Test Name | Status |
|---|---|---|---|---|
| Strip lists all real-roster butlers | bu-ja5bt.2 | `SiblingButlerNav.test.tsx` | "Scenario 1 -- strip lists all real-roster butlers" (line 165) | pass |
| Active butler is marked (aria-current="page") | bu-ja5bt.2 | `SiblingButlerNav.test.tsx` | "Scenario 2 -- active butler is marked" (line 189) | pass |
| Strip keyboard navigation and ARIA contract | bu-ja5bt.6 | `SiblingButlerNav.test.tsx` | "Scenario 3 -- navigation ARIA contract" (line 217); "Scenario 8 -- keyboard contract and ARIA" (line 440) | pass |
| Strip renders skeleton while butler data loads | bu-ja5bt.2 | `SiblingButlerNav.test.tsx` | "Scenario 4 -- skeleton while loading or errored" (line 245) | pass |
| Paused or quarantined sibling butler remains navigable | bu-ja5bt.2 | `SiblingButlerNav.test.tsx` | "Scenario 5 -- paused or quarantined butler stays navigable" (line 297) | pass |
| No butler hue on strip chrome states | bu-ja5bt.2 | `SiblingButlerNav.test.tsx` | "Scenario 6 -- no butler hue on strip chrome" (line 335) | pass |
| Query parameters are carried across butler navigation | bu-ja5bt.2 | `SiblingButlerNav.test.tsx` | "Scenario 7 -- query params carried across navigation" (line 362) | pass |

### ADDED Requirement: Per-butler footer KPI band

| OpenSpec Scenario | Implementing Bead | Test File | Test Name | Status |
|---|---|---|---|---|
| Four KPI cells render for the active butler | bu-ja5bt.4 | `ButlerDetailFooter.test.tsx` | "Scenario 1: Four KPI cells render for the active butler" (line 162) | pass |
| Partial-failure data renders a placeholder glyph | bu-ja5bt.4 | `ButlerDetailFooter.test.tsx` | "Scenario 2: Partial-failure renders placeholder glyphs" (line 194) | pass |
| Last activity uses Time component | bu-ja5bt.4 | `ButlerDetailFooter.test.tsx` | "Scenario 3: Last activity uses Time component" (line 293) | pass |
| KpiCell atom is reused | bu-ja5bt.4 | `ButlerDetailFooter.test.tsx` | "Scenario 4: KpiCell atom is reused" (line 345) | pass |

### ADDED Requirement: Mode-aware tab rail overflow under status-board chrome

| OpenSpec Scenario | Implementing Bead | Test File | Test Name | Status |
|---|---|---|---|---|
| Operator mode tab rail scrolls horizontally | bu-ja5bt.7 | `ButlerDetailPage.test.tsx` | "Spec scenario 14 -- responsive tab rail overflow (bu-ja5bt.7)" (line 2444): "operator tab rail container has overflow-x-auto class" (line 2464) | pass |
| Resident mode tab rail fits without scroll at md+ | bu-ja5bt.7 | `ButlerDetailPage.test.tsx` | "resident mode tab rail has overflow-x-auto from TabsList" (line 2566); "resident mode has 7 tab triggers for a plain butler" (line 2581) | pass |

### ADDED Requirement: Chrome components SHALL comply with the token policy

| OpenSpec Scenario | Implementing Bead | Test File | Test Name | Status |
|---|---|---|---|---|
| No hex, oklch, or rgb literals in chrome components | bu-ja5bt.9 (doctrine audit) | `ButlerDetailPage.test.tsx` | "no hex or oklch color literals appear in the sibling-nav section" (line 1918); doctrine audit `docs/reports/butler-detail-status-board-chrome-audit.md` Audit 1 | pass |
| Butler hue restricted to ButlerMark | bu-ja5bt.9 (doctrine audit) | `ButlerDetailPage.test.tsx` | "no data-butler-hue attribute appears on sibling-nav link chrome elements" (line 1911); doctrine audit Audit 3 | pass |
| No em-dashes in new JSX strings | bu-ja5bt.9 (doctrine audit) | Doctrine audit | `docs/reports/butler-detail-status-board-chrome-audit.md` Audit 2: zero em-dashes in JSX text nodes across all three primitives | pass |
| Real roster only, no fictional butler names | bu-ja5bt.9 (doctrine audit) | Doctrine audit | `docs/reports/butler-detail-status-board-chrome-audit.md` Audit 4: no hardcoded butler names in functional code | pass |

---

## 4. Doctrine Audit Checklist

Results verified against live `main` state (all merged). Full audit record at
`docs/reports/butler-detail-status-board-chrome-audit.md`.

**No Tier 2 hero rendered on `/butlers/:name`**

PASS. `ButlerDetailPage.tsx` mounts `<Page archetype="status-board">` (line 475). The `header`
prop receives `<ButlerDetailHeader>` (line 483) and `footer` receives `<ButlerDetailFooter>`
(line 484). `ButlerDetailHeader.tsx` renders only a `<div>` wrapper with SiblingButlerNav and
butler identity (H1 + ButlerMark); no `<Hero>` component or body-level identity card is present.

**`<ButlerHeartbeatTile />` NOT in detail-page DOM; IS in SystemPage DOM**

PASS, confirmed by static grep:
- `rg -n "ButlerHeartbeatTile" frontend/src/pages/ButlerDetailPage.tsx`: zero matches
- `rg -n "ButlerHeartbeatTile" frontend/src/pages/SystemPage.tsx`: 2 matches (import at line 10, render at line 102)

**All chrome tokens are CSS variables (zero hex/oklch/rgb across the three primitive files)**

PASS. Running:
```
rg -n "#[0-9a-fA-F]{3,8}|oklch\(|rgb\(|rgba\(" \
  frontend/src/components/butler-detail/SiblingButlerNav.tsx \
  frontend/src/components/butler-detail/ButlerDetailHeader.tsx \
  frontend/src/components/butler-detail/ButlerDetailFooter.tsx
```
returns zero matches. Tailwind named utilities (`bg-emerald-500`, `bg-amber-500`) are used for
activity-state tone dots in `SiblingButlerNav.tsx`; these are established codebase precedents,
not hex literals, and are explicitly addressed in the doctrine audit.

**No em-dashes in new component JSX strings**

PASS. All 16 em-dash occurrences across the three primitive files are in `//` line comments or
`/* */` block comments. Zero appear in JSX string literals, text nodes, prop values, or any
user-visible rendered content. The `PLACEHOLDER = "--"` constant in `ButlerDetailFooter.tsx`
uses two regular hyphens (intentionally documented as such in the file).

**Butler hue scoped to `<ButlerMark>` only**

PASS. `SiblingButlerNav.tsx` uses `<ButlerMark>` as the sole hue surface per entry (line 185).
`ButlerDetailHeader.tsx` renders `<ButlerMark>` in two paths (error state line 105, loaded state
line 131). `ButlerDetailFooter.tsx` contains no `<ButlerMark>` and no butler-hue classes of any
kind. All surrounding chrome uses only neutral token classes (`text-foreground`,
`text-muted-foreground`, `border-border`, etc.).

**Real roster: SiblingButlerNav iterates `useButlerStatusBoard()` data; no hardcoded butler names**

PASS. `SiblingButlerNav.tsx` calls `useButlerStatusBoard()` (line 82) and iterates `rows.map()`
(line 151). `ButlerDetailHeader.tsx` calls `useButlerStatusBoard()` (line 58) and resolves the
active butler by runtime name lookup. `ButlerDetailFooter.tsx` scopes all KPI queries to the
`butler` prop at runtime. The only hardcoded butler names in the three files are inside JSDoc
`@example` annotations (documentation, not functional code).

**Mode toggle round-trips in the new chrome**

PASS. `ButlerDetailPage.test.tsx` "Spec scenario 12 -- mode toggle round-trip preserves tab when
possible" (line 2198) covers the full round-trip: switching operator → resident preserves shared
tabs; switching with a resident-only tab auto-promotes; switching with an operator-only tab
auto-promotes. Pre-existing mode toggle tests (Gate-B B2 suite, lines 474–613) remain green.

---

## 5. Per-Butler Smoke Check

The 12 real-roster butlers (from `roster/*/butler.toml`) are:

| Butler | Tested directly | How |
|---|---|---|
| `general` | Yes | Default butler in most ButlerDetailPage.test.tsx scenarios; `BASE_BUTLER.name = "general"` |
| `health` | Yes | Active butler in SiblingButlerNav.test.tsx scenarios 1–8; bespoke tab tests (line 689–691); deep-link tests (line 1067–1078) |
| `finance` | Yes | Bespoke tab tests (line 1120–1128); quarantined/paused scenario (line 1870); integration scenario 5 (line 1869) |
| `relationship` | Yes | aria-current test in SiblingButlerNav.test.tsx (line 192); ButlerDetailHeader.test.tsx happy path; ButlerDetailFooter.test.tsx all scenarios |
| `chronicler` | Yes | Bespoke tab tests (line 1110–1118); integration test (line 2593–2602) with 8 tab triggers |
| `switchboard` | Yes | Bespoke tab tests for routing-log and registry (lines 694–698, 1080–1103) |
| `home` | Yes | Bespoke tab tests: devices tab (line 1130, 1559-1572); ROSTER_NAMES array |
| `travel` | Yes | Bespoke tab tests: trips tab (line 1600-1614); query param carry test (line 375) |
| `messenger` | Yes | ROSTER_NAMES array in integration harness |
| `education` | Yes | ROSTER_NAMES array; query param carry test (line 407) |
| `lifestyle` | Yes | ROSTER_NAMES array in integration harness |
| `qa` | Yes | ROSTER_NAMES array in integration harness |

All 12 roster butlers appear in the `ROSTER_NAMES` constant in `ButlerDetailPage.test.tsx`
(lines 1676–1689) used by the sibling-nav integration scenarios. Eight butlers are exercised
with bespoke-tab or targeted integration coverage beyond the roster array. No butler from the
actual `roster/` directory is absent from test coverage.

Note: the integration harness (`bu-ja5bt.8`) exercises operator vs resident mode rendering
explicitly using `general` (default mode tests), `health` (sibling nav active-butler tests),
`finance` (quarantined sibling scenario), `chronicler` (resident mode + 8 tabs test), and
`relationship` (footer KPI scope test). The remaining 7 butlers are covered via the full
12-entry roster array in scenarios 3, 6, and 13.

---

## 6. OpenSpec Archive

The OpenSpec change has been archived at:

```
openspec/changes/archive/2026-05-13-extend-butler-detail-status-board-chrome/
```

This matches the established convention (`YYYY-MM-DD-<slug>`) as used by prior entries such as
`2026-05-10-redesign-butlers-page-status-board` and `2026-05-05-dashboard-hero-contract`.

The archived directory contains `design.md`, `proposal.md`, `specs/`, and `tasks.md`; all files
from the active change are preserved verbatim under the archive path.

---

## 7. Deferred Follow-Ups

Two follow-up items were filed during the epic and are not blocking the close:

| Bead | Type | Priority | Description |
|---|---|---|---|
| `bu-2ks6c` | bug | P2 | Pre-existing TypeScript build errors on main: `atoms.tsx`, `ButlerLifestyleTasteTab.test.tsx`, `vite.config.ts`. These errors existed before bu-ja5bt and are not regressions introduced by this epic. |
| `bu-0ofvc` | task | P3 | Add `snap-start` to `TabsTrigger` to engage scroll-snap on the tab rail (cosmetic polish). The tab rail has `snap-x` applied (confirmed by integration test at line 2477) but individual triggers lack `snap-start`. |

---

## 8. Appendix: Key New Files

**Frontend primitives:**
- `frontend/src/components/butler-detail/SiblingButlerNav.tsx`
- `frontend/src/components/butler-detail/ButlerDetailHeader.tsx`
- `frontend/src/components/butler-detail/ButlerDetailFooter.tsx`

**Test files introduced by this epic:**
- `frontend/src/components/butler-detail/SiblingButlerNav.test.tsx` (bu-ja5bt.2 / bu-ja5bt.6)
- `frontend/src/components/butler-detail/ButlerDetailHeader.test.tsx` (bu-ja5bt.3)
- `frontend/src/components/butler-detail/ButlerDetailFooter.test.tsx` (bu-ja5bt.4)
- `frontend/src/pages/ButlerDetailPage.test.tsx` (extended with scenarios 1-14, bu-ja5bt.8)

**Modified files:**
- `frontend/src/pages/ButlerDetailPage.tsx` (archetype swap, header/footer slot wiring, ButlerHeartbeatTile removal)

**Reports:**
- `docs/reports/butler-detail-status-board-chrome-audit.md` (doctrine audit, bu-ja5bt.9)
- `docs/reports/butler-detail-status-board-chrome.md` (this document, bu-ja5bt.10)

### Source reliability notes

Spec compliance matrix built from first-hand inspection of the archived spec at
`openspec/changes/archive/2026-05-13-extend-butler-detail-status-board-chrome/specs/dashboard-butler-management/spec.md`
and the four test files listed above.

Doctrine audit section is sourced from `docs/reports/butler-detail-status-board-chrome-audit.md`
(bu-ja5bt.9 / PR #1615) plus direct static grep confirmation against the live source files on `main`.

Per-butler smoke check is sourced from `ROSTER_NAMES` array in `ButlerDetailPage.test.tsx`
(line 1676) and targeted bespoke-tab tests in the same file.
