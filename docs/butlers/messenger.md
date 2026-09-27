# Messenger Butler

The staffer that executes approved outbound Telegram, email, and WhatsApp adapter calls. Domain
butlers reach it only through `notify()` via Switchboard; Messenger does not classify inbound
messages or own domain logic.

- **Identity and scope:** [`roster/messenger/MANIFESTO.md`](../../roster/messenger/MANIFESTO.md)
- **Required behavior:** [`butler-messenger` spec](../../openspec/specs/butler-messenger/spec.md)
- **Schedules, modules, and port:** [`roster/messenger/butler.toml`](../../roster/messenger/butler.toml)

## Truthful delivery boundary

Live egress is the approved Switchboard route to a Messenger-owned channel adapter. Approval
gates, deferred notifications, and Switchboard attention outcomes are their own live boundaries.
Messenger has no delivery-tracking, retry, dead-letter, queue-depth, or health subsystem, so
verify delivery through the daemon health endpoint, the approval-gated adapter tools, and a scoped
`notify()` route outcome, never through retired tracking tables. The retirement of those tables is
specified in [`messenger-tracking-retirement`](../../openspec/specs/messenger-tracking-retirement/spec.md).
