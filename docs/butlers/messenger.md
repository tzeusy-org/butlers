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

## Implementation Notes

- Email and Telegram native commands check the live permission matrix at their module effect seam, including deferred approval execution. An unavailable store refuses before traffic with `PermissionUnavailable`; Messenger reports a retryable `target_unavailable` and existing `not_attempted` / `pre_transport` / `policy_denied` evidence. Errors and availability logs omit content, recipients, row reasons and raw database errors. Readable missing rows remain default-allow and explicit revocation/recipient approvals remain separate. This bu-30sah4 effect stage precedes bu-svqpzo.3, which makes remaining shared callers, including spawn, fail closed. No provider-uncertain send gains retry permission.

- The delivery path is Switchboard `route.execute`, then approval or pending action, then the native
  Telegram, email or WhatsApp adapter, then the outcome and attention ledger. `msg_003` retired the
  unwired `delivery_requests` tracking stack; do not reintroduce a tracking health, queue, retry or
  receipt surface without an admission path that owns those records.
- Inline approvals materialise one registered native delivery command before gating and reuse it
  for immediate execution and deferred replay (`tool_args` hold only handler kwargs). Email replies
  need an authoritative `request_context.source_thread_identity`, never `request_id`, and channel
  policies such as WhatsApp `send_enabled` apply on both paths. Retries expose only allowlisted
  validation classes; raw provider errors stay in logs and audit.

- A committed recipient park emits a typed same-request/channel Messenger review reference.
  Switchboard's internal writer stores that bounded reference beside the failed notification in
  one INSERT; its public notification log tool has no review-reference input. Caller metadata,
  error text, failed parking, and legacy rows cannot create a review door. Logging failure leaves
  the committed park intact and does not retry delivery. The feed opens the adopted approval
  dossier only after complete unique-source admission, and the destination repeats that check.
