## Epic bu-hb7dh Implementation Children

### R1: Status-board archetype shell

- [ ] R1 `bu-hb7dh.R1` Add `<Page archetype="status-board">` shell to `frontend/src/components/ui/page.tsx`, including header strip (eyebrow + h1 + pill + clock+date), 4-column cell grid slot, footer KPI band slot, and the status-board skeleton (header line + 2x4 cell skeletons + footer band). This is the gate for all F-tasks.

### B1: Data aggregation hook

- [ ] B1 `bu-hb7dh.B1` Implement `useStatusBoard()` (or equivalent co-located hook) that composes `useButlers`, `useRegistry`, `useButlerHeartbeats`, `useCostSummary('today').by_butler`, and `useSessions({ since: 24h })` into a unified `ButlerCellProps[]` array sorted by `sessions_24h` descending then name ascending. Derives activity verb, load%, 24h activity stripe, and eligibility rail colour client-side. No new API calls.

### P1: OpenSpec change (this bead)

- [x] P1 `bu-hb7dh.1` Author this OpenSpec change for the `/butlers` status-board redesign so the spec gate is in place before implementation merges.

### F1: Page shell migration

- [ ] F1 `bu-hb7dh.F1` Replace the outer chrome in `frontend/src/pages/ButlersPage.tsx` with `<Page archetype="status-board">`. Remove the two-section group split. Pass `useStatusBoard()` result as the cell grid data. Wire the header strip healthy/total pill to the same data.

### F2, F3: Cell component and activity chip

- [ ] F2 `bu-hb7dh.F2` Implement `ButlerCell` component: `ButlerMark` + capitalized name + role tagline + activity chip + KPI quartet (sess·24h, spend, load, last) + 24h activity stripe pinned bottom + hover open-arrow affordance.
- [ ] F3 `bu-hb7dh.F3` Implement activity chip: derive verb (`running`/`idle`/`paused`/`awaiting`/`quarantined`) and rail color from `status`, `active_session_count`, and `eligibility_state` per the approved derivation in the proposal. No per-butler-name heuristics.

### F4: Footer KPI band

- [ ] F4 `bu-hb7dh.F4` Implement footer KPI band: active / paused / awaiting count badges + sessions·24h / spend·today / avg load KPIs + status-tone dots (only when count > 0) + composition addendum (Nb butlers, Ns staffers).

### F5, F6: Eligibility rail and click-to-restore

- [ ] F5 `bu-hb7dh.F5` Wire the cell's left-edge state rail to eligibility state: emerald for active, amber for stale, red for quarantined, dim for unavailable.
- [ ] F6 `bu-hb7dh.F6` Quarantined and stale chips remain click-to-restore via the existing `setEligibility` mutation. Unavailable registry rows render a dim `--` verb without hiding the cell.

### F7: Polling cadences

- [ ] F7 `bu-hb7dh.F7` Verify polling cadences: butler list 30s, registry/heartbeat 30s, cost 60s, clock ticks every 1s. Ensure no regressions in the stale-data banner or empty-state scenarios.

### Recon: Spec reconciliation

- [ ] Recon `bu-hb7dh.Recon` Reconcile final implementation against this OpenSpec change: verify no new `ButlerSummary` fields, no mock verbs (`patrol`/`consolidating`/`ingesting`), no hardcoded butler names in the grid path, and no raw hex or inline styles in cell JSX.
