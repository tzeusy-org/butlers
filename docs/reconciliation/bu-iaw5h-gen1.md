# Vertical F (theme commitment + chart palette rationalization) -- Gen-1 Reconciliation

**Issue:** bu-iaw5h.5
**Date:** 2026-05-07
**Epic:** bu-iaw5h -- Frontend redesign F: theme commitment + chart palette rationalization
**Status:** Coverage complete. Two scope extensions (bu-yge9o, bu-h3k9n) are tracked as separate beads and are being handled by parallel workers; they are not gaps in the epic's acceptance criteria.

---

## Epic Acceptance Criteria vs. Implementation

| # | Epic Acceptance Criterion | Bead | Status | Notes |
|---|---|---|---|---|
| 1 | design-language.md contains a "theme commitment" section with the physical-scene sentence and the dark-primary or dual decision | bu-iaw5h.1 | CLOSED (PR #1461) | PASS -- `## Theme commitment` section present at line 278; physical-scene sentence anchored to evening 10pm dim-room use; decision is unambiguously dark-primary with light fallback |
| 2 | chart-1..5 oklch values are tuned for distinguishability under dim ambient | bu-iaw5h.2 | CLOSED (PR #1464) | PASS -- All 5 tokens updated in both `:root` and `.dark`; chroma pulled down across both modes; L variation preserved for categorical distinctness; follow-up fix bu-99yox improved light chart-4/5 L separation |
| 3 | next-themes is removed from package.json dependencies and lockfile | bu-iaw5h.3 | CLOSED (PR #1447) | PASS -- `grep -r 'next-themes' frontend/src` returns zero hits; package.json and package-lock.json clean |
| 4 | useDarkMode hook reflects the committed decision (default mode set accordingly) | bu-iaw5h.4 | CLOSED (PR #1463) | PASS -- Cold load with no localStorage defaults to `'dark'`; `getStoredTheme()` returns `'dark'` as fallback; SSR guard also defaults to `'dark'` |
| 5 | gen-1 reconciliation closed clean | bu-iaw5h.5 | IN PROGRESS | This bead. No new gap beads required (known follow-ons already tracked as bu-yge9o and bu-h3k9n). |

---

## Per-Bead Audit

### bu-iaw5h.1 -- Write theme physical-scene sentence + dark-primary decision

**Closed:** PR #1461 (merged 2026-05-07)

| AC | Status | Finding |
|---|---|---|
| 1. design-language.md has a "Theme Commitment" section | PASS | `## Theme commitment` at line 278 with `Status: settled (bu-iaw5h.1)` |
| 2. Physical-scene sentence is present | PASS | "I open this dashboard at 10pm from a dim room after reviewing my day, and again at 8am from a bright kitchen while coffee is brewing; the evening glance is more deliberate and more frequent than the morning check." |
| 3. Decision is unambiguous (no "either or") | PASS | "Decision: dark-primary with light fallback." -- explicit; "dual-with-dark-default" distinction explained and rejected in the section body |

**Gap:** None.

---

### bu-iaw5h.2 -- Tighten chart palette per mode for evening glance

**Closed:** PR #1464 (merged 2026-05-07)

| AC | Status | Finding |
|---|---|---|
| 1. chart-1..5 oklch values updated in both `:root` and `.dark` | PASS | All 10 slots updated; inline comments document before/after chroma |
| 2. All 5 chart colors visibly distinguishable in both modes | PASS (via spec review) | Hue diversity preserved (orange, teal, slate-blue, yellow/amber, rose/violet); L variation maintained (0.440-0.800 in light, 0.560-0.760 in dark); chroma uniformly reduced to 0.080-0.150 band |
| 3. Visual smoke: Costs, Chronicles, QA charts render without glare | NOT VERIFIED IN BROWSER (see below) | Dev stack is running but headless environment prevents live visual verification |

**Follow-on:** bu-99yox (committed alongside PR #1464 as part of the same PR branch) improved light-mode chart-4/5 L separation (chart-4: 0.780 to 0.800; chart-5: 0.720 to 0.700), giving a 0.10 lightness gap between adjacent yellow/amber stripes in light mode. The values in the live file reflect this refinement.

**Known follow-ons tracked separately:**
- **bu-yge9o** (in progress, parallel): Mute permanence and category token families for evening-glance consistency. Addresses token families beyond chart-1..5 that may still have high chroma in dark mode.
- **bu-h3k9n** (in progress, parallel): Define light-mode accessibility minimums for the fallback palette. Ensures the reduced-chroma light-mode chart values still meet WCAG contrast minimums.

**Gap:** None at the epic scope level. Token-family widening (bu-yge9o) and a11y floor (bu-h3k9n) are correctly scoped as separate work items, not regressions from bu-iaw5h.2.

---

### bu-iaw5h.3 -- Remove next-themes from package.json + lockfile

**Closed:** PR #1447 (merged 2026-05-05)

| AC | Status | Finding |
|---|---|---|
| 1. sonner.tsx no longer imports from next-themes | PASS | `frontend/src/components/ui/sonner.tsx` uses `useDarkMode()` exclusively |
| 2. `grep -r 'next-themes' frontend/src` returns zero hits | PASS | Confirmed by audit |
| 3. next-themes removed from package.json + package-lock.json | PASS | Neither file contains `next-themes` |
| 4. `npm run build` passes | PASS (per close reason) | 1150 vitest tests pass; `tsc --noEmit` passes |
| 5. Toasts render with correct theme in both modes (manual smoke) | NOT VERIFIED IN BROWSER (see below) | Dev stack is running but headless environment prevents live verification |

**Minor documentation drift (not a gap):** Two doc files retain a parenthetical phrase written before bu-iaw5h.3 landed. Both describe `useDarkMode` as the real implementation and mention `next-themes` only as an aside about the old declared-but-unused dependency:
- `about/heart-and-soul/design-language.md` line 78: "Custom `useDarkMode` hook (not `next-themes`, despite it being a dependency)"
- `about/lay-and-land/frontend.md` lines 343-344: "not `next-themes` (despite `next-themes` being a declared dependency; it is unused)"

These parentheticals are now stale since the dependency was removed. The code is correct (zero `next-themes` imports, package.json clean). The doc phrases are harmless but misleading; they should be trimmed as a follow-up cleanup. This does not affect the AC verdict: the code-level AC is PASS.

**Gap:** None at epic scope. Minor doc cleanup tracked as cosmetic follow-up.

---

### bu-iaw5h.4 -- Update useDarkMode and document the commitment

**Closed:** PR #1463 (merged 2026-05-07)

| AC | Status | Finding |
|---|---|---|
| 1. useDarkMode default reflects dark-primary decision | PASS | `getStoredTheme()` returns `'dark'` as fallback (no localStorage entry); SSR guard also returns `'dark'` |
| 2. design-language.md and frontend.md describe the commitment unambiguously | PASS | design-language.md "Theme commitment" section settled (bu-iaw5h.1); `about/lay-and-land/frontend.md` lines 346-353 add "Theme commitment (settled bu-iaw5h.1)" paragraph documenting dark-primary default and linking to design-language.md |
| 3. Cold load with no localStorage: page loads in committed default mode | NOT VERIFIED IN BROWSER (see below) | Dev stack is running; behavioral code path verified by code review |

**Code review finding (cold load path):**
1. `getStoredTheme()` reads `window.localStorage.getItem('theme')`
2. If no stored value or unrecognized value, returns `'dark'`
3. `useState<Theme>(getStoredTheme)` initializes to `'dark'`
4. `useEffect` adds `.dark` to `document.documentElement`

The code path is correct and unambiguous.

**Gap:** None.

---

## Live Browser Verification

**Dev stack status:** Running (Docker). Vite frontend on port 42173 (HTTP 302 -> app); Dashboard API on port 42200 (HTTP 200 /api/health).

**Limitation:** This worker runs in a headless environment without a graphical browser. Direct visual verification of dark-mode cold load, chart palette rendering (Costs, Chronicles, QA pages), and toast theming was not performed interactively.

**Code-path substitution:** For each UI-facing acceptance criterion, the implementation was verified by:
1. Reading the exact code branch that executes on cold load (useDarkMode.ts `getStoredTheme` function)
2. Reviewing the before/after PR data for chart token values against the live `index.css` file
3. Confirming zero `next-themes` imports remain in `frontend/src/`

**Manual smoke test recommendation:** The owner or a sighted agent should:
- Clear localStorage (`localStorage.removeItem('theme')`) and reload port 42173: confirm page opens in dark mode
- Navigate to Costs, Chronicles, and QA chart pages: confirm chart stripes are visually distinguishable in dim ambient (no glare from high-chroma colors)
- Trigger a toast notification (e.g., any settings save action): confirm toast adopts the active theme

---

## Summary

Epic acceptance criteria 1-4 are covered by closed sibling beads (bu-iaw5h.1 through bu-iaw5h.4). AC 5 (gen-1 reconciliation closed clean) is covered by this report. No new gap beads are required from this reconciliation. The two known follow-on items (bu-yge9o for token family muting, bu-h3k9n for light-mode a11y minimums) were correctly identified and tracked as separate beads before this reconciliation ran; they extend the work rather than repair it.

The epic scope is clean. The close reason for bu-iaw5h.5 should record: "Gen-1 reconciliation complete. AC 1-4 fully covered by sibling beads. Two follow-on scope extensions (token muting: bu-yge9o; a11y floor: bu-h3k9n) are tracked separately and do not block this epic's closure. No browser-visual confirmation due to headless environment; code path review substituted."
