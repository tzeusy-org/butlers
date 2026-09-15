## Why

Messenger can park a routed outbound action whose stored `tool_name` and `tool_args`
do not match any executable delivery handler. Approval and Retry then reach the
daemon but fail before delivery, while the API misreports the handler failure as an
unreachable butler.

## What Changes

- Materialize a canonical native delivery command before inline approval gating and
  use that same command for immediate execution and deferred replay.
- Require authoritative provider thread identity before parking or executing an
  email reply; never substitute an internal request identifier.
- Persist exact registered-tool arguments for routed email, Telegram, and WhatsApp
  delivery paths, keeping routing provenance out of executable arguments.
- Distinguish transport unreachability from a reachable executor or tool rejection
  in both approval Retry endpoints without marking a failed action executed.
- Add behavior-executing regressions for park, approve, replay, and failure-state
  preservation. Historical malformed actions are not rewritten or auto-replayed.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `module-approvals`: Inline approval records become self-contained replay commands
  and failed replays preserve the approved, unexecuted state.
- `core-notify`: Routed outbound delivery uses one canonical native command mapping,
  including authoritative email thread identity.
- `dashboard-approvals`: Retry reports whether dispatch transport was unreachable or
  a reachable executor rejected the stored action.

## Impact

The change affects Messenger route execution and inline approval parking, approval
dispatch internals and API error responses, the three listed OpenSpec contracts, and
focused routing/approval/API tests. It does not migrate pending-action data, change
approval states, or add dependencies.
