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
- Inline approvals materialise the native command for owner and standing-rule checks. A recipient
  park stores the complete original `route.execute` envelope, including rendering policy. Human
  approval replays that exact envelope through the registered handler and the existing executor's
  task, tool and argument binding. Scheduled non-owner deliveries without a matching owner rule
  now require approval; a missing runtime or failed park refuses before sending. Email replies
  need an authoritative `request_context.source_thread_identity`, never `request_id`, and channel
  policies such as WhatsApp `send_enabled` apply on both paths. Retries expose only allowlisted
  validation classes; raw provider errors stay in logs and audit.
- `render_links_inert` is derived from internal sender marks at Switchboard, never a model tool
  argument. A nested sender cannot clear an existing mark. Telegram sends literal text in explicit
  `pre` entities and disables previews; email defangs address and URL separators. Telegram refuses
  caller URL buttons while marked and always refuses `login_url` and `web_app`; existing server
  dashboard buttons remain usable. See the [Telegram Bot API](https://core.telegram.org/bots/api)
  entity and link-preview contracts. The current WhatsApp delivery bridge has no preview-control
  contract, so marked deliveries park for explicit approval. Its ingestion connector's readback
  fields do not establish outbound preview support. Unmarked WhatsApp behavior is unchanged.

- A committed recipient park emits a typed same-request/channel Messenger review reference.
  Switchboard's internal writer stores that bounded reference beside the failed notification in
  one INSERT; its public notification log tool has no review-reference input. Caller metadata,
  error text, failed parking, and legacy rows cannot create a review door. Logging failure leaves
  the committed park intact and does not retry delivery. The feed opens the adopted approval
  dossier only after complete unique-source admission, and the destination repeats that check.


### Fixed dashboard controls

Approval cards carry typed self references, not raw dashboard URLs. The fixed
server registry maps `approval_detail` (canonical UUID `action_id`) to
`/approvals/<action_id>` and `approvals_queue` (no parameters) to `/approvals`.
Switchboard resolves these references using the configured HTTPS `DASHBOARD_URL`
origin. Messenger independently resolves them again; a wire URL, host, label,
provenance string or claimed server flag is never link authority. Valid controls
show the actual host and dashboard provenance. Self references do not expire.

The public notify contract rejects server-only `open_link` output and raw
`dashboard_url` actions. Missing or unsafe origin configuration, unknown routes,
invalid parameters and URLs over 300 characters produce `[link not verified]`
without a URL button, while preserving delivery and valid signed callbacks.
Origins cannot supply paths, queries, fragments, user information, IP or IDN
hosts, or non-default ports. The builder's legacy `dashboard_base_url` argument
remains accepted for call compatibility but does not authorize or mint a URL.

Normal and authenticated recovery delivery use the same resolver. Existing
owner recipient checks, callback signatures, recovery transport authority,
exact stored replay, inert message text and disabled previews remain in force.
The fixed dashboard control does not turn message-body URLs into live links,
change unsupported transport policy, or authorize general links or login flows.
A source guard binds every Python self route to the existing phone entry routes.

## Long Telegram delivery and terminal tails

Messenger keeps the original approved text and exact argument binding; the Telegram adapter performs ordered chunking after admission. A successful multi-chunk receipt names the last chunk and retains all validated chunk IDs. A known prefix failure returns `delivery_error`, `partial_delivery: true`, and `retryable: false`; an actual selected Telegram timeout/unknown effect is distinct `telegram_effect_uncertain: true` without prefix IDs. Neither is complete delivery. Cached route reconnect and legacy candidate selection must not replay these effects; read-only and proven-not-attempted retry controls remain intact.

Deferred notifications, Secrets transition notifications, Insight and Health visit briefs settle their existing failed-tail/dedup state before a later automatic admission. Failed terminal writes remain quarantined within the process, including changed selection windows. Stored terminal state prevents replay after successful settlement and restart; no provider/database crash-atomic chunk outbox is claimed. Authenticated approval recovery continues to use its own provider-started/ambiguous durable handoff. Inert link handling, callback authority and ordinary preview behavior are unchanged.
