# Approval delivery reason vocabulary: owner_mode and action_cancelled

## Why

The closed delivery reason vocabulary in REQ-approval-delivery-intent-recovery-002 requires an
explicit revision for every new code. `action_cancelled` shipped with approvals_022 and the
worker without a spec revision, and an owner-mode hold (bu-dhloi1.22.1, approvals_024) needs its
own `owner_mode` reason rather than reusing `quiet_hours`.

## What Changes

- Add `action_cancelled` and `owner_mode` to the closed vocabulary.
- Engineering vocabulary only: no behavior, authority or owner policy changes.

## Capabilities

### Modified Capabilities

- `approval-delivery-intent-recovery`: the closed reason vocabulary.

## Impact

Matches `SAFE_REASON_CODES` and the approvals_022 and approvals_024 CHECK constraints.
