# Messenger Butler

Messenger is the staffer that executes approved outbound Telegram, email, and
WhatsApp adapter calls. Domain butlers use `notify()` through Switchboard;
Messenger does not classify inbound messages or own domain logic.

## Profile

| Property | Value |
|---|---|
| Port | 41104 |
| Schema | `messenger` |
| Modules | calendar, telegram, email, WhatsApp, approvals |

## Truthful delivery boundary

Live egress is the approved Switchboard route to a Messenger-owned channel
adapter. Approval gates, deferred notifications, and Switchboard attention
outcomes remain their own live boundaries. Messenger has no delivery-tracking,
retry, dead-letter, queue-depth, or fabricated health subsystem.

Migration `msg_003` takes one transaction-scoped exclusive lock across every
existing legacy table before it checks emptiness or drops anything. It raises
without destructive DDL when retained or concurrently committed rows exist.
Its downgrade recreates the exact empty `msg_002` compatibility schema; it
cannot restore data.

## Verification

Verify the daemon health endpoint, its approval-gated adapter tool registration,
and a scoped `notify()` route outcome. Do not infer delivery health from retired
Messenger tracking tables or endpoints.
