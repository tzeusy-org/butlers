## Context

`scheduled_tasks.source='toml'` is configuration authority; `source='db'` is
the owner-controlled runtime surface. The dashboard currently sends a toggle
request without a desired state and optimistically flips its cached row, while
the named MCP action is absent from the scheduling registration.

## Decisions

### D1: Requested state, not an implicit flip, is canonical

The dashboard and MCP callers must send an explicit `enabled: bool`; omission
and non-boolean values are rejected before a row is changed. The butler locks
the target row, rejects non-runtime sources, and updates `enabled` plus
`next_run_at` in one transaction.
Repeated requests for the same desired state return `status='unchanged'` and
`outcome='already_requested'`; they do not schedule a second transition.

### D2: Refusals are bounded and typed

The action returns `SCHEDULE_NOT_FOUND`, `SCHEDULE_TOML_MANAGED`, or
`SCHEDULE_MANAGED` without prompt, job-argument, or other runtime payload.
The API maps these to 404 or 409 `ErrorResponse` envelopes. Transport failures
remain the existing unavailable path.

### D3: Success carries only safe server evidence

Successful results include requested and observed booleans, whether a change
landed, the next-run projection, and `{action, result, target}` audit evidence.
The dashboard records a safe summary containing schedule identity and those
control-plane fields, never schedule prompt or job arguments. Its audit row
attributes the authenticated owner as actor; butler and schedule are target
context, separate from the actor. A refusal records its bounded code.

### D4: The UI is honest-pending

The schedule list does not optimistically flip. Each row's control stays
disabled until its own action settles, even when another row settles first.
On success the query is invalidated and the
tab renders the server-observed state with the `schedule.toggle` receipt; on a
typed refusal it renders the API error and makes no success claim.

## Rollback

Reverting the implementation and this delta removes the action and restores
the previous (non-functional) control. No database migration or data rollback
is needed.
