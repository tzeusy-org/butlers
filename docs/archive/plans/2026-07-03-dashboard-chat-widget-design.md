> **ARCHIVED** — This implementation/design plan is historical. Archived on 2026-09-06.
> **Reason:** Owner-validated chat-widget design shipped via the dashboard conversations / durable-turn spine; the plan is spent exhaust.
> **Successor:** `openspec/specs/dashboard-conversations/spec.md`.
>
> The doctrine on archival lives in `openspec/specs/docs-information-architecture/spec.md`.

# Dashboard Chat Widget — Owner ↔ Switchboard Conversational Ingress

**Date:** 2026-07-03
**Status:** Validated design (brainstorm with owner)
**Related:** stubbed TODOs bu-27mx / bu-4m6i (`_submit_to_switchboard`), cmdk spine (bu-86c4c.7)

## Problem

The dashboard is read-only for the owner's knowledge. There are many things the owner
does with the ecosystem that have no input mechanism — e.g. viewing
`/entities/concentration?predicate=child-of` and knowing a fact or correction, with no
way to state it. Likewise there is no owner-facing surface for reporting data problems
or system bugs. The owner wants a floating chat widget, available on every page, that
talks to the Switchboard Butler, which routes each message to the right place — with a
strong engine mapping free-text statements into consistent backend predicates.

## Decisions (owner-validated)

1. **Interaction model: confirm-loop chat.** Each message is routed; the target butler
   replies in-thread with its interpretation ("Recorded: Alice child-of Bob; birthday →
   2019-03-03 — correct?") and the owner can correct it. Replies take ~10–60s (spawned
   session, DB-polled SSE). No token-level streaming (deliberately deferred).
2. **Two lanes via Switchboard classification.** Data statements/corrections →
   `route()` to the domain butler. Bug/system reports → fingerprinted
   `butler_reports` row (same plumbing as QA canary injection) so QA patrol
   investigates; reply carries the case reference.
3. **Extend the existing conversations spine** — do not build a parallel feedback API.
   The conversations API (`POST /api/butlers/{name}/conversations`, SSE, `ingest.v1`
   envelope) was designed for exactly this and is stubbed at `_submit_to_switchboard`.
4. **History is first-class.** Conversations persist (existing per-butler
   `conversations`/`messages` tables); widget has a history view.
5. **Unread-reply indicator** on the floating button.

## Architecture

### Frontend

- **Mount:** floating button (bottom-right, every route) as a new sibling in
  `frontend/src/layouts/RootLayout.tsx` (inside `CommandRegistryProvider`, next to
  `EntityFinder`/`Toaster`). Also registered in the cmdk palette ("Talk to Butlers").
- **Panel:** compact floating popover reusing `frontend/src/components/chat/*`
  primitives (`MessageThread`, `MessageInput`, `sse-utils`), restyled from the
  butler-detail Sheet. Two views: active thread + history (reuse `ConversationList`).
- **Page context capture:** on send, snapshot route path, query params, and any
  entity/subject the page exposes via a lightweight `PageContextProvider` that pages
  can enrich. Attached to the message envelope so statements arrive grounded.
- **History/lifecycle:** reopening resumes the most recent open conversation; can start
  fresh or continue any past thread. Titles auto-generated from the first message
  (existing `PATCH` title). Each conversation displays the butler it was routed to and
  links to spawned sessions.
- **Storage scope:** all widget conversations live under **Switchboard's schema** (the
  ingress) — one unified "everything I told the system" history; routed-to butler is
  metadata, not storage location. Same conversations are visible on Switchboard's
  butler-detail chat panel.
- **Unread badge:** poll the existing conversation-summary endpoint (~60s while the
  dashboard is open); compare latest reply timestamp vs a last-seen watermark in
  localStorage; badge the floating button when a reply arrived while the panel was
  closed.

### Backend

- **Implement the stub:** `_submit_to_switchboard()` in
  `src/butlers/api/routers/conversations.py` becomes a real MCP call:
  `mcp_manager.get_client("switchboard").call_tool("ingest", envelope)`.
- **Envelope:** existing `ingest.v1` from `build_dashboard_envelope()`, extended with
  `page_context` (`route`, `query_params`, `entity_ref?`) and
  `conversation_id`/`request_id` for reply correlation. Channel `dashboard`, sender =
  owner (resolved via `public.contacts` bootstrap).
- **Switchboard classification:** existing `classify` → `route` pipeline gains one new
  outcome:
  - **Lane A (data):** `route()` to domain butler; routed envelope carries
    `conversation_id` + page context; target session prompted to *interpret, apply,
    confirm*.
  - **Lane B (bug/system):** file fingerprinted `butler_reports` row; reply with case
    reference.
  - Misclassification is corrected in-thread via existing `correct_route`.
- **Reply mechanics:** routed butler sessions get a `conversation_reply` capability
  that writes the confirmation message into the Switchboard-schema conversation
  (correlated by `conversation_id`). The SSE poller (`_poll_session_completion`) is
  changed to watch for that reply message rather than raw session completion — the
  widget receives the butler's deliberate reply, not its transcript. Session ends
  without replying → poller times out with a graceful "no reply — inspect session"
  event + session link.
- **Sticky follow-ups:** first successful route stamps `routed_butler` on the
  conversation; `POST /conversations/{id}/messages` bypasses classification and goes
  straight to that butler (same ingest tool, `mode=followup`).

## Error handling

- **Switchboard unreachable (MCP 503):** SSE `error` event; widget shows "Switchboard
  offline — retry". Message is persisted before submission. Retry starts a fresh
  submission attempt with the same `message_id`; it does not replay the original
  HTTP/SSE request or envelope. For details, see:

  [Chat send retry semantics](2026-07-17-chat-send-retry-semantics.md).
- **Reply timeout:** graceful timeout event with session link; thread stays open; late
  replies are caught by the unread poll.
- **Unroutable:** Switchboard dead-letter tooling; owner told in-thread.

## Testing

- **Backend units:** enriched envelope (page_context), real `_submit_to_switchboard`
  (mocked MCP manager), reply-watch poller, sticky follow-up path.
- **Integration (real Postgres/testcontainers):** `conversation_reply` write path —
  mocked-pool-only green has previously hidden schema/search_path bugs.
- **Switchboard tools:** lane classification, `butler_reports` filing, `correct_route`.
- **Frontend:** vitest for widget/unread state; one Playwright happy path; `eslint .`
  gate (frontend CI runs it).

## Rollout

1. Widget + real ingest + confirm-loop for the data lane (one butler end-to-end, e.g.
   relationship).
2. QA bug lane (`butler_reports` filing).
3. Unread badge + per-page `PageContextProvider` enrichment.

No feature flag — single-owner dev dashboard.
