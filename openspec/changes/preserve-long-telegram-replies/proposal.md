# Preserve long Telegram replies

## Why

A single oversized or malformed Markdown message must not drop an owner reply. A confirmed prefix or uncertain Telegram effect must not authorize replay of the original logical send (RFC 0038 D11/D12; bu-z0b0ah.1).

## What Changes

- Split send/reply source before one safe HTML renderer, with ordered per-chat chunks and truthful final receipts.
- Bound explicit parse-rejection fallback and provider-rejected rate retries; retain permission/approval pinning, inert rendering and ordinary previews.
- Carry distinct known-prefix and uncertain-effect terminal outcomes through the actual automatic retry consumers. Settle existing terminal stores without claiming full delivery.
- Clamp edits, document behavior and add causal software and authored migrated-PostgreSQL controls.

## Impact

Telegram adapter and the existing Messenger, deferred notification, credential-transition, Insight and Health visit-brief consumers. No new table, chunk outbox, idempotency key, activation, provider integration, or retired DecisionDesk. Email and read-only transport retry behavior are preserved. PostgreSQL bodies and hosted/protected gates are separate execution evidence.
