## 1. Spec Landing

- [x] 1.1 Create `dashboard-overview-triage-cockpit` as the page-level OpenSpec change.
- [x] 1.2 Reconcile the stale chart-first `dashboard-overview` contract through a MODIFIED/REMOVED delta.
- [x] 1.3 Define binding existing endpoint data sources for briefing, attention, KPIs, Operations, and Now.
- [x] 1.4 Run `openspec validate dashboard-overview-triage-cockpit --strict`.

## 2. Frontend Implementation Bead

- [ ] 2.1 Reconcile `frontend/src/pages/DashboardPage.tsx` to the `dashboard-overview-triage-cockpit` spec without adding a backend aggregation endpoint.
- [ ] 2.2 Rename or adapt the right-column sections so the user-facing headings are `Operations` and `Now`.
- [ ] 2.3 Implement stale/old issue summarization over the existing `Issue` payload from `GET /api/issues`.
- [ ] 2.4 Compose `Now` from existing approval, QA, notification, and recent activity sources without adding a backend aggregation endpoint.
- [ ] 2.5 Ensure all five surfaces have explicit empty, loading, and error states.
- [ ] 2.6 Preserve the six-field `GET /api/dashboard/briefing` public response contract.

## 3. Frontend Test Bead

- [ ] 3.1 Add/adjust `DashboardPage` tests for the cockpit hierarchy: briefing, Needs attention, KPI strip, Operations, Now.
- [ ] 3.2 Test promoted KPI derivation from `useButlers()` and `useApprovalMetrics()` data.
- [ ] 3.3 Test stale issue ordering/summarization for old unresolved issues.
- [ ] 3.4 Test `Now` rows for pending approvals, QA pressure, failed notifications, and recent activity when those sources report actionable state.
- [ ] 3.5 Test empty/loading/error state rendering for attention, Operations, and Now.
- [ ] 3.6 Assert no new dashboard aggregation endpoint client is introduced for the Overview.

## 4. Archive Readiness

- [ ] 4.1 After implementation and tests land, run `openspec validate dashboard-overview-triage-cockpit --strict`.
- [ ] 4.2 Archive the change so `openspec/specs/dashboard-overview/spec.md` is updated and the chart-first requirements are removed from mainline specs.
