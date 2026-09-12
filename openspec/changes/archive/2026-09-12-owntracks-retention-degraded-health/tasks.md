## 1. Retention Health Contract

- [x] 1.1 Add failing focused OwnTracks tests for consecutive purge failures, sanitized degradation, reset on success, and existing error priority.
- [x] 1.2 Add the minimal process-local retention failure streak and sanitized accessor.
- [x] 1.3 Route retention degradation through the existing OwnTracks health callback without changing heartbeat or endpoint schemas.

## 2. Verification

- [x] 2.1 Run the focused OwnTracks tests through red-green-refactor and confirm the regression cases pass.
- [x] 2.2 Run scoped Ruff check and format verification plus strict OpenSpec validation.
