## 1. Timeline metadata and pagination truthfulness

- [x] 1.1 Add RED API/client behavior coverage for additive named failed-butler
  metadata while retaining generic source metadata.
- [x] 1.2 Add RED ledger behavior coverage for retaining the snapshot/cursor
  and retrying the identical cursor after an older-page failure.
- [x] 1.3 Implement the additive metadata and retryable pagination state with
  no fan-out, endpoint, cursor-format, or registry changes.

## 2. Timeline reader failures

- [x] 2.1 Add RED page and accessibility coverage for named unavailable
  butler facets and saved views with native retry controls.
- [x] 2.2 Add RED ledger coverage for a visible older-page failure and retry.
- [x] 2.3 Implement the bounded page/ledger states while preserving useful
  rows, built-in controls, and current filters.

## 3. Pinned error excerpts

- [x] 3.1 Add RED `SessionsPinnedStrip` coverage for loading, unavailable
  row-local retry, and loaded-null detail states.
- [x] 3.2 Implement discriminated per-detail query state and accessible,
  non-nested retry controls.

## 4. Verification

- [x] 4.1 Run focused backend/frontend behavior tests, strict OpenSpec
  validation, formatter/lint/type checks, and `git diff --check`.
- [x] 4.2 Review every changed path against the explicit Sessions,
  fan-out, degraded-envelope/registry, and migration fences before handoff.
