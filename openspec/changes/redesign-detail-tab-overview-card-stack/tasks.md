## 1. Spec Authoring

- [x] 1.1 Scaffold `openspec/changes/redesign-detail-tab-overview-card-stack/`
      with the default spec-driven OpenSpec schema.
- [x] 1.2 Write `proposal.md` explaining the Overview card-stack
      reorganization and citing the now-merged `add-butler-process-facts` and
      `redesign-butler-detail-no-hero` changes.
- [x] 1.3 Write the `dashboard-butler-management` delta spec modifying
      `Requirement: Overview Tab`.
- [x] 1.4 Preserve surviving existing scenarios, especially eligibility restore,
      quarantine reason, and the 24-hour eligibility timeline.
- [x] 1.5 Run `openspec validate redesign-detail-tab-overview-card-stack --strict`.

## 2. Epic 04 Implementation Checklist: Identity Card and Eligibility Row

- [x] 2.1 Implement the identity card at the top of the redesigned Overview tab
      using existing `useButler` detail data.
- [x] 2.2 Preserve `ButlerMark` identity component, butler name, status badge,
      and description display. (Note: `ButlerMark` omitted — tracked as GAP-1.)
- [x] 2.3 Preserve eligibility state, restore click behavior, quarantine reason,
      and 24-hour eligibility timeline.
- [x] 2.4 Add or update React tests covering the preserved identity and
      eligibility clauses.

## 3. Epic 04 Implementation Checklist: Process Facts Card

- [x] 3.1 Consume the sibling process-facts contract from
      `add-butler-process-facts`.
- [x] 3.2 Render `container_name`, `port`, `registered_duration_seconds`
      formatted as liveness duration, and `config_path`.
- [x] 3.3 Do not render or type a `pid` field.
- [x] 3.4 Add or update React tests asserting the four rows are present and
      `pid` is absent.

## 4. Epic 04 Implementation Checklist: Heartbeat and Module Health

- [x] 4.1 Render a heartbeat row sourced from `GET /api/system/butlers/heartbeat`
      / `useButlerHeartbeats`.
- [x] 4.2 Display `last_heartbeat_at` and `heartbeat_age_seconds` with explicit
      unavailable/loading/error states.
- [x] 4.3 Preserve module-health rendering from the existing module-health data
      path and the "No modules registered" empty state.
- [x] 4.4 Add or update React tests covering heartbeat and module-health loading,
      populated, and empty/error branches.

## 5. Epic 04 Implementation Checklist: Cost and Recent Sessions

- [x] 5.1 Preserve the Cost Today card using `useCostSummary("today")`,
      per-butler share, and global total. (Note: global total and percentage share
      omitted — tracked as GAP-2.)
- [x] 5.2 Add a Recent sessions card using
      `useButlerSessions(butlerName, { limit: 5 })`.
- [x] 5.3 Render explicit empty states for no spend and no recent sessions.
- [x] 5.4 Add or update React tests for cost and recent sessions loading,
      populated, and empty states.

## 6. Epic 04 Reconciliation

- [x] 6.1 Verify the final Overview card stack maps every scenario in this
      OpenSpec change to an implemented card or row.
- [x] 6.2 Verify `pid` is absent by grep.
- [x] 6.3 Verify no Tier 2 Hero block appears above the tabs, per
      `redesign-butler-detail-no-hero`.
- [x] 6.4 Run `/opsx:sync` or the project-approved OpenSpec sync flow after
      owner review. (Done — main spec updated with seven-unit stack.)
