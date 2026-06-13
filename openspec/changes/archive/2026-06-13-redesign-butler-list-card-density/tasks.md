## 1. Epic 05 Implementation Children

- [ ] 1.1 `bu-insd4.1` Replace `frontend/src/pages/ButlersPage.tsx` card markup with the denser Dispatch layout from `pr/overview/butlers-page.jsx:108-198`, adapted to existing `ButlerSummary` fields and the existing `ButlerMark` component.
- [ ] 1.2 `bu-insd4.2` Restrict the list to butlers returned by `GET /api/butlers`; remove or prevent hardcoded calendar, memory, and household references in the butler-list code path and fixtures.
- [ ] 1.3 `bu-insd4.3` Preserve the existing sort, empty, stale-error, and 30-second polling scenarios with focused RTL coverage, including fake-timer coverage for polling.
- [ ] 1.4 `bu-insd4.4` Reconcile the implementation against this OpenSpec delta, verify no new `ButlerSummary` fields were added, verify no nonexistent butler references remain, and run the a11y baseline.
