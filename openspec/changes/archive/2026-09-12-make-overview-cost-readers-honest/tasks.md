## 1. Regression coverage

- [x] 1.1 Add failing component tests for direct summary unavailability and successful zero-cost rendering.
- [x] 1.2 Add failing component tests for direct top-sessions unavailability and successful empty rendering.
- [x] 1.3 Add failing Overview integration tests proving direct query errors reach both readers.

## 2. Scoped implementation

- [x] 2.1 Pass explicit direct query-error state from `DashboardPage` without changing hooks or API behavior.
- [x] 2.2 Render direct unavailable states before CostWidget and TopSessionsTable calm branches while preserving compatibility degradation and daily/unpriced behavior.

## 3. Verification

- [x] 3.1 Run focused CostWidget, TopSessionsTable, and DashboardPage Vitest coverage.
- [x] 3.2 Run frontend lint, build, and em-dash gates.
- [x] 3.3 Validate the OpenSpec change strictly and review the final scoped diff.
