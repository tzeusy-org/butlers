## Why

The schedule table already offers a pause/resume control and the dashboard API
already names `schedule_toggle`, but that MCP action is not registered. The
button therefore cannot change a schedule and the existing optimistic cache
update can present a state that never landed. TOML-owned rows also need an
explicit refusal because their authority remains the checked-in configuration.

## What Changes

- Register a canonical `schedule_toggle` core action in the scheduling group.
- Accept an explicit requested `enabled` state and serialize the row transition
  so retries and concurrent same-state requests converge idempotently.
- Return bounded typed outcomes for missing, TOML-managed, and other managed
  schedules, plus a server-observed success receipt.
- Make the dashboard API pass the requested state through MCP, map refusals to
  typed HTTP errors, and record only safe control-plane audit evidence.
- Make `ButlerSchedulesTab` wait for the real response, refresh server truth,
  and show the observed state and audit action instead of claiming optimistic
  success.

## Non-Goals

No scheduler redesign, new schedule type, credential/runtime activation, live
schedule execution, or unrelated Calendar UX is part of this change.

## Impact

Implementation touches the core scheduler/tool registration, schedule API
models/router, schedule client/hook/tab, and behavior tests. The OpenSpec
delta updates the core scheduler CRUD contract and Calendar butler toggle
contract. No migration or live-data action is required.
