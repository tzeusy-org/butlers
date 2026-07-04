## Why

Owner chat widget epic bu-p6ey8 (design: `docs/plans/2026-07-03-dashboard-chat-widget-design.md`)
needs a real reply channel. Today `_poll_session_completion` reads the routed
butler's raw `sessions.result` — the spawned session's final transcript, not
a deliberate confirmation — and only ever finds it when the session landed on
the *requested* butler's own schema, which is never true for a
classification-routed (Switchboard widget) conversation. That path currently
just times out. There is also no mechanism for a follow-up message on a
classification-routed conversation to stick to the butler it was already
routed to — every message reclassifies from scratch.

## What Changes

- New `conversation_reply` MCP tool, registered unconditionally on every
  butler (any butler can be the classification or pinned-target destination
  of a dashboard conversation): writes an assistant-role message directly
  into `public.dashboard_messages` for the conversation it was routed from.
- The SSE poller (`_stream_conversation_response` /
  `message_find_reply_since`) now watches `public.dashboard_messages` for
  that reply instead of the routed butler's `sessions` row. `message_complete`
  no longer carries model/token/duration attribution (`null` — the reply is
  persisted mid-session, before the session's own accounting exists).
  `SESSION_FAILED`/`PERSISTENCE_ERROR` are retired from this path; a
  `SESSION_TIMEOUT` error event now carries a `session_id` link when no reply
  arrives within the poll window, and the conversation thread stays open (a
  late reply is visible on the next fetch/poll).
- `public.dashboard_conversations` gains a `routed_butler TEXT NULL` column
  (migration `core_153`). A classification-routed (Switchboard-addressed)
  conversation stamps it on the first successful `route_to` decision;
  follow-up messages on that conversation then submit with
  `control.pinned_target = routed_butler`, bypassing reclassification.
  Pinned per-butler conversations are unaffected (already deterministic).

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `dashboard-conversations`: adds the Conversation Reply Channel requirement
  (the `conversation_reply` tool); adds `routed_butler` to the conversation
  data model; updates the SSE Response Streaming requirement's
  `message_complete`/error-event contract; adds sticky-routing scenarios to
  the ingestion envelope requirement.

## Impact

- `src/butlers/core_tools/_conversation_reply.py` (new) — the
  `conversation_reply` MCP tool, registered in `_dispatcher.py`.
- `src/butlers/api/conversations.py` — `conversation_reply_create`,
  `conversation_set_routed_butler`, `message_find_reply_since`.
- `src/butlers/api/routers/conversations.py` — `_stream_conversation_response`
  rewritten around the new poll target; `_lookup_timed_out_session_id` added
  for the `SESSION_TIMEOUT` session link; `send_message` pins follow-ups to
  `routed_butler` when set.
- `alembic/versions/core/core_153_dashboard_conversations_routed_butler.py` —
  additive, backward-compatible column.
- Out of scope: token-level streaming (deliberately deferred, per the design
  doc); the Switchboard classify/route-lane prompt wiring that instructs a
  routed session to actually call `conversation_reply` (sibling bead
  bu-p6ey8.2 — until it lands, dashboard sessions that don't call the tool
  time out gracefully rather than replying, which is the documented fallback
  behavior).
