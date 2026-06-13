# G12 Gen-1 Reconciliation: Page Rewrites (Hard Cut)

**Issue:** bu-aopqt  
**Auditor:** agent/bu-aopqt  
**Date:** 2026-05-16  
**Scope:** Four page-level shells only (G12A–G12D). Component internals (G11) are audited separately by bu-otub9.

---

## Hard-Cut Verification

`rg "from 'recharts'" frontend/src/pages/Qa*` — **zero matches**. Verified.  
`rg "recharts" frontend/src/pages/Qa*` — only references in test files asserting the absence of recharts. No live imports in any page file or `frontend/src/components/qa/`.

---

## Reconciliation Checklist

### QA Overview Page — `frontend/src/pages/QaOverviewPage.tsx` (G12A, bu-21uf7)

| # | Spec Requirement | Status | File/Line | Notes |
|---|---|---|---|---|
| O1 | Route `/qa` renders the dossier shell | PASS | router.tsx:124 | Route registered inside `RootLayout` |
| O2 | Sticky top bar: severity filter (all/high/medium/low) | PASS | QaOverviewPage.tsx:40-121 | `StickyTopBar` with `SeverityFilter` type and four options |
| O3 | Sticky top bar: theme toggle | PASS | QaOverviewPage.tsx:47-118 | Uses `useDarkMode`, sun/moon SVG |
| O4 | Page header: eyebrow "QA Staffer · dossier" | PASS | QaOverviewPage.tsx:136 | Rendered as mono uppercase |
| O5 | Page header: title "What the staff caught and fixed" | PASS | QaOverviewPage.tsx:138-140 | H1, sans, font-medium |
| O6 | Page header: clock (mono, HH:MM 24h, tabular nums) | **GAP** | QaOverviewPage.tsx:127-145 | Clock not implemented; header has no live clock |
| O7 | Page header: caption `port :<N> · model <M> · patrol every <P>m` | **GAP** | QaOverviewPage.tsx:141-143 | Port number missing; caption reads `model {staffer_status} · patrol every 10m`; `staffer_status` is a status string not the model name; patrol interval hardcoded to `10` |
| O8 | KPI strip: four cells in exact order: `prs landed · 24h`, `mttr · 24h`, `self-resolved · 7d`, `active cases · now` | PASS | QaKpiStrip.tsx:41-66 | Correct order, correct labels |
| O9 | KPI strip: no card chrome, hairline dividers | PASS | QaKpiStrip.tsx:71-75 | Uses `divide-x`/`divide-y` on grid |
| O10 | KPI cell: mono uppercase eyebrow label | PASS | QaKpiStrip.tsx:79-81 | `font-mono uppercase tracking-[0.14em]` |
| O11 | KPI cell: large sans-500 tabular-nums numeric value | PASS | QaKpiStrip.tsx:83-87 | `font-sans text-[32px] font-medium tnum` |
| O12 | KPI sub-label: `+2 vs prior 24h` (prs landed) | **GAP** | QaKpiStrip.tsx:46 | Shows `"24h window"` static string; no prior-period delta; backend `QaKpiBlock` has no `prs_landed_prior_24h` field |
| O13 | KPI sub-label: `−12m vs 7d` (mttr) | **GAP** | QaKpiStrip.tsx:52 | Shows `"terminal cases in 24h"` or `"no terminal cases in 24h"`; no delta |
| O14 | KPI sub-label: `+4pp vs prior week` (self-resolved) | **GAP** | QaKpiStrip.tsx:57 | Shows `"7d window"` static string; no delta |
| O15 | KPI sub-label: `N awaiting CI · M escalated` (active cases) | PASS | QaKpiStrip.tsx:60-65 | Correctly reads `active_breakdown.awaiting_ci` and `active_breakdown.escalated_open_cases` |
| O16 | MTTR empty sample: shows `—` and `no terminal cases in 24h` | PASS | QaKpiStrip.tsx:17-19, 52 | `null` returns `"—"`; sub-label correct |
| O17 | Two-pane body: 320 px case-list rail on left | PASS | CaseList.tsx:31, QaOverviewPage.tsx:204-222 | `md:w-[320px]` on CaseList; rail wraps it |
| O18 | Case rail: rule-separated rows, severity glyph, short case id, butler name, headline, detected+age, PR-state dot | PASS | CaseList.tsx | All fields rendered correctly |
| O19 | Dossier body for selected case | PASS | QaOverviewPage.tsx:226-237 | `<CaseDossier caseId={effectiveCaseId} />` |
| O20 | No case selected: default to most recent case | PASS | QaOverviewPage.tsx:179 | `effectiveCaseId = selectedCaseId ?? casesData[0]?.id` |
| O21 | Empty case list: serif-italic "Nothing in the dossier." + hidden dossier body | PASS | QaOverviewPage.tsx:213-215, 232 | Rail shows italic serif; dossier body shows same |
| O22 | KPI strip renders with zero values when empty | PASS | QaKpiStrip.tsx | `—` for null values; zero renders as `"0"` |
| O23 | No card chrome, no drop shadows, no gradients, no recharts | PASS | QaOverviewPage.tsx | No Card/shadow/gradient/recharts imports |
| O24 | Uses Inter Tight, JetBrains Mono, Source Serif 4, OKLCH palette | PASS | Uses `font-sans`, `font-mono`, `font-serif` Tailwind tokens mapped in `index.css` |
| O25 | URL-driven case selection via `?case=` | PASS | QaOverviewPage.tsx:165-187 | `useSearchParams`; `setParams` with functional update |

### Patrol Detail Page — `frontend/src/pages/QaPatrolDetailPage.tsx` (G12C, bu-e5zne)

| # | Spec Requirement | Status | File/Line | Notes |
|---|---|---|---|---|
| P1 | Route `/qa/patrols/:patrolId` | PASS | router.tsx:125 | Registered inside `RootLayout` |
| P2 | Header: mono eyebrow "QA Patrol" | PASS | QaPatrolDetailPage.tsx:251 | `<Eyebrow>QA Patrol</Eyebrow>` |
| P3 | Header: H2 sans-500 22px title with started-at timestamp | PASS | QaPatrolDetailPage.tsx:252-254 | `font-sans text-[22px] font-medium`; uses `<Time>` component |
| P4 | Header: mono caption with duration, status, sources polled, log_lookback_minutes | PASS | QaPatrolDetailPage.tsx:255-259 | All four values joined with ` · ` |
| P5 | Findings list: rule-separated rows, no table | PASS | QaPatrolDetailPage.tsx:88-140 | `divide-y divide-border` grid rows |
| P6 | Finding row grid: `mark / id+butler / 1fr summary / meta` | PASS | QaPatrolDetailPage.tsx:94 | `grid-cols-[auto_minmax(0,180px)_1fr_auto]` |
| P7 | Finding row: severity glyph | PASS | QaPatrolDetailPage.tsx:96 | `<SeverityGlyph severity={finding.severity} />` |
| P8 | Finding row: mono fingerprint prefix + butler | PASS | QaPatrolDetailPage.tsx:99-101 | `finding.fingerprint.slice(0,8) · source_butler` |
| P9 | Finding row: anonymized event_summary | PASS | QaPatrolDetailPage.tsx:104 | `finding.event_summary` |
| P10 | Finding row: right-aligned mono "novel" badge or dedup_reason | PASS | QaPatrolDetailPage.tsx:119, 63-81 | `<DedupMark>` component |
| P11 | Dispatch summary: rule-separated rows linking to `/qa/investigations/:attemptId` | PASS | QaPatrolDetailPage.tsx:146-164, 273-285 | `<DispatchedRow>` with Link to investigation |
| P12 | Reuses eyebrow / hairline / mono-numeral vocabulary | PASS | QaPatrolDetailPage.tsx | Consistent with `/qa` page |
| P13 | Patrol not found: serif-italic "Patrol not found." with same chrome | PASS | QaPatrolDetailPage.tsx:224-232 | Breadcrumbs + serif italic paragraph |
| P14 | No card chrome, no recharts | PASS | QaPatrolDetailPage.tsx | No Card/recharts imports |

### Investigation Detail Page — `frontend/src/pages/QaInvestigationDetailPage.tsx` (G12C, bu-e5zne)

| # | Spec Requirement | Status | File/Line | Notes |
|---|---|---|---|---|
| I1 | Route `/qa/investigations/:attemptId` | PASS | router.tsx:127 | Registered inside `RootLayout` |
| I2 | Mounts same `CaseDossier` component as `/qa?case=` with no UX divergence | PASS | QaInvestigationDetailPage.tsx:59 | `<CaseDossier caseId={attemptId} />` |
| I3 | Top eyebrow "QA Investigation · #<short_id>" | **PARTIAL** | QaInvestigationDetailPage.tsx:24-56 | Eyebrow is constructed as `QA Investigation · #${caseData.short_id}`. Since backend `short_id` already includes `#` prefix (e.g. `#218`), this produces `##218` (double hash). Severity: visual cosmetic bug. |
| I4 | Back-link to `/qa` | PASS | QaInvestigationDetailPage.tsx:31-34, 42-44, 53-55 | `<Breadcrumbs>` first item links to `/qa` |
| I5 | Existing `Retry` and `Dismiss` actions as pill buttons in dossier header | **GAP** | CaseDossierHeader.tsx, CaseDossier.tsx | Only `remove dismissal` pill exists. No `Retry` pill. No generic `Dismiss` (create new dismissal) pill. The spec explicitly states "existing Retry and Dismiss actions remain available." |
| I6 | Investigation not found: serif-italic "Investigation not found." | PASS | QaInvestigationDetailPage.tsx:28-47 | Two branches (no attemptId, error) render serif italic |
| I7 | No card chrome, no recharts | PASS | QaInvestigationDetailPage.tsx | No Card/recharts imports |

### Investigations List Page — `frontend/src/pages/QaInvestigationsPage.tsx` (G12B, bu-q397p)

| # | Spec Requirement | Status | File/Line | Notes |
|---|---|---|---|---|
| L1 | Route `/qa/investigations` | PASS | router.tsx:126 | Registered |
| L2 | Sticky filter bar: state, severity, butler, time range | PASS | QaInvestigationsPage.tsx:186-280 | Four filters; state/severity/butler/time range |
| L3 | Rule-separated list of cases using `QaCaseSummary` rows | PASS | QaInvestigationsPage.tsx:292-298 | `<CaseList>` component |
| L4 | Each row: same grid as `/qa` rail (sev glyph, short id, butler, headline, detected/age, PR-state dot) | PASS | CaseList.tsx | Shared component |
| L5 | Clicking a row navigates to `/qa/investigations/:attemptId` | PASS | QaInvestigationsPage.tsx:296 | `onSelect={(id) => navigate(...))}` |
| L6 | No Kanban, no tabular dashboard | PASS | QaInvestigationsPage.tsx | Only rule-separated list |
| L7 | Empty list: serif-italic "Nothing matches." | PASS | QaInvestigationsPage.tsx:87-93, 290-291 | `<EmptyLine>` renders serif italic |
| L8 | No card chrome, no recharts | PASS | QaInvestigationsPage.tsx | No Card/recharts imports |
| L9 | Page header includes page title | PASS | QaInvestigationsPage.tsx:198-200 | "Dispatch case index" H1 with mono subtitle |
| L10 | Butler filter uses live butler names | PASS | QaInvestigationsPage.tsx:122-125 | Merges `butlersQuery` names + cases butler names |
| L11 | `shadow-md` on butler dropdown popover | INFORMATIONAL | QaInvestigationsPage.tsx:247 | `shadow-md` on the `role="menu"` dropdown. Spec says "no drop shadows" for the page body; this is a popover chrome element. Acceptable per design language but worth noting. |
| L12 | Load more / pagination | PASS | QaInvestigationsPage.tsx:301-307 | `hasMore` check; button increments limit |

---

## Identified Gaps

### GAP-1: Page header clock missing (O6)

**File:** `frontend/src/pages/QaOverviewPage.tsx`, `PageHeader` component (lines 127-145)  
**Spec requirement:** "clock (mono, `HH:MM` 24h, tabular nums)" in the page header  
**What's there:** No clock. The header only shows eyebrow, H1 title, and runtime caption.  
**What to do:** Add a live `HH:MM` 24h clock using `useState` + `setInterval(1000)` or a `useClock` hook. Use `font-mono tabular-nums` styling. Position alongside the eyebrow or after the runtime caption.

---

### GAP-2: Page header caption missing port number and uses wrong field for model (O7)

**File:** `frontend/src/pages/QaOverviewPage.tsx`, lines 131-143  
**Spec requirement:** Caption `port :<N> · model <M> · patrol every <P>m` where `<M>` is model name, `<N>` is port, and `<P>` is `[modules.qa].patrol_interval_minutes`  
**What's there:** Caption reads `model {data?.staffer_status} · patrol every 10m`. Problems:
1. `staffer_status` is a circuit-breaker status string (`"running"`, `"tripped"`, `"stopped"`), not the model name.
2. Port number is entirely absent.
3. `patrol_interval_minutes` is hardcoded to `10` with a TODO comment "no config endpoint yet".  
**What to do:** The `/api/qa/summary` response would need to expose `model` and `port` fields. Check what fields are available in `QaSummaryResponse`. If `staffer_status` is truly the only available info, document the gap as requiring a backend extension to add `model` and `port` to the summary response. The hardcoded `10` should be replaced once a config endpoint exists.

---

### GAP-3: KPI sub-labels missing prior-period delta comparisons (O12, O13, O14)

**File:** `frontend/src/components/qa/QaKpiStrip.tsx`, lines 46, 52-53, 57  
**Spec requirement:** Sub-labels: `+2 vs prior 24h` (prs landed), `−12m vs 7d` (mttr), `+4pp vs prior week` (self-resolved)  
**What's there:** Static strings `"24h window"`, `"7d window"`, and the MTTR null/non-null branch (`"no terminal cases in 24h"` or `"terminal cases in 24h"`).  
**Root cause:** Backend `QaKpiBlock` model at `src/butlers/api/routers/qa.py:288-291` and frontend type at `frontend/src/api/types.ts:3137-3141` contain no prior-period fields or delta values. The SQL query at `qa.py:1053-1101` does not compute prior-period comparisons.  
**What to do:** Backend needs to add prior-period fields to `QaKpiBlock` (e.g., `prs_landed_prior_24h: int`, `mttr_prior_7d_seconds: float | None`, `self_resolved_prior_7d_pct: float`). The SQL summary query needs a second window per KPI. Frontend `QaKpiStrip` then computes and formats the delta string.

---

### GAP-4: Retry and Dismiss pill buttons missing from dossier header (I5)

**File:** `frontend/src/components/qa/CaseDossierHeader.tsx`, `frontend/src/components/qa/CaseDossier.tsx`  
**Spec requirement (Investigation Detail Page, line 58):** "the existing `Retry` and `Dismiss` actions remain available as pill buttons in the dossier header"  
**What's there:** Only a "remove dismissal" pill (shown when an active dismissal exists). No `Retry` pill (re-trigger investigation), no generic `Dismiss` pill (create new dismissal for a fingerprint).  
**What to do:**
- Add a `Dismiss` pill button to `CaseDossierHeader` that calls the existing `dismissQaKnownIssue` / `useDismissQaIssue` hook. Show only when the case is in a non-terminal state (detect/diagnose/pr).
- Add a `Retry` pill button that calls the QA retry endpoint (if one exists) or triggers a re-dispatch. Hook `useRetryAction` exists in `frontend/src/hooks/use-approvals.ts` but is for approval actions, not QA cases. A QA-specific retry endpoint/hook may be needed.

---

### GAP-5: Double hash in dossier header and investigation eyebrow (I3, partial)

**File:** `frontend/src/components/qa/CaseDossierHeader.tsx:54`, `frontend/src/pages/QaInvestigationDetailPage.tsx:25`  
**Issue:** Backend `short_id_from_uuid()` returns `"#218"` (with `#` prefix). Both files prepend another `#`:
- `CaseDossierHeader.tsx:54`: `` `#{qaCase.short_id}` `` → renders `##218`
- `QaInvestigationDetailPage.tsx:25`: `` `#${caseData.short_id}` `` → renders `##218`  

**Note:** Test fixtures use `short_id: "mfg"` (no hash), which is why tests pass.  
**What to do:** Remove the `#` prefix from the template literals in both files. `qaCase.short_id` already includes `#`. Alternatively, strip the leading `#` from `short_id_from_uuid` at the backend and let the frontend add it — but that requires coordinating with G11.

---

### GAP-6: "Why this fix" renders as sans (not serif italic) (PRPanel)

**File:** `frontend/src/components/qa/PRPanel.tsx:71`  
**Spec requirement (Proposed fix column, line 127):** `"Why this fix" eyebrow followed by a serif-italic 13 px line rendered from investigation_notes.why_this_fix`  
**What's there:** `<p className="font-sans text-sm leading-relaxed text-foreground">{whyThisFix}</p>`  
**What to do:** Change to `font-serif italic text-[13px]`. Note: this is a G11 component (PRPanel) not a page file, so this gap should be handed to the G11 worker or filed separately.

---

## Summary

| Page | Routes | Layout | KPIs | Components | Hard cut |
|---|---|---|---|---|---|
| QaOverviewPage | PASS | PASS (missing clock, port, model) | 1/4 sub-labels correct | PASS | PASS |
| QaPatrolDetailPage | PASS | PASS | n/a | PASS | PASS |
| QaInvestigationDetailPage | PASS | PASS (double-hash cosmetic) | n/a | PARTIAL (missing Retry/Dismiss pills) | PASS |
| QaInvestigationsPage | PASS | PASS | n/a | PASS | PASS |

**Hard-cut verified:** No recharts imports in any of the four page files or the `frontend/src/components/qa/` tree.

**Gaps for coordinator to materialize as beads:**
1. GAP-1: Live clock in QaOverviewPage header (frontend only)
2. GAP-2: Port + model fields in page header caption (backend + frontend)
3. GAP-3: Prior-period KPI delta sub-labels (backend + frontend)
4. GAP-4: Retry and Dismiss pill buttons in dossier header (frontend, possibly backend)
5. GAP-5: Double hash `##NNN` in dossier header and investigation eyebrow (frontend, cosmetic)
6. GAP-6: `why_this_fix` should be serif italic 13px, not sans (G11 component PRPanel)
