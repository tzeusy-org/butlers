## Epic bu-hb7dh Implementation Children

### R1: Status-board archetype shell

- [x] R1 `bu-hb7dh.3` Add `<Page archetype="status-board">` shell to `frontend/src/components/ui/page.tsx`, including header strip (eyebrow + h1 + pill + clock+date), 4-column cell grid slot, footer KPI band slot, and the status-board skeleton (header line + 2x4 cell skeletons + footer band). Merged: PR #1526 (f1ffba86).

### B1: Data aggregation hook

- [x] B1 `bu-hb7dh.5` Implement `useButlerStatusBoard()` composite hook that composes `useButlers`, `useRegistry`, `useButlerHeartbeats`, `useCostSummary('today').by_butler`, and `useQuery(getSessions({ since: <ISO> }))` (rolling 24h window via the existing sessions endpoint) into a unified `StatusBoardRow[]` array sorted by `sessions_24h` descending then name ascending. Derives activity verb, load%, 24h activity stripe, and eligibility rail colour client-side. No new API calls. Merged: PR #1528 (ece542d1).

### P1: OpenSpec change (this bead)

- [x] P1 `bu-hb7dh.1` Author this OpenSpec change for the `/butlers` status-board redesign so the spec gate is in place before implementation merges. Merged: e1b2b7c4.

### F1: Page shell migration

- [x] F1 `bu-hb7dh.8` Replace the outer chrome in `frontend/src/pages/ButlersPage.tsx` with `<Page archetype="status-board">`. Remove the two-section group split. Pass `useButlerStatusBoard()` result as the cell grid data. Wire the header strip healthy/total pill to the same data. Merged: PR #1534 (e818ad90).

### F2, F3: Cell component and activity chip

- [x] F2 `bu-hb7dh.6` Implement `StatusBoardCell` component: `ButlerMark` + capitalized name + role tagline + activity chip + KPI quartet (sess·24h, spend, load, last) + 24h activity stripe pinned bottom + hover open-arrow affordance. Merged: PR #1532 (bd291cdf).
- [x] F3 `bu-hb7dh.6` Implement activity chip: derive verb (`running`/`idle`/`paused`/`awaiting`/`quarantined`) and rail color from `status`, `active_session_count`, and `eligibility_state` per the approved derivation in the proposal. No per-butler-name heuristics. Merged: PR #1532 (bd291cdf).

### F4: Footer KPI band

- [x] F4 `bu-hb7dh.7` Implement footer KPI band (`BoardFooter`): active / paused / awaiting count badges + sessions·24h / spend·today / avg load KPIs + status-tone dots (only when count > 0) + composition addendum (Nb butlers, Ns staffers). Merged: PR #1531 (c59cce9b).

### F5, F6: Eligibility rail and click-to-restore

- [x] F5 `bu-hb7dh.6` Wire the cell's left-edge state rail to eligibility state: emerald for active, amber for stale, red for quarantined, dim for unavailable. Merged: PR #1532 (bd291cdf).
- [x] F6 `bu-hb7dh.8` Quarantined and stale chips remain click-to-restore via the existing `setEligibility` mutation. Unavailable registry rows render a dim `--` verb without hiding the cell. Merged: PR #1534 (e818ad90).

### F7: Polling cadences

- [x] F7 `bu-hb7dh.8` Verify polling cadences: butler list 30s, registry/heartbeat 30s, cost 60s, header clock updates every minute via `<Time mode="clock-24h-mono">` (60s interval, aligned to minute boundaries). Ensure no regressions in the stale-data banner or empty-state scenarios. Merged: PR #1534 (e818ad90).

### Recon: Spec reconciliation

- [x] Recon `bu-hb7dh.9` Reconcile final implementation against this OpenSpec change: verify no new `ButlerSummary` fields, no mock verbs (`patrol`/`consolidating`/`ingesting`), no hardcoded butler names in the grid path, and no raw hex or inline styles in cell JSX. All scenarios covered; full compliance matrix in docs/reports/butlers-status-board.md.
