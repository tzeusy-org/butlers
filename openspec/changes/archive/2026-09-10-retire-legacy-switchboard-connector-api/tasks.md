## 1. Canonical API migration

- [x] 1.1 Add canonical ingestion detail, statistics, and settings routes with
      focused behavior tests for the existing detail-page contract.
- [x] 1.2 Migrate every frontend connector list, detail, statistics, and
      settings caller to the canonical ingestion namespace.
- [x] 1.3 Remove the full legacy Switchboard connector route family and its
      obsolete client adapters, models, helpers, and tests.

## 2. Contract hygiene

- [x] 2.1 Remove the duplicate ingestion-router mount and prove OpenAPI has no
      duplicate operation IDs.
- [x] 2.2 Add the OpenAPI/source-level proof that no live legacy connector
      route remains, and update E2E interception fixtures.
- [x] 2.3 Reconcile current specs, active-change references, and API docs with
      the canonical ingestion namespace.

## 3. Verification and delivery

- [x] 3.1 Run focused backend, frontend, E2E-stub, and OpenAPI contract tests.
- [x] 3.2 Run the dirty-worktree test planner and required cheap guards, then
      validate the OpenSpec change strictly and re-scan for retired paths.
