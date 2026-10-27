# Withhold bearer content in HA connector events

## Why
The HA connector currently forwards and buffers raw text-sensor state and attributes. The existing shared scrubber already recognizes ordinary keyword OTPs and reset/magic links, but the HA producer never calls it. Person-history and recorder-wellness branches must share the correction.

## What changes
Add a connector-local adapter for the shared scrubber; sanitize a copy before the reorder queue and dispatcher consumers, defend direct outgoing/history/filtered/replay helpers, and stop echoing malformed WebSocket text. Qualify the complete-event replay promise with the existing bearer-withholding security exception. Preserve every other old clause, source identity, numeric/enum reading, transport-diff membership, channel, checkpoint, and shared-buffer behavior.

## Scope and status
This connector-local security correction is implemented in the private bu-svqpzo.11 checkout based on 08d03fa7e64c1f6f8047d961e3e8cece327ab0a2. Three new ordinary-consumer regressions demonstrated causal old-source RED and now pass; the complete owning scope passes167 tests. The ordinary numeric/enum outputs match a captured old-source consumer snapshot exactly. The separate real-Postgres test is present but remains unproven because the canonical Docker fixture failed with PermissionError; no privileged retry or delivery claim is made. Independent review and hosted gates remain separate. No notification feature, allowlist change, new detector, auth change, history backfill, shared-kit repair, or unrelated raw-cache remediation is included.
