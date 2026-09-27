# Tasks

## 1. Core scheduler and API

- [x] 1.1 Register `schedule_toggle` in the canonical scheduling tool group.
- [x] 1.2 Persist explicit requested state under a row lock with idempotent
      same-state receipts and source restrictions.
- [x] 1.3 Return typed MCP refusals and typed dashboard HTTP errors, with safe
      server-derived audit evidence on success/failure.

## 2. Dashboard

- [x] 2.1 Send the requested state through the real toggle action.
- [x] 2.2 Remove optimistic success claims, retain pending state per row, and
      render observed state plus audit receipt after that row's mutation settles.

## 3. Verification

- [x] 3.1 Add scheduler, API, and ButlerSchedulesTab behavior tests covering
      registration, missing/managed refusals, idempotence/concurrency,
      observed UI receipt, and overlapping row completion/refusal order.
- [x] 3.2 Run targeted tests, test-plan, guards, OpenSpec strict validation,
      and the applicable frontend CI order before handoff.
