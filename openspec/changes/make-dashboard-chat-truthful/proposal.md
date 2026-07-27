## Why

The dashboard conversation path is durable and session-linked, but it leaves the owner with a
silent 10–60 second wait after Send and then presents Switchboard rather than the domain butler
that owns the work. A list or message-history fetch failure can also resemble an empty thread.
That is operationally correct infrastructure wearing an untrustworthy conversational surface.

## What Changes

- Add an additive SSE routing receipt after Switchboard accepts a dashboard message. It reports
  an accepted message honestly and names a routed domain butler only when the Switchboard has
  actually selected one; a non-routing lane must not impersonate a route.
- Surface the receipt in both dashboard chat surfaces as a concise, screen-reader-visible state
  sequence: submitted, routed or retained by Switchboard, waiting for the reply, then completed
  or recoverable failure.
- Make the persisted `routed_butler` an accountable, navigable identity in conversation chrome
  rather than leaving the active thread labelled only as `switchboard`.
- Render explicit, retryable conversation-list and message-history read failures without
  discarding already-visible thread content or draft text.
- Give typing, timeout, cancellation, and recovery feedback consistent live-region semantics.

This change does not add provider token streaming, change routing policy, create a cross-channel
ledger, or alter the existing server-confirmed Stop contract.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-conversations`: dashboard submission SSE gains an honest, additive routing receipt
  event before reply completion.
- `dashboard-chat-ui`: both chat surfaces expose routing accountability, accessible asynchronous
  state, and explicit read-side recovery.

## Impact

- Backend: `src/butlers/api/routers/conversations.py` and its API tests.
- Frontend: chat SSE state handling, conversation headers, message-thread status feedback, and
  shared recovery rendering in `frontend/src/components/chat/`.
- No database migration, new dependency, runtime-adapter change, or external connector change.
