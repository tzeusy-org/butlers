# G11 Gen-1 Reconciliation: QA Dossier Components

**Issue:** bu-otub9  
**Branch:** agent/bu-otub9  
**Date:** 2026-05-16  
**Auditor:** beads-worker (G11 reconciliation audit)

This document maps every scenario item in the Case Dossier Layout requirements
(spec.md, design.md §D9) to the implementation file and line where it is
(or is not) covered.

---

## Scope

Components audited (all under `frontend/src/components/qa/`):

| Group | Components |
|---|---|
| G11A | QaKpiStrip.tsx, CaseList.tsx, CaseDossierHeader.tsx, StateTrack.tsx |
| G11B | ClaimAnchoredBlurb.tsx, EvidenceLog.tsx |
| G11C | CounterEvidence.tsx, PRPanel.tsx, DiffPreview.tsx |
| G11D | PatrolJournal.tsx, CaseDossier.tsx |

Page-level shells also reviewed:
`QaOverviewPage.tsx`, `QaInvestigationDetailPage.tsx`, `QaInvestigationsPage.tsx`, `QaPatrolDetailPage.tsx`

---

## Reconciliation Checklist

### Scenario: Dossier header

| Requirement | File:Line | Status | Notes |
|---|---|---|---|
| Severity glyph (square, colored) | CaseDossierHeader.tsx:50-52 | PASS | Uses `qaSeverityClassName` from utils.ts |
| Mono `#<short_id>` | CaseDossierHeader.tsx:54 | **GAP** | Renders `#{qaCase.short_id}` but `short_id` from API already includes `#` (e.g. `"#218"`), producing `##218`. Test fixture in atoms.test.tsx uses `short_id: "mfg"` (no hash), masking the bug. Page test in QaInvestigationDetailPage.test.tsx:36 uses `"#218"` and asserts `toContain("#218")` which passes even with `"##218"`. |
| Mono `· <butler>` | CaseDossierHeader.tsx:54 | PASS | Renders `· {qaCase.butler}` |
| Mono `· detected <HH:MM>` | CaseDossierHeader.tsx:54 | PASS | Uses `formatQaDetectedTime()` from utils.ts |
| Right-aligned StateTrack | CaseDossierHeader.tsx:56-57 | PASS | `<StateTrack stage={stage} />` in right-side flex |
| H2 sans-500 22px headline | CaseDossierHeader.tsx:78-80 | PASS | `font-sans text-[22px] font-medium` |
| Fallback to event_summary when headline null | CaseDossierHeader.tsx:79 | PARTIAL | Shows `"Untitled QA case"` not `event_summary`. The backend `headline_for_case()` already handles the fallback so headline is never null in practice; but the frontend fallback text is wrong per spec and would show if the API ever sends null. |
| StateTrack "detect — diagnose — pr — landed" | StateTrack.tsx:10-15 | PASS | Stages list matches spec |
| StateTrack escalated variant (amber pr+landed) | StateTrack.tsx:18-25 | PASS | Amber on pr/landed when escalated |

### Scenario: Active dismissal display

| Requirement | File:Line | Status | Notes |
|---|---|---|---|
| Mono caption "dismissed until `<expires_at>`" | CaseDossierHeader.tsx:73-76 | PASS | Rendered when `dismissal` non-null |
| "remove dismissal" pill action | CaseDossierHeader.tsx:58-71 | PASS | `Button` rendered alongside existing actions |
| DELETE /api/qa/dismissals/:fingerprint on click | CaseDossierHeader.tsx:66 + use-qa.ts:198 | PASS | Calls `removeQaDismissal(dismissal.fingerprint)` |
| Re-fetch case after dismissal removed | use-qa.ts:199-203 | PASS | Invalidates `["qa-case"]` and `["qa-cases"]` query keys |
| "Retry" pill button in dossier header | CaseDossierHeader.tsx | **GAP** | Missing. Spec says "existing Retry and Dismiss actions remain available as pill buttons in the dossier header." No `Retry` button in any QA dossier component. No frontend hook wires to `/api/healing/attempts/:id/retry`. |
| "Dismiss" pill button in dossier header | CaseDossierHeader.tsx | **GAP** | Missing. `useDismissQaIssue()` hook exists in use-qa.ts:165 but is not wired to any dossier header button. |

### Scenario: Diagnosis column

| Requirement | File:Line | Status | Notes |
|---|---|---|---|
| "Diagnosis" mono uppercase eyebrow | CaseDossier.tsx:86-87 | PASS | `<DossierEyebrow>Diagnosis</DossierEyebrow>` |
| Serif paragraph from blurb_segments | ClaimAnchoredBlurb.tsx:40-83 | PASS | `font-serif text-[17px] leading-8` |
| Plain string segments render as-is | ClaimAnchoredBlurb.tsx:48-49 | PASS | `typeof segment === "string"` branch |
| Anchored segments with mono superscript [N] | ClaimAnchoredBlurb.tsx:59-79 | PASS | Superscript rendered with claim number |
| Claim numbers assigned by order-of-appearance | claimOrder.ts:5-14 | PASS | `getClaimOrderFromSegments()` extracts first-seen order |
| "Hypothesis" mono line from hypothesis field | CaseDossier.tsx:96-101 | PASS | Mono font-mono text-[11px] |
| "Evidence · log fragments" eyebrow | CaseDossier.tsx:103-104 | PASS | |
| Evidence grid: ts, level, butler, msg | EvidenceLog.tsx:80 + 103-112 | PASS | Grid cols: claim, ts, level, butler, msg |
| Evidence bracketed claim numbers [N] | EvidenceLog.tsx:97-100 | PASS | First column shows `[N,M]` labels |
| Column order: claim # last per spec | EvidenceLog.tsx:80 | **MINOR** | Spec says "ts, level, butler, msg, and bracketed claim numbers" (claims at end), but implementation puts claim # in the first 20px column. Functionally equivalent but ordering diverges from spec. |
| "Considered & ruled out" eyebrow | CaseDossier.tsx:113-116 | PASS | |
| Counter evidence rows: hypothesis + reason + verdict | CounterEvidence.tsx:28-42 | PASS | Each row renders all three fields |
| Hover claim segment highlights matching evidence | ClaimAnchoredBlurb.tsx:51-52 + EvidenceLog.tsx:71 | PASS | Bidirectional via shared `hoveredClaim` state |
| Hover evidence row highlights matching claim segments | EvidenceLog.tsx:85-90 | **PARTIAL** | When evidence links to multiple claims (e.g. c1 and c2), only `myClaims[0]` is broadcast as the hover state. Only the first claim's segments are highlighted; remaining linked claims' blurb segments stay unhighlighted. Spec says "the claim segments referencing that row's id are highlighted." |
| Eyebrow font: mono uppercase | CaseDossier.tsx:22-25 (DossierEyebrow) | PASS | `font-mono uppercase tracking-[0.14em]` |

### Scenario: Proposed fix column

| Requirement | File:Line | Status | Notes |
|---|---|---|---|
| "Proposed fix" eyebrow | CaseDossier.tsx:133 | PASS | |
| State chip (drafted/open/merged/closed) | PRPanel.tsx:38-45 | PASS | Border+text colored by state |
| State chip "rejected" variant | PRPanel.tsx:18-23 | **GAP** | `prStateClassName` only covers `closed/drafted/merged/open`. Spec lists "drafted/open/merged/closed/rejected" but `QaPrSummary.state` in types.ts also only has 4 values. Backend API has no "rejected" state. Gap spans both frontend component and API/type definition. |
| Mono `pr #<number> · <state>` | PRPanel.tsx:46-48 | PASS | |
| Sans-500 14px PR title | PRPanel.tsx:58 | **GAP** | Uses `text-[17px]` not `text-[14px]`. Spec requires "Sans-500 14 px PR title." |
| Mono caption: branch · ci status · additions/deletions | PRPanel.tsx:61-63 | **PARTIAL** | Uses `+{pr.additions} / -{pr.deletions}` separator `/`. Spec shows `+<additions> −<deletions>` (space-separated with minus sign, no slash). |
| "Why this fix" eyebrow | PRPanel.tsx:68 | PASS | |
| Serif-italic 13px why_this_fix | PRPanel.tsx:71 | **GAP** | Renders `font-sans text-sm` (not `font-serif italic text-[13px]`). Spec says "serif-italic 13 px line." |
| "Diff preview" eyebrow | PRPanel.tsx:77 | PASS | |
| Line-kind-aware diff renderer | DiffPreview.tsx:36-64 | PASS | Handles `meta / + / - / (space)` kinds |
| Mono footer: "opened HH:MM · merged HH:MM" | PRPanel.tsx:84-93 | **PARTIAL** | Uses `<Time value={pr.opened_at} mode="smart" />`. Spec says `opened <HH:MM>` (24h mono fixed). `mode="smart"` shows relative time for recent events ("4 minutes ago"), not fixed HH:MM. Should use `mode="absolute"` with `precision="time"` or a dedicated `precision="time"` format. |
| null-PR fallback "No PR — escalated to user." | PRPanel.tsx:28-30 | **MINOR** | Em-dash in prose copy (`No PR — escalated to user.`). Voice doctrine bans em-dashes in prose (design-language.md). The spec itself uses the em-dash, creating a spec-vs-doctrine conflict. |

### Scenario: Patrol journal section

| Requirement | File:Line | Status | Notes |
|---|---|---|---|
| Mono row per event: ts, step, text | PatrolJournal.tsx:56-79 | PASS | Grid with time, step, text+detail |
| ts as HH:MM | PatrolJournal.tsx:24-28 | PASS | `toLocaleTimeString` with hour/minute |
| Step name in step color | PatrolJournal.tsx:10-22 + 65-68 | PASS | Per-step color map |
| Step colors: flagged amber | PatrolJournal.tsx:16 | PASS | `text-amber-500` |
| Step colors: sampled neutral | PatrolJournal.tsx:19 | PASS | `text-foreground` |
| Step colors: cross-checked neutral | PatrolJournal.tsx:12 | PASS | `text-foreground` |
| Step colors: drafted neutral | PatrolJournal.tsx:13 | PASS | `text-foreground` |
| Step colors: considered dim | PatrolJournal.tsx:13 | PASS | `text-muted-foreground` |
| Step colors: wait dim | PatrolJournal.tsx:21 | PASS | `text-muted-foreground` |
| Step colors: tick dim | PatrolJournal.tsx:20 | PASS | `text-muted-foreground` |
| Step colors: concluded green | PatrolJournal.tsx:12 | PASS | `text-emerald-500` |
| Step colors: merged green | PatrolJournal.tsx:17 | PASS | `text-emerald-500` |
| Step colors: escalated amber | PatrolJournal.tsx:15 | PASS | `text-amber-500` |
| "opened" step entry in color map | PatrolJournal.tsx:18 | **MINOR** | `opened` is in `stepClassName` as amber but it is NOT a valid journal step per D1/QaJournalEvent type. Dead entry, causes no visible issue but is code drift. |
| Optional dim detail line | PatrolJournal.tsx:75-79 | PASS | Renders `event.detail` in muted if non-null |
| Eyebrow: "Patrol journal · every QA decision on this case" | PatrolJournal.tsx:47-49 | PASS | |
| Right-aligned caption: count entries · patrol every Pm | PatrolJournal.tsx:50-54 | PASS | `ml-auto` positioned |
| Section hidden when no journal events | PatrolJournal.tsx:35 | PASS | `if (events.length === 0) return null` |
| Events rendered in chronological order | PatrolJournal.tsx:37-43 | PASS | Sorted by `ts` ascending |

### Scenario: Overview page layout (QaOverviewPage)

| Requirement | File:Line | Status | Notes |
|---|---|---|---|
| Sticky top bar: severity filter + theme toggle | QaOverviewPage.tsx:40-121 | PASS | `StickyTopBar` component |
| Page header eyebrow "QA Staffer · dossier" | QaOverviewPage.tsx:135-136 | PASS | |
| Page header H1 "What the staff caught and fixed" | QaOverviewPage.tsx:138-140 | PASS | `font-sans text-2xl font-medium` |
| Page header clock (mono HH:MM 24h, tabular nums) | QaOverviewPage.tsx | **GAP** | Clock is mentioned in the file's leading comment (line 6) but is NOT rendered in the template. No `<Time mode="clock-24h-mono">` in the PageHeader component. |
| Page header caption: model + patrol interval | QaOverviewPage.tsx:141-143 | PARTIAL | Shows `model {model} · patrol every {patrolInterval}m` but missing `port :<N>` field. Spec says "port :<N> · model <M> · patrol every <P>m". No port field in `QaSummary` API type. |
| KPI strip: 4 cells in correct order | QaKpiStrip.tsx:41-66 | PASS | prs landed · 24h, mttr · 24h, self-resolved · 7d, active cases · now |
| KPI strip hairline dividers | QaKpiStrip.tsx:69-73 | PASS | `divide-x divide-border/60` |
| KPI strip: mono uppercase eyebrow labels | QaKpiStrip.tsx:79-81 | PASS | |
| KPI strip: large sans-500 tabular-nums value | QaKpiStrip.tsx:82-87 | PASS | `text-[32px] font-medium tnum` |
| KPI strip: mono sub-labels | QaKpiStrip.tsx:88-90 | PARTIAL | Current sub-labels are "24h window", "7d window", "N awaiting CI · M escalated". Spec example sub-labels "+2 vs prior 24h", "−12m vs 7d", "+4pp vs prior week" imply period-over-period comparison data. The API (`QaKpiBlock`) has no prior-period fields. The delta comparisons are not implemented. |
| MTTR null: shows "—" and "no terminal cases in 24h" | QaKpiStrip.tsx:52-53 | PASS | |
| Two-pane body: 320px case rail + dossier | QaOverviewPage.tsx:203-239 | PASS | `shrink-0` rail + `flex-1` main |
| Case rail: rule-separated rows | CaseList.tsx:35-36 | PASS | `divide-y divide-border/60` |
| Case row: severity glyph + short_id + butler + headline + detected/age + PR dot | CaseList.tsx:50-77 | PASS | All fields present |
| Empty rail: serif-italic "Nothing in the dossier." | QaOverviewPage.tsx:214-216 | PASS | |
| Default selection: first case when no ?case= param | QaOverviewPage.tsx:179 | PASS | `effectiveCaseId = selectedCaseId ?? casesData[0]?.id` |
| Dossier body hidden when case rail empty | QaOverviewPage.tsx:231-232 | PASS | `DossierPlaceholder` shown when no cases |
| URL-driven case selection via ?case= | QaOverviewPage.tsx:165-186 | PASS | `useSearchParams` + functional update |

### Scenario: KPI strip values

| Requirement | File:Line | Status | Notes |
|---|---|---|---|
| prs_landed_24h cell | QaKpiStrip.tsx:43-48 | PASS | |
| mttr_24h_seconds cell | QaKpiStrip.tsx:49-54 | PASS | |
| self_resolved_7d_pct cell | QaKpiStrip.tsx:55-59 | PASS | |
| active_cases_now cell | QaKpiStrip.tsx:60-65 | PASS | |
| active cases sub-label: "N awaiting CI · M escalated" | QaKpiStrip.tsx:37-38 | PASS | `formatActiveBreakdown()` |
| MTTR null: "—" value + "no terminal cases in 24h" sublabel | QaKpiStrip.tsx:17-27 + 52 | PASS | |

### Scenario: Investigation detail page

| Requirement | File:Line | Status | Notes |
|---|---|---|---|
| Top eyebrow "QA Investigation · #<short_id>" | QaInvestigationDetailPage.tsx:24-25 | **GAP** | Eyebrow string is `QA Investigation · #${caseData.short_id}`. Since `short_id` includes `#` (e.g. `"#218"`), the rendered value is `QA Investigation · ##218`. Same double-hash issue as CaseDossierHeader. |
| Back-link to /qa | QaInvestigationDetailPage.tsx:53-54 | PASS | Breadcrumbs with `{ label: "QA", href: "/qa" }` |
| Mounts same CaseDossier component | QaInvestigationDetailPage.tsx:59 | PASS | `<CaseDossier caseId={attemptId} />` |
| "Retry" pill button in investigation detail | QaInvestigationDetailPage.tsx + CaseDossierHeader.tsx | **GAP** | Not present. Same gap as dossier header Retry pill. |
| "Dismiss" pill button in investigation detail | QaInvestigationDetailPage.tsx + CaseDossierHeader.tsx | **GAP** | Not present. Same gap as dossier header Dismiss pill. |
| "Investigation not found" serif-italic on no data | QaInvestigationDetailPage.tsx:31-37 | PASS | |

### Scenario: Patrol detail page

| Requirement | File:Line | Status | Notes |
|---|---|---|---|
| Mono eyebrow "QA Patrol" | QaPatrolDetailPage.tsx:251 | PASS | `<Eyebrow>QA Patrol</Eyebrow>` |
| H2 sans-500 22px title with started-at timestamp | QaPatrolDetailPage.tsx:252-254 | PASS | `text-[22px] font-medium` with `<Time>` |
| Mono caption: duration, status, sources, log_lookback | QaPatrolDetailPage.tsx:255-259 | PASS | All 4 fields concatenated |
| Findings: rule-separated rows, no table | QaPatrolDetailPage.tsx:124-139 | PASS | `divide-y` list |
| Finding row grid: mark / id+butler / 1fr summary / meta | QaPatrolDetailPage.tsx:94 | PASS | 4-column grid |
| "novel" badge or dedup_reason | QaPatrolDetailPage.tsx:63-82 | PASS | `DedupMark` component |
| Dispatch summary: rule-separated rows linking to investigations | QaPatrolDetailPage.tsx:273-285 | PASS | |
| "Patrol not found" serif-italic on missing | QaPatrolDetailPage.tsx:224-232 | PASS | |

### Scenario: Investigations list page

| Requirement | File:Line | Status | Notes |
|---|---|---|---|
| Sticky filter bar: state, severity, butler, time range | QaInvestigationsPage.tsx:97-280 | PASS | All 4 filters implemented |
| Rule-separated list of QaCaseSummary rows | QaInvestigationsPage.tsx:293-298 | PASS | Uses `<CaseList>` with `md:w-full` |
| Each row: severity glyph, short id, butler, headline, detected/age, PR dot | CaseList.tsx:50-77 | PASS | |
| Click navigates to /qa/investigations/:attemptId | QaInvestigationsPage.tsx:296 | PASS | `navigate()` on select |
| Empty list: serif-italic "Nothing matches." | QaInvestigationsPage.tsx:89-91 | PASS | |
| H1 font weight | QaInvestigationsPage.tsx:198 | **MINOR** | Uses `font-semibold` (600). Design language says "Display weight is 500, not 700." Should be `font-medium` (500). |

---

## Design Language Compliance

| Item | File:Line | Status | Notes |
|---|---|---|---|
| Fonts: Inter Tight / JetBrains Mono / Source Serif 4 | index.css:196-198 | PASS | All three family tokens declared and used |
| No new CSS tokens | All components | PASS | No new `--token` additions; existing tokens used |
| No card chrome / drop shadows / gradients / recharts | All QA components | PASS | |
| OKLCH hardcoded values in Tailwind arbitrary classes | EvidenceLog.tsx:81, ClaimAnchoredBlurb.tsx:61 | **MINOR** | Two `bg-[oklch(0.81_0.185_84_/_0.1x)]` values for evidence hover highlight. Not from token system. Could map to `bg-[color-mix(in_oklch,var(--severity-medium),transparent_85%)]` or a new `--qa-evidence-hover` token. |
| Tabular nums on numeric values | All components (tnum class) | PASS | |
| Em-dashes in prose copy (banned) | PRPanel.tsx:29 | **MINOR** | `"No PR — escalated to user."` contains em-dash. Source of truth conflict: spec.md itself specifies this string with an em-dash, but voice doctrine bans em-dashes in prose. |
| Voice doctrine: sentence case | All components | PASS | Labels use sentence/lower case consistently |
| Motion: transition-fast on hover states | Multiple components | PASS | `transition-colors duration-fast` used |

---

## Summary of Gaps

The following gaps require attention. None have been implemented.

### G11-GAP-1: Double-hash short_id prefix (bug)

- **Files:** `CaseDossierHeader.tsx:54`, `QaInvestigationDetailPage.tsx:25`
- **What is missing:** `short_id` from the API already includes `#` (e.g. `"#218"`). Both locations add another `#` prefix, producing `##218`. The component test uses `short_id: "mfg"` (no hash), masking the regression.
- **Fix:** Remove the explicit `#` prefix from both render sites: `{qaCase.short_id}` not `#{qaCase.short_id}`. Update atoms.test.tsx fixture to use `short_id: "#218"` and assert the rendered text is `#218` not `mfg`.

### G11-GAP-2: Retry and Dismiss pill buttons missing from dossier header

- **Files:** `CaseDossierHeader.tsx`, `QaInvestigationDetailPage.tsx`
- **What is missing:** Spec says "the existing Retry and Dismiss actions remain available as pill buttons in the dossier header." Neither the `Retry` pill (which should call `POST /api/healing/attempts/:id/retry`) nor the `Dismiss` pill (which should call `POST /api/qa/known-issues/:fingerprint/dismiss`) are implemented in any QA component. The backend endpoints and `useDismissQaIssue()` hook exist; only the UI wiring is missing for Dismiss. For Retry, both the frontend hook and the UI are missing.
- **Fix (Retry):** Add `useRetryHealingAttempt()` hook wrapping `POST /api/healing/attempts/:id/retry`. Wire a pill button in `CaseDossierHeader` that is visible for non-active states (`landed`, `escalated`).
- **Fix (Dismiss):** Wire `useDismissQaIssue()` to a pill button in `CaseDossierHeader` visible when no active dismissal exists.

### G11-GAP-3: Live clock missing from QaOverviewPage header

- **File:** `QaOverviewPage.tsx` (line 6 comment lists clock but no implementation)
- **What is missing:** Spec requires "clock (mono, HH:MM 24h, tabular nums)" in the page header. The `<Time mode="clock-24h-mono">` component already exists and implements a live-ticking 24h clock. It is simply not included in the `PageHeader` template.
- **Fix:** Add `<Time mode="clock-24h-mono" className="font-mono tnum text-[10px] text-muted-foreground" />` to the `PageHeader` component render output.

### G11-GAP-4: Port number missing from QaOverviewPage header caption

- **File:** `QaOverviewPage.tsx:142`
- **What is missing:** Spec says caption is "port :<N> · model <M> · patrol every <P>m". Current render is "model {model} · patrol every {patrolInterval}m". `port` is not included.
- **Fix:** Add port number to `QaSummaryData` API response and render it in the header caption. Alternatively, if port is not surfaced by the summary endpoint, the spec text "port :<N>" may require a new API field or can be documented as deferred.

### G11-GAP-5: PR title font size wrong in PRPanel

- **File:** `PRPanel.tsx:58`
- **What is missing:** Spec says "Sans-500 14 px PR title." Implementation uses `text-[17px]`.
- **Fix:** Change `text-[17px]` to `text-[14px]` on the PR title `<h3>`.

### G11-GAP-6: "Why this fix" uses sans not serif-italic in PRPanel

- **File:** `PRPanel.tsx:71`
- **What is missing:** Spec says "serif-italic 13 px line rendered from `investigation_notes.why_this_fix`." Implementation uses `font-sans text-sm leading-relaxed text-foreground` (sans, 14px, not italic).
- **Fix:** Change to `font-serif italic text-[13px] leading-relaxed text-foreground`.

### G11-GAP-7: PR footer timestamp uses smart mode not fixed HH:MM

- **File:** `PRPanel.tsx:85, 88`
- **What is missing:** Spec says "Mono footer caption with `opened <HH:MM>` and optional `· merged <HH:MM>`" implying a fixed 24h time format. Implementation uses `<Time value={pr.opened_at} mode="smart" />` which shows relative time ("4 minutes ago") for recent events.
- **Fix:** Change `mode="smart"` to `mode="absolute"` with `precision="time"` (or use `precision="time"` alone) so the footer always shows a fixed time like `opened 14:32`.

### G11-GAP-8: PR state chip missing "rejected" variant — RESOLVED (spec corrected)

- **Files:** `PRPanel.tsx:18-23`, `frontend/src/api/types.ts` (QaPrSummary.state)
- **What was noted:** Spec listed state chip variants as "drafted/open/merged/closed/rejected". `QaPrSummary.state` type in types.ts only covered 4 values (`drafted|open|merged|closed`). Backend API also had no "rejected" state.
- **Resolution:** "rejected" is not a real GitHub PR state and the backend `_pr_state_for_case()` function (`src/butlers/api/routers/qa.py:831`) only produces `drafted|open|merged|closed`. The spec's own API scenario (line 143) already listed only 4 values. The "rejected" entry in the state chip list was a spec authoring error (copy-paste from GitHub's PR state vocabulary, which uses `rejected` in review contexts but not for PR merge state). The fix is to remove "rejected" from the spec rather than add it to the implementation. Spec corrected in `openspec/changes/redesign-qa-dossier/specs/qa-dashboard/spec.md`. Tests added for all 4 valid states in `fix.test.tsx`.

### G11-GAP-9: EvidenceLog multi-claim hover only highlights first claim

- **File:** `EvidenceLog.tsx:86, 89`
- **What is missing:** When an evidence row is linked to multiple claims (e.g. c1 and c2), hovering the row only broadcasts `myClaims[0]` as the hover state. Only segments for the first claim are highlighted in `ClaimAnchoredBlurb`; other linked claims' segments stay un-highlighted.
- **Fix:** Either: (a) broadcast all linked claims as a set `onRowHover(myClaims)` and update `hoveredClaim` state type to `string[] | null`, or (b) iterate and highlight all claims whose `evidence_ids` include the hovered row.

### G11-GAP-10: KPI sub-labels lack period-over-period comparison data

- **File:** `QaKpiStrip.tsx:46, 58`
- **What is missing:** Spec example sub-labels are "+2 vs prior 24h", "−12m vs 7d", "+4pp vs prior week" implying prior-period comparison values. The API `QaKpiBlock` type has no prior-period fields. Current implementation shows "24h window" and "7d window" as static strings.
- **Fix:** Extend `QaKpiBlock` (backend + frontend type) with prior-period comparison fields (`prs_landed_prior_24h`, `mttr_prior_7d_seconds`, `self_resolved_prior_7d_pct`). Compute them in `src/butlers/api/routers/qa.py` inside the summary handler and render the deltas in `QaKpiStrip`.

---

## Minor Issues (Below Gap Threshold)

These do not require separate beads but should be fixed alongside related gap work:

1. **EvidenceLog column order** (`EvidenceLog.tsx:80`): Claim `[N]` column appears first (20px) but spec lists it last after `msg`. Cosmetic; functionally equivalent.
2. **Dead `opened` entry in PatrolJournal** (`PatrolJournal.tsx:18`): `opened` is in `stepClassName` but is not a valid `QaJournalEvent.step` value per D1 schema check constraint. Should be removed to prevent code drift.
3. **QaInvestigationsPage H1 font weight** (`QaInvestigationsPage.tsx:198`): Uses `font-semibold` (600); design language says display weight is 500. Should be `font-medium`.
4. **OKLCH hardcoded values** (`EvidenceLog.tsx:81`, `ClaimAnchoredBlurb.tsx:61`): Hover highlight uses `bg-[oklch(0.81_0.185_84_/_0.1x)]` arbitrary Tailwind values outside the token system. Consider a named token or CSS `color-mix` against `--severity-medium`.
5. **Em-dash in PRPanel no-PR copy** (`PRPanel.tsx:29`): "No PR — escalated to user." The spec.md itself contains this em-dash. Voice doctrine bans em-dashes in prose. Should be "No PR. Escalated to user." — requires a spec correction in addition to the component fix.
6. **`formatQaDetectedTime` not using `<Time>` primitive** (`utils.ts:9-13`, `PatrolJournal.tsx:24-28`): Two inline `toLocaleTimeString()` calls. Design doctrine says all timestamps should use `<Time>`. Low urgency (output is correct) but creates drift from the `<Time>` contract.
7. **Frontend headline fallback** (`CaseList.tsx:64`, `CaseDossierHeader.tsx:79`): Shows "Untitled QA case" when headline is null. Backend `headline_for_case()` always returns a non-null string so this code path is unreachable in production. The fallback copy should at minimum match the spec's intended fallback (`event_summary`) in case the null path is ever reached.

---

## Test Coverage Note

| Component | Test file | Coverage |
|---|---|---|
| QaKpiStrip | atoms.test.tsx | null MTTR, active breakdown, cell labels |
| CaseList | atoms.test.tsx | row selection, severity glyph |
| CaseDossierHeader | atoms.test.tsx | dismissal caption, remove-dismissal pill |
| StateTrack | atoms.test.tsx | escalated variant |
| ClaimAnchoredBlurb | diagnosis.test.tsx | hover highlight, claim numbers |
| EvidenceLog | diagnosis.test.tsx | hover highlight, level colors |
| CounterEvidence | fix.test.tsx | empty state, row rendering |
| PRPanel | fix.test.tsx | null-PR message, field rendering |
| DiffPreview | fix.test.tsx | kind classification, empty state |
| PatrolJournal | dossier.test.tsx | step colors |
| CaseDossier | dossier.test.tsx | full/in-flight composition, hover lift |

No test currently validates the `short_id` double-hash bug (G11-GAP-1) because test
fixtures use hashes-less short IDs while the production API returns `"#NNN"` values.
