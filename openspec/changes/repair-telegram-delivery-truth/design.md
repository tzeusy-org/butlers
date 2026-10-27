# Design: proof-bearing Telegram delivery

## Context

The Switchboard-to-Messenger MCP hop and the Messenger-to-Telegram provider
call are distinct boundaries. A successful MCP response proves only that the
peer answered. It does not prove that Telegram accepted a message.

## Decisions

### D1: Sent requires a complete nested receipt

Switchboard accepts success only when `route_response.v1.status=ok` contains a
`notify_response.v1` with `status=ok`, the requested channel, and a non-empty
delivery id. A route-level error is propagated. A missing or malformed receipt
is treated as an uncertain delivery outcome and is never automatically retried.

### D2: Telegram message id is the Telegram delivery id

Telegram adapter success requires HTTP success, a JSON object with `ok` exactly
true, a result object, and a positive integer non-boolean `message_id`. That
provider id is returned unchanged through Messenger and Switchboard. No request
id or generated UUID substitutes for a Telegram message receipt. Telegram
reactions are separate provider-confirmed operations whose API returns only a
boolean; they retain a stable reaction operation reference after `ok=true` and
`result=true` rather than fabricating a message id.

### D3: Failure evidence is content-blind

Audit failures use fixed categories. Provider bodies and exception strings are
not persisted because they may contain message content or token-bearing URLs.

## Rollback

The change adds no schema or data migration. Reverting code and deltas restores
the prior behavior but also restores false-positive sent accounting.
