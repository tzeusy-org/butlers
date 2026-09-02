# Supersede Telegram batch thread identity

## Why

Telegram user-client batches now split stable conversation identity from the
latest-message reply target.

## What Changes

- Replace the legacy batch-envelope requirement with its stable-identity successor.

## Impact

- Affected spec: `telegram-user-client-conversation-history`
