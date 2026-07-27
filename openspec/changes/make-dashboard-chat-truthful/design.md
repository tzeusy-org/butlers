## Context

Dashboard chat already persists the owner message before dispatch, routes through Switchboard,
records durable reply/session lineage, and offers a server-confirmed Stop path. Its SSE generator
emits `conversation_created`, then silently polls for a persisted `conversation_reply`; it only
emits `token` once the whole reply exists. The frontend therefore shows animated dots without a
truthful account of whether Switchboard accepted the work, where it went, or whether history
could be read.

The Switchboard accepts dashboard messages in two lanes. A data statement can be routed to a
domain butler; a bug/system report is deliberately retained by Switchboard and acknowledged in
the thread. The UI must not turn the latter into a fabricated domain route.

## Goals / Non-Goals

**Goals:**

- Acknowledge a successful Switchboard dispatch before the reply arrives.
- Show the actual routed domain butler as an accountable, navigable identity when one exists.
- Make waiting, read failures, timeouts, and cancellation screen-reader perceptible without
  announcing partial token text repeatedly.
- Preserve the existing user message, draft text, durable conversation model, and cancellation
  behavior on every recovery path.

**Non-Goals:**

- Provider token streaming, runtime-adapter callbacks, or Telegram typing relays.
- Changes to Switchboard classification, sticky-routing policy, or the cross-channel ledger.
- Database migrations, new dependencies, or a new dashboard route.

## Decisions

### Emit one additive `dispatch_accepted` SSE event

After `_submit_to_switchboard()` returns an accepted result and before reply polling begins, the
server will emit `dispatch_accepted` with `{"routed_butler": <name-or-null>}`. A non-null name is
included only for a `route_to` decision with a target. `null` means the message was accepted but
the receipt has no domain target; it is not a failure and must not be rendered as a route.

This event is intentionally small: `request_id` and raw triage decision stay server-side because
the UI needs neither to state the owner-visible truth. It is additive, so an older frontend
ignores it and retains its current behavior. A first-token protocol was rejected for this slice
because it crosses runtime adapters and the session lifecycle rather than solving the immediate
honesty gap.

### Share activity state between both chat surfaces

`StreamingState` will carry the optional dispatch receipt. A small shared chat activity/status
component will render one `role="status"`, `aria-live="polite"`, `aria-atomic="true"` message:
"Sending to Switchboard", "Routed to <butler>; waiting for a reply", or "Received by
Switchboard; waiting for a reply". Animated dots are decorative and hidden from assistive
technology. The status clears when a reply becomes visible; terminal errors use the existing
recovery surface rather than claiming success.

Keeping this state local to the active stream prevents a stale route label from leaking into a
new conversation. Both `FloatingChatWidget` and `ChatPanel` consume the same component and event
shape so their behavior cannot drift.

### Make route ownership a persistent door

`ConversationHeader` will derive the accountable butler from the in-flight receipt first, then
the persisted `conversation.routed_butler`. For Switchboard conversations with a known domain
target it will render a short link to `/butlers/{name}`. For a targetless receipt it will say only
that Switchboard received the message. Per-butler chat continues to identify its addressed
butler.

This makes the existing durable routing fact visible without changing who owns the conversation
row or adding another read model.

### Recover read-side failures explicitly and non-destructively

`ConversationList` will own its list-query error state. The active-thread containers will render
a shared retryable history-read error. Each retry calls the existing React Query refetch function;
neither handler clears `localMessages`, the selected conversation, nor the draft input. These
alerts use `role="alert"`, while routine loading/activity remains polite status.

## Risks / Trade-offs

- [An accepted receipt can precede downstream runtime failure] → Wording says only “waiting for a
  reply”; timeout/error paths remain explicit and unchanged.
- [An older backend will not emit the new event] → The frontend starts with a truthful
  “Sending to Switchboard” state and still handles the existing event sequence.
- [Repeated live-region updates can become noisy] → Announce state edges only, never individual
  tokens; use one activity region per active thread.
- [A refetch failure may happen while stale messages are visible] → Retain the visible messages
  and show a non-destructive retry alert instead of replacing the thread with an empty state.

## Migration Plan

1. Deploy the additive backend SSE event; it is ignored safely by older clients.
2. Deploy the frontend receipt, route-door, and recovery UI.
3. Verify source-level SSE/event tests and frontend interaction/accessibility tests. A deployed
   manual test may send a real owner message only with operator approval; this change's routine
   validation uses local tests and read-side live probes.
4. Roll back by reverting the frontend and/or event emission. No persisted state requires repair.

## Open Questions

None. First-token streaming and cross-channel acknowledgement remain separately scoped work.
