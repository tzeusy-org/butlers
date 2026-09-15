## 1. Contract and regression coverage

- [x] 1.1 Add API behavior coverage for configured-empty, pending-actions-partial, and approval-rules-partial metrics responses.
- [x] 1.2 Add frontend behavior coverage for unavailable approval metrics, overview derivation, Sidebar, Settings, and promotion/suggestion reader retries.
- [x] 1.3 Verify the new OpenSpec delta against the approved availability contracts.

## 2. Vertical availability implementation

- [x] 2.1 Return family-specific degraded metrics metadata while retaining successful aggregate contributions.
- [x] 2.2 Type and centralize approval metrics availability checks in frontend API and hooks.
- [x] 2.3 Render named, retryable unavailable states in approvals, overview, dashboard, Sidebar, Settings, suggestions, and promotion readers.

## 3. Validation and delivery

- [x] 3.1 Run focused backend and frontend behavior tests plus OpenSpec validation.
- [x] 3.2 Run relevant formatting, lint, type, and diff checks.
- [x] 3.3 Review the scoped diff, commit, push, and open a non-draft PR.
