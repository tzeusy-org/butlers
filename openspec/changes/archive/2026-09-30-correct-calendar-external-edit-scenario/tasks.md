## 1. Spec

- [x] 1.1 Rewrite the "Butler overwrites external edits to butler-owned events" scenario to match provider sync and the outbound push.

## 2. Code and tests

- [x] 2.1 Correct the stale `_project_provider_changes` comment (comment-only).
- [x] 2.2 Test that provider sync persists butler-generated events with their metadata.
- [x] 2.3 Test that the outbound push overwrites an externally edited pushed scheduled task with local state.

## 3. Verification

- [x] 3.1 Run strict OpenSpec validation and the overwrite and archive guards.
