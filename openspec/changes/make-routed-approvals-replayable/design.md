## Context

Messenger's `route.execute` path resolves a notify envelope, renders content, checks
approval rules inline, and then calls channel module methods directly. Today the
approval branch constructs a different, routing-shaped payload from the immediate
delivery branch. For email reply, that payload omits the provider thread identifier
and uses `message`/`intent` fields that the registered handler rejects. Non-email
paths can persist `route.execute` itself, which is not a safe replay command.

Approved actions are later dispatched through the owning daemon's original registered
handler. The dashboard API currently reduces every dispatch failure to a null sentinel
and therefore cannot distinguish an unreachable daemon from a reachable handler
rejection.

## Goals / Non-Goals

**Goals:**

- Make every newly parked routed outbound action self-contained and executable.
- Ensure the immediate and deferred paths use identical normalized delivery values.
- Fail closed when an email reply lacks authoritative provider thread identity.
- Preserve approved/unexecuted state on replay failure and report the failure class
  truthfully to operators.

**Non-Goals:**

- Rewrite, infer missing fields for, or automatically replay historical actions.
- Introduce a dropped/abandoned approval state.
- Change WhatsApp's current routed-reply delivery semantics.
- Repair unrelated approval producers outside Messenger routed delivery.

## Decisions

### Materialize a typed native command before gating

`route.execute` will construct a small internal command value containing the
registered native tool name, exact handler kwargs, and an execution closure. Rule
matching, parking, and immediate execution all consume that value.

This is preferred over translating stored arguments during Retry because replay-time
translation creates versioned aliases, cannot recover omitted identifiers, and lets
the immediate and deferred paths drift again.

### Keep provenance separate from executable arguments

`pending_actions.tool_args` will contain only kwargs accepted by the registered
handler. Request, route, and origin lineage remains in existing action metadata and
audit fields. The executor therefore does not need to strip unknown routing fields.

### Require provider-native thread identity for email reply

Email reply command construction requires `request_context.source_thread_identity`.
Internal `request_id` is not a Gmail thread identifier and will never be substituted.
When the identity is absent, command construction returns a validation failure before
approval parking or delivery.

### Preserve current channel semantics

Email maps to `email_send_message` or `email_reply_to_thread`; Telegram maps to
`telegram_send_message` or `telegram_reply_to_message`; WhatsApp maps to
`whatsapp_send_message`, including the currently implemented routed-reply behavior.
This change makes approval replay match actual delivery without separately redesigning
WhatsApp reply targeting. Both paths call the module's policy-aware send seam so the
`send_enabled` ban-risk gate cannot be bypassed by immediate delivery.

### Return a structured internal dispatch outcome

Approval dispatch will return a private structured outcome that identifies success,
transport unreachability, or reachable executor/tool failure. Both Retry endpoints
map those outcomes to safe operator-facing errors. Handler detail is sanitized and
allowlisted by failure shape; arbitrary handler text and raw tracebacks are logged,
not returned.

The pending action remains `approved` with null `execution_result` unless the shared
executor completes successfully. Existing immutable `action_execution_failed` audit
events remain the durable failure record.

## Risks / Trade-offs

- **[Existing malformed actions remain unreplayable]** → Preserve them as audit
  records and fail explicitly; do not guess missing provider identifiers.
- **[Native handler signatures can drift]** → Centralize command construction and
  add exact-argument plus behavior-executing tests against registered handlers.
- **[Error detail could leak provider data]** → Expose only classified, bounded
  handler messages and retain full exception context in server logs.
- **[WhatsApp reply semantics remain limited]** → Name this explicitly as a non-goal
  and persist the native send command that current execution actually uses.

## Migration Plan

No schema or data migration is required. Deploy code and contract tests together.
New actions use canonical commands; historical rows are unchanged. Rollback restores
the prior producer/API behavior without data transformation.

## Open Questions

None for this change. Approval producers outside Messenger routed delivery will be
tracked separately because they cross different trust and ownership boundaries.
