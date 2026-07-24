## Why

Cross-butler delegation currently ends when the target records an answer: the
original asking butler has no authorized return path and cannot safely be
scheduled by a sibling. The result is a durable answer that is discoverable
only by later manual lookup rather than a bounded continuation of the asking
butler's work.

This change specifies the one permitted v1 return loop before any writer,
schema, tool-activation, or producer work begins: an answered target calls
back only through Switchboard, and only the original asker creates its own
one-shot return task.

## What Changes

- Define the durable delegated-answer wake protocol: `delegate_answer` records
  an authoritative answer, Switchboard validates and routes the callback, and
  the original asker accepts `delegate_wake` and reconciles exactly one local
  `delegate-return-<ledger_id>` task.
- Specify answer and wake disposition, callback attempts/results, task ID,
  immutable replay key, authoritative asker/target identity, and audit and
  retention expectations for the delegation ledger.
- Fence callback payloads as untrusted references. Neither Switchboard nor the
  asker may treat caller-supplied answer text, target names, or task data as
  authority in place of the durable ledger row.
- Define retry, duplicate/reconnect, wrong-row/actor, changed-answer,
  crash-after-insert, late, and legacy-row behavior without creating a second
  logical wake or fabricating a completed callback.
- Reserve the non-staffer delegation core-tool inventory, including the
  Switchboard-routed `delegate_wake` endpoint. Runtime-config validation,
  live activation, roster guidance, and seed configuration remain follow-on
  work.
- State the briefing boundary: a delegated return wake is internal work, not a
  briefing contribution, composer input, envelope change, schedule entry, or
  owner-facing notification.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `cross-butler-delegation`: add the authoritative answer-to-asker callback,
  durable wake state machine, replay fencing, and asker-local task contract.
- `core-daemon`: define the reserved delegation core-tool inventory and the
  server-to-server authorization boundary for `delegate_wake`.
- `cross-butler-briefing-contribution`: make the delegated-return exclusion
  from specialist contributions and the briefing composer normative.

## Impact

This is an OpenSpec-only contract change. It modifies no runtime source,
migration, database row, schedule, config, connector, dashboard, or user
delivery behavior.

Follow-on implementation will touch the delegation ledger, delegation tools,
Switchboard routing, and the asking butler's local scheduler through MCP-only
boundaries. It must not create a direct sibling-schema write/call, user
notification, quiet-window/DND wake-recovery behavior, briefing-composer join,
catalog/QA/subscription workflow, or any overlap with the separate strict
owner-Telegram wake-recovery change in PR #3513.
