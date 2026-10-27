# Repair Telegram delivery truth

## Why

Ordinary `notify.v1` traffic has been rejected before Telegram egress since a
nullable recovery field began serializing as `recovery: null`. Switchboard then
recorded the nested Messenger application error as `sent` because it treated a
successful MCP hop as delivery confirmation. Telegram's adapter also accepts
any HTTP-success JSON body without proving `ok=true` and a provider message id.

The owner therefore received no messages while the delivery ledger reported
success. This change makes provider confirmation structural and restores
ordinary notification compatibility without weakening approval-recovery
authentication.

## What Changes

- Require a complete Messenger delivery receipt before any notification is
  returned or persisted as sent/delivered.
- Require Telegram `sendMessage` success to include `ok=true` and a positive
  integer provider `message_id`.
- Preserve best-effort post-send bookkeeping: a confirmed provider receipt is
  not reversed by a later notification-log or conversation-history failure.
- Keep historical replay, live deployment, and canary execution out of scope.

## Capabilities

### Modified Capabilities

- `core-notify`: makes the successful notify response a proof-bearing boundary.
- `module-telegram`: defines Telegram provider acceptance and receipt shape.

## Impact

Switchboard delivery validation, Messenger Telegram response validation,
notification metadata, focused unit/daemon tests, and runtime documentation.
