## 1. Spec Authoring

- [x] 1.1 Create `openspec/changes/redesign-detail-page-tab-vocabulary/` with
      the default spec-driven OpenSpec schema.
- [x] 1.2 Write `proposal.md` citing Gate B bead `bu-41p8z` and chosen option
      B2.
- [x] 1.3 Write `design.md` with the operator/resident mode decisions,
      deep-link behavior, conditional-tab rule, and Models-tab treatment.
- [x] 1.4 Write the `dashboard-butler-management` delta spec for the tab
      vocabulary requirements.
- [x] 1.5 Run `openspec validate redesign-detail-page-tab-vocabulary --strict`
      and confirm it passes.

## 2. Frontend Implementation

- [ ] 2.1 Split butler detail base-tab configuration into resident and operator
      tab sets.
- [ ] 2.2 Make resident mode the default and persist the selected mode in
      `localStorage` under `butlers:detail:mode`.
- [ ] 2.3 Add an accessible operator/resident toggle in the detail page shell
      actions or an equivalent page-level control.
- [ ] 2.4 Render resident mode with Overview, Activity, Logs, Approvals, Spend,
      Config, and Memory.
- [ ] 2.5 Render operator mode with Overview, Sessions, Config, Skills,
      Schedules, Trigger, MCP, State, CRM, and Memory.
- [ ] 2.6 Keep the current Models tab out of resident mode and handle it as an
      operator-only extension while the code exposes it.
- [ ] 2.7 Preserve switchboard Routing Log and Registry, health Health,
      general Collections and Entities, and education Reviews as conditional
      tabs in both modes.
- [ ] 2.8 Auto-promote to operator mode when `?tab=` targets an operator-only
      tab, including Models while exposed.
- [ ] 2.9 Keep invalid `?tab=` values falling back to Overview without forcing
      operator mode.
- [ ] 2.10 Switch to resident mode when `?tab=` targets a resident-only tab
      while stored mode is operator.

## 3. Verification

- [ ] 3.1 Add or update React tests for default resident mode, operator mode,
      localStorage persistence, and the mode toggle.
- [ ] 3.2 Add or update React tests for deep-link auto-promotion to
      operator-only tabs and resident-only tab resolution from stored operator
      mode.
- [ ] 3.3 Add or update React tests proving conditional tabs remain visible in
      both modes.
- [ ] 3.4 Add or update React tests for the current Models tab behavior while it
      remains exposed.
- [ ] 3.5 Run the targeted frontend test file for `ButlerDetailPage`.
