# Gen-1 Reconciliation: Overview Triage Cockpit

**Issue:** bu-tn1po.7  
**Auditor:** agent/bu-tn1po.7  
**Date:** 2026-05-16  
**Scope:** Full epic reconciliation — spec vs. final repository state after bu-tn1po.1 through bu-tn1po.6 merged.

---

## Summary of Sibling Beads

| Bead | Title | PR | Status |
|---|---|---|---|
| bu-tn1po.1 | Reconcile overview triage cockpit spec | #1646 | Closed |
| bu-tn1po.2 | Model overview triage data from existing hooks | #1656 | Closed |
| bu-tn1po.3 | Build recency-aware overview needs-attention list | #1676 | Closed |
| bu-tn1po.4 | Promote overview KPIs and butler activity index | #1675 | Closed |
| bu-tn1po.5 | Add overview operations-now signal list | #1689 | Closed |
| bu-tn1po.6 | Integrate triage cockpit into DashboardPage | #1691 | Closed |

---

## Hard-Cut Verification

**Editorial archetype preserved?** YES.
- `DashboardPage.tsx` wraps everything in `<Page archetype="editorial" title="Overview">`.
- No session stripe chart in any component under `frontend/src/components/overview/`.
- No card-grid chrome: all lists use hairline `1px solid var(--border)` row separators with no background fills.
- Two-column grid: `grid-cols-[1.4fr_1fr]` at `lg`, single-column below.

**No new backend aggregate endpoint?** CONFIRMED.
- `DashboardPage.tsx` header comment explicitly states: `Data sources (no backend aggregation endpoint required)`.
- Nine existing hooks used: `useBriefing`, `useIssues`, `useButlers`, `useCostSummary`, `useApprovalMetrics`, `useButlerHeartbeats`, `useNotificationStats`, `useQaSummary`, `useTimeline`.
- No new backend route was added for this epic.

**KPI strip hairline/tabular?** YES. `KpiStrip.tsx` uses `borderRight: "1px solid var(--border)"`, `fontFamily: "var(--font-sans)"`, `fontSize: "32px"`, tabular numbers via `className="tnum"`. No card chrome.

**Row-based attention and index lists?** YES. `AttentionList.tsx` uses `role="list"` with `gridTemplateColumns: "24px 1fr auto"` rows. `ButlerIndex.tsx` uses row grid `"16px minmax(0, 1fr) auto minmax(86px, auto)"`. `OperationsNowList.tsx` uses `gridTemplateColumns: "auto 1fr auto"` rows.

---

## Feature-by-Feature Reconciliation

### Feature 1: Promoted Runtime KPIs

Spec requirement: four cells — `Total butlers` (type=butler count), `Healthy` (ok/online count), `Sessions · 24h` (sum of sessions_24h), `Pending approvals` (total_pending from approvals/metrics).

| Scenario | Status | File:Line | Notes |
|---|---|---|---|
| Four cells in spec-approved order | PASS | RuntimeSummaryKpi.tsx:16-33 | Order: Total butlers → Healthy → Sessions · 24h → Pending approvals |
| `Total butlers` = type==="butler" count only | PASS | model.ts:119, 189 | `.filter((butler) => butler.type === "butler")` before counting |
| `Healthy` = ok or online status | PASS | model.ts:104, 191 | `HEALTHY_STATUSES = new Set(["ok", "online", "healthy"])` |
| `Sessions · 24h` = sum of sessions_24h | PASS | model.ts:192 | `reduce((sum, butler) => sum + (butler.sessions_24h ?? 0), 0)` |
| `Pending approvals` = total_pending | PASS | model.ts:193 | `input.approvalMetrics?.total_pending ?? 0` |
| Loading state renders "—" for all cells | PASS | RuntimeSummaryKpi.tsx:19-32 | `isLoading ? "—" : value` |
| Partial failure degrades only affected cells | PASS | RuntimeSummaryKpi.tsx:31-32 | `pendingApprovalsAvailable` prop gates approval cell |
| Hairline strip, no card chrome | PASS | KpiStrip.tsx:37-49 | Borderless cells except hairline right-side dividers |
| Tests cover KPI values and failure states | PASS | RuntimeSummaryKpi.test.tsx | Five tests covering order, values, zero, partial failure, loading |

**Result: PASS — no gaps.**

---

### Feature 2: Recency-Aware `Needs Attention` Surface

Spec requirement: capped list, recency metadata (last_seen, occurrences), old issues summarized, link to `/issues`, empty state `Nothing waiting.`.

| Scenario | Status | File:Line | Notes |
|---|---|---|---|
| Issues bucketed into currentHigh / recent / old | PASS | model.ts:134, 243-270 | `bucketIssues()` separates by 24h recency and severity tier |
| Cap at `maxRecentIssueRows` (default 5) | PASS | model.ts:99, 141-146 | `DEFAULT_MAX_RECENT_ISSUE_ROWS = 5`, slice applied |
| Hidden groups summarized into one row with href=/issues | PASS | model.ts:163-177 | `kind: "old-issues-summary"`, `href: "/issues"`, `count: N` |
| Row shows severity mark | PASS | AttentionList.tsx:31-43 | `severityGlyph()` maps to `!`, `~`, or `·` with color |
| Row shows title, detail, butler names | PASS | model.ts:316-337 | `humanButlerNames()` produces readable list |
| Row shows occurrences when > 1 | PASS | model.ts:321-323 | `"${issue.occurrences} occurrences"` appended |
| Row shows last-seen/first-seen recency | PASS | model.ts:324-326, 525-533 | `issueRecencyDetail()` returns `"last seen Xm ago"` etc. |
| `lastSeenAt` field surfaced on row | PASS | model.ts:333 | `lastSeenAt: issue.last_seen_at ?? null` |
| Empty state: `Nothing waiting.` serif italic | PASS | AttentionList.tsx:47-59 | Serif italic, muted, no illustration |
| Missing timestamps treated as current | PASS | model.ts:276 | `if (!timestamp) return true` → issue is current |
| Severity ordering: high/critical first | PASS | model.ts:282-284, 297-309 | `compareIssues()` with `issueSeverityRank()` |
| Within severity, older first | PARTIAL | model.ts:286-294 | **Note:** spec (D4) says "older unresolved issues sort before newer" within a tier; implementation sorts by `last_seen_at ?? first_seen_at` descending (newer first within a tier). The `compareIssues` function returns `timeB.localeCompare(timeA)` (B-A = descending = newer first). The spec intent was to surface stalest issues first. **Minor drift — not a regression blocker.** |
| Attention rows include non-issue signals | PASS | model.ts:135-139 | Runtime, approval, notification, QA rows mixed in |
| Non-issue signals appear after critical issues | PASS | model.ts:153-161 | Order: currentHighIssues → runtime → approval → notification → qa → recentIssues |
| Tests cover recency, multi-butler, summarization, timestamps | PASS | model.test.ts | 12 tests covering all scenarios |

**Result: MOSTLY PASS — one minor drift on within-severity sort direction.**

**Gap G1:** Within-severity issue ordering is newer-first (descending by last_seen), but spec D4 says "older unresolved issues before newer issues when `first_seen_at` exists". The implementation effectively shows the most recently active issue first within a severity tier, which may be more operator-useful in practice, but diverges from the spec text. File: `model.ts:292-294`.

---

### Feature 3: Current Operational Signals Beyond Issues

Spec requirement: butler/session status, pending approvals, QA status, failed notification pressure, recent activity — all as `Now` rows.

| Scenario | Status | File:Line | Notes |
|---|---|---|---|
| Pending approvals row when total_pending > 0 | PASS | model.ts:413-424 | `kind: "approval"`, `href: "/approvals"` |
| QA patrol failure surfaces with severity=high | PASS | model.ts:470-476 | `last_patrol.status === "failed"` → title `"QA patrol failed"`, `severity: "high"` |
| QA novel findings surfaces in Now | PASS | model.ts:489-496 | `stats_24h.novel_findings > 0` → row emitted |
| QA dispatched investigations surfaces in Now | PASS | model.ts:479-487 | `dispatched_investigations > 0` → row emitted |
| Failed notification pressure in Now | PASS | model.ts:437-450 | `stats.failed > 0` → `kind: "notification"`, `href: "/notifications"` |
| Recent activity from timeline in Now | PASS | model.ts:451-462 | Up to `maxTimelineRows` (default 2) timeline events appended |
| `Nothing scheduled.` empty state | PASS | OperationsNowList.tsx:35-47 | Serif italic, no listitem rendered |
| Section eyebrow labelled "Now" | PASS | OperationsNowList.tsx:34 | `<Section eyebrow="Now">` |
| QA clean state is quiet (no row) | PASS | model.ts:500 | `return null` when no QA condition met |
| Zero approvals produce no row | PASS | model.ts:412-413 | Guard `if (pendingApprovals <= 0) return []` |
| Tests for all four signal kinds | PASS | OperationsNowList.test.tsx | 9 tests covering zero/nonzero approvals, QA, notif, activity, multi-row |
| Source error states | PARTIAL | DashboardPage.tsx:81-84 | **Minor:** spec D7 says source failures should render as local error rows so the owner can see which signal is unavailable. For Now rows, the page guards against `isError` but falls back silently to null input → the `OperationsNowList` receives empty rows and shows `Nothing scheduled.` rather than a named error row. No distinct "notification source unavailable" or "QA source unavailable" row is produced. |

**Result: MOSTLY PASS — source failure visibility for Now rows is suppressed rather than named.**

**Gap G2:** When a `Now` source (notifications, QA, timeline) fails, `DashboardPage` passes `null` to the model derivation via the `isError ? null : ...` guard. The model returns no row for the missing signal. Spec D7 requires a "local error row" so the owner sees which source is unavailable. Current behavior silently omits the row. Affects `notificationStatsQuery`, `qaSummaryQuery`, `timelineQuery` failure paths. File: `DashboardPage.tsx:81-84`.

---

### Feature 4: Enriched Right-Column Operations / Butler Activity Index

Spec requirement: butler scan list, sessions_24h, today's cost, last activity from heartbeat, runtime state (active/stale/degraded/offline).

| Scenario | Status | File:Line | Notes |
|---|---|---|---|
| Section eyebrow labelled "Operations" | PASS | ButlerIndex.tsx:11 | `<Section eyebrow="Operations">` |
| Only `type === "butler"` rows shown | PASS | model.ts:119 | `.filter((butler) => butler.type === "butler")` |
| Per-row: butler name | PASS | ButlerIndex.tsx:37 | `{butler.name}` rendered |
| Per-row: sessions_24h count | PASS | ButlerIndex.tsx:63-73 | `aria-label` and value rendered |
| Per-row: today's cost from by_butler | PASS | ButlerIndex.tsx:57-58 | `formatCost(butler.costUsd) today` when costUsd > 0 |
| Per-row: last activity from heartbeat | PASS | ButlerIndex.tsx:117-125 | Prefers `lastSessionAt` → heartbeat age → "no session" |
| Per-row: runtimeState derived from heartbeat | PASS | model.ts:201-241 | States: healthy/active/stale/degraded/offline/unknown |
| Stale detection from heartbeat age | PASS | model.ts:211-212 | `heartbeatAgeSeconds > staleHeartbeatSeconds` (default 5m) |
| Missing heartbeat when source loaded = offline | PASS | model.ts:210 | `isMissingHeartbeat = heartbeatSourceLoaded && heartbeat == null` → `offline` |
| `needsAttention` propagates stale/degraded/offline | PASS | model.ts:236-239 | `needsAttention` flag drives attention row injection |
| Empty state: `No butlers active.` | PASS | ButlerIndex.tsx:88-100 | Rendered when `butlers.length === 0` |
| Per-butler mark via `ButlerMark` | PASS | ButlerIndex.tsx:28 | `<ButlerMark name={butler.name} tone="neutral" />` |
| Missing cost renders as zero, not hidden | PASS | model.ts:129 | `input.costs?.by_butler?.[butler.name] ?? 0` — zero default |
| Tests cover cost, stale heartbeat, active sessions, null last-session | PASS | model.test.ts:368-480 | Four tests covering enriched butler index metadata |

**Result: PASS — no gaps.**

---

### Feature 5: Preservation of Editorial Archetype

Spec requirement: briefing + system voice, row-based attention/index lists, hairline KPI strip, NO card-heavy regression.

| Criterion | Status | File:Line | Notes |
|---|---|---|---|
| `<Page archetype="editorial">` wrapping | PASS | DashboardPage.tsx:94 | `<Page archetype="editorial" title="Overview">` |
| Two-column grid: 1.4fr / 1fr at lg | PASS | DashboardPage.tsx:103 | `lg:grid-cols-[1.4fr_1fr]` |
| Responsive: single column below lg | PASS | DashboardPage.tsx:103 | `grid gap-8 items-start lg:gap-14 lg:grid-cols-[...]` |
| Left column: DateEyebrow + BriefingStatus | PASS | DashboardPage.tsx:111-120 | `DateEyebrow` with `statusSlot=<BriefingStatus>` |
| Left column: Display headline (44px sans-500) | PASS | Headline.tsx:26-27 | `fontSize: "44px"`, `fontWeight: 500`, `letterSpacing: "-0.025em"` |
| Left column: Voice elaboration paragraph | PASS | Elaboration.tsx | Serif 16px, muted, opacity cross-fade on fetch |
| Left column: Section "Needs attention" → AttentionList | PASS | DashboardPage.tsx:128-130 | `<Section eyebrow="Needs attention">` |
| Left column: KPI strip below attention | PASS | DashboardPage.tsx:132-136 | `<RuntimeSummaryKpi>` after Section |
| Right column: ButlerIndex ("Operations") | PASS | DashboardPage.tsx:144 | `<ButlerIndex butlers={model.operationsRows} />` |
| Right column: OperationsNowList ("Now") | PASS | DashboardPage.tsx:145 | `<OperationsNowList rows={model.nowRows} />` |
| No session stripe chart anywhere | PASS | all overview components | No Recharts or chart imports in any overview file |
| No card-grid chrome | PASS | all overview components | No shadcn `Card` component or equivalent background-fill wrappers |
| Hairline borders only | PASS | AttentionList, ButlerIndex, OperationsNowList | All use `1px solid var(--border)` row separators |
| Briefing fallback path: greet/headline/elaboration | PASS | DashboardPage.tsx:87-91 | Safe fallback strings for all three briefing fields |
| BriefingStatus pill: composing/llm/templated states | PASS | BriefingStatus.tsx:44-53 | Three states correctly derived |
| Section eyebrow mono uppercase component | PASS | Section.tsx:17-31 | `font-mono 10px uppercase letterSpacing 0.14em` |

**Result: PASS — editorial archetype fully preserved.**

---

### Feature 6: No New Backend Aggregate Endpoint

| Check | Status | Evidence |
|---|---|---|
| No new `/api/dashboard/overview` or equivalent route | PASS | DashboardPage.tsx header comment confirms; `grep -r "dashboard/overview" src/` returns no new API calls |
| All nine data hooks use pre-existing endpoints | PASS | `useBriefing`, `useIssues`, `useButlers`, `useCostSummary`, `useApprovalMetrics`, `useButlerHeartbeats`, `useNotificationStats`, `useQaSummary`, `useTimeline` — all existed before this epic |
| `useTimeline` used instead of introducing `/api/sessions` aggregation | PASS | DashboardPage.tsx:72 `useTimeline({ limit: 5 })` |
| design.md D3 rationale documented | PASS | design.md §D3 — existing endpoint table justifies each surface |

**Result: PASS — no new backend endpoint was introduced.**

---

## Gap Summary

| ID | Severity | Description | File | Spec Reference |
|---|---|---|---|---|
| G1 | Low | Within-severity issue ordering is newer-first (descending by last_seen), but spec D4 says "older unresolved issues before newer issues when first_seen_at exists". Behaviorally more operator-useful but technically drifts from spec text. | `model.ts:292-294` | design.md §D4 |
| G2 | Low | Now source failures (notificationStats, qaSummary, timeline) silently omit the row rather than rendering a named error row. Spec D7 says failures render as "local Now error rows so the owner can see which signal is unavailable." | `DashboardPage.tsx:81-84` | design.md §D7, spec §Now: empty/loading/error states |

No critical or high-severity gaps. Both gaps are presentation/error-visibility concerns, not data correctness failures.

---

## Conclusion

The repository final state after bu-tn1po.1 through bu-tn1po.6 substantially matches the proposed triage cockpit shape:

- **Editorial archetype**: preserved without regression. No cards, no chart.
- **KPI strip**: four cells at hairline, exact spec-approved meanings, correct sources, loading/failure degradation.
- **Needs attention**: capped, recency-aware, old-issue summarization, link to /issues, correct empty state — all implemented and tested.
- **Operations**: butler index enriched with heartbeat-derived runtime state, cost, last activity, attention propagation.
- **Now**: approval, QA, notification, and timeline rows implemented with compact zero state.
- **No new backend endpoint**: confirmed.

Two minor gaps remain (G1: sort direction within a severity tier; G2: Now source error visibility). Neither blocks archetype integrity. Both are candidates for follow-up beads at coordinator discretion.
