# Dashboard Chat Truthful Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every dashboard chat turn visibly and accessibly truthful about Switchboard acceptance, routed ownership, waiting, and read-side recovery.

**Architecture:** The API adds one backward-compatible `dispatch_accepted` SSE event after Switchboard accepts a message. Both chat surfaces carry that receipt in their existing shared `StreamingState`, render the same local activity status, and expose the persisted/in-flight routed butler in the existing conversation header. Query failures render retry controls without resetting local UI state.

**Tech Stack:** FastAPI/asyncio SSE, React 19, TypeScript, TanStack Query, Vitest/RTL, pytest, OpenSpec.

## Global Constraints

- Preserve Switchboard as the only inter-butler routing path; a null route receipt must never claim a domain butler.
- Keep `conversation_reply` as the reply source; token-level runtime streaming and Telegram parity are out of scope.
- Do not alter persistence, schemas, cancellation semantics, or retry identity.
- Use a single polite, atomic activity announcement per active chat; use alerts only for recovery/failure.
- Keep all work on `codex/dashboard-chat-truth-loop-20260728`; do not modify the dirty root checkout.

---

### Task 1: Add the server-side acceptance receipt

**Files:**
- Modify: `tests/api/test_conversations.py:928-1064`
- Modify: `src/butlers/api/routers/conversations.py:340-386`

**Interfaces:**
- Produces: SSE `event: dispatch_accepted` with JSON `{"routed_butler": string | null}`.
- Consumed by: `FloatingChatWidget.tsx` and `ChatPanel.tsx` SSE event handlers.

- [x] **Step 1: Write failing routed-receipt coverage**

Add an async test that scripts an accepted Switchboard result with `triage_decision="route_to"`, `triage_target="relationship"`, and an immediately available persisted reply. Collect the generator chunks and assert this ordering:

```python
assert 'event: dispatch_accepted' in full_stream
assert '"routed_butler": "relationship"' in full_stream
assert full_stream.index('event: dispatch_accepted') < full_stream.index('event: token')
```

- [x] **Step 2: Run the new test and verify it fails**

Run: `uv run pytest tests/api/test_conversations.py -k dispatch_accepted -q`

Expected: failure because the stream currently has no `dispatch_accepted` event.

- [x] **Step 3: Write failing targetless-receipt coverage**

Add a second test with a successful accepted result that has no `route_to` target. Assert exactly this truth-bearing payload occurs before the reply:

```python
assert 'event: dispatch_accepted' in full_stream
assert '"routed_butler": null' in full_stream
assert '"routed_butler": "switchboard"' not in full_stream
```

- [x] **Step 4: Implement the minimal server event**

After `accepted`, `triage_decision`, and `triage_target` are extracted in `_stream_conversation_response`, emit one event after `_ACTIVE_TURNS` registration and before reply polling, so a client that closes immediately after the receipt cannot leak a cancel handle:

```python
receipt_target = triage_target if routed_this_turn else None
yield _sse_event("dispatch_accepted", {"routed_butler": receipt_target})
```

Keep `routed_butler = triage_target if routed_this_turn else butler_name` unchanged for timeout and cancellation resolution.

- [x] **Step 5: Run focused server coverage**

Run: `uv run pytest tests/api/test_conversations.py -q`

Expected: all conversation API tests pass, including receipt ordering and targetless truthfulness.

### Task 2: Carry and render receipt state across both chat surfaces

**Files:**
- Modify: `frontend/src/components/chat/MessageThread.tsx:241-360`
- Modify: `frontend/src/components/chat/TypingIndicator.tsx:5-23`
- Modify: `frontend/src/components/chat/ConversationHeader.tsx:41-70`
- Modify: `frontend/src/components/chat/FloatingChatWidget.tsx:232-314,489-504`
- Modify: `frontend/src/components/chat/ChatPanel.tsx:220-344,430-445`
- Test: `frontend/src/components/chat/FloatingChatWidget.test.tsx`
- Test: `frontend/src/components/chat/ChatPanel.test.tsx`

**Interfaces:**
- Consumes: `dispatch_accepted` data `{ routed_butler?: string | null }`.
- Produces: `StreamingState.dispatchReceipt?: { routedButler: string | null }` and a header prop carrying the in-flight route target.

- [x] **Step 1: Write failing widget receipt tests**

Use the existing scripted-SSE seam to send `conversation_created`, then `dispatch_accepted` before `done`. Assert the widget has one status region and accurate text:

```tsx
expect(screen.getByRole("status").textContent).toContain("Routed to relationship")
expect(screen.getByRole("link", { name: /relationship/i }).getAttribute("href"))
  .toBe("/butlers/relationship")
```

Add a targetless case asserting `Received by Switchboard` and no route link.

- [x] **Step 2: Run the widget tests and verify they fail**

Run: `npm --prefix frontend run test -- src/components/chat/FloatingChatWidget.test.tsx`

Expected: failure because `dispatch_accepted` is currently ignored and no activity status exists.

- [x] **Step 3: Add the shared stream-state contract**

Extend `StreamingState` with the optional receipt while preserving every cancellation field:

```ts
dispatchReceipt?: { routedButler: string | null }
```

In both SSE switches, set it only on `dispatch_accepted` and retain the existing `conversation_created`, `token`, `error`, `message_complete`, and `done` behavior.

- [x] **Step 4: Render accessible activity and a route door**

While `streaming.pending` is true, `MessageThread` renders one `role="status"`, `aria-live="polite"`, `aria-atomic="true"` message. It says `Sending to Switchboard` before the receipt, `Routed to <name>; waiting for a reply` for a non-null receipt, and `Received by Switchboard; waiting for a reply` for null. Mark the three animated dots `aria-hidden="true"`.

Pass `streaming?.dispatchReceipt?.routedButler` to `ConversationHeader`. When a Switchboard thread has that target or persisted `conversation.routed_butler`, render a native link to `/butlers/{target}`; retain the plain current-butler label for direct per-butler chat and a targetless Switchboard receipt.

- [x] **Step 5: Add detail-panel parity tests and run both suites**

Add the same routed/targetless assertions to `ChatPanel.test.tsx`, then run:

```bash
npm --prefix frontend run test -- \
  src/components/chat/FloatingChatWidget.test.tsx \
  src/components/chat/ChatPanel.test.tsx
```

Expected: both surfaces pass the same status and accountability contract.

### Task 3: Make list and history reads recoverable

**Files:**
- Create: `frontend/src/components/chat/ConversationReadError.tsx`
- Modify: `frontend/src/components/chat/ConversationList.tsx`
- Modify: `frontend/src/components/chat/FloatingChatWidget.tsx`
- Modify: `frontend/src/components/chat/ChatPanel.tsx`
- Modify: `frontend/src/components/chat/send-error.tsx`
- Test: `frontend/src/components/chat/FloatingChatWidget.test.tsx`
- Test: `frontend/src/components/chat/ChatPanel.test.tsx`

**Interfaces:**
- `ConversationReadError` receives `label: string` and `onRetry: () => void`.
- `useConversations`/`useConversationMessages` query results provide `isError` and `refetch`.

- [x] **Step 1: Write failing history-recovery tests**

Mock an active message query with `isError: true`, a spy `refetch`, existing `localMessages`, and a typed draft. Assert:

```tsx
expect(screen.getByRole("alert").textContent).toContain("Couldn’t load")
expect(screen.getByText(existingMessage)).toBeDefined()
expect(screen.getByDisplayValue("draft to preserve")).toBeDefined()
fireEvent.click(screen.getByRole("button", { name: /try again/i }))
expect(refetch).toHaveBeenCalledOnce()
```

Add a list-query failure test asserting its retry refetches without showing the empty-state copy.

- [x] **Step 2: Run the recovery tests and verify they fail**

Run: `npm --prefix frontend run test -- src/components/chat/FloatingChatWidget.test.tsx src/components/chat/ChatPanel.test.tsx`

Expected: failure because query errors are currently rendered as missing data/empty state.

- [x] **Step 3: Add the shared read-error component**

Create a compact component using `role="alert"`, `aria-atomic="true"`, an explicit `Try again` button, and no destructive local-state reset. Use it in `ConversationList` for its list query and beside the active thread for the message query. Call the hook-provided `refetch` functions directly.

- [x] **Step 4: Align existing recovery alerts**

Give the timeout branch of `SendErrorBanner` the same `role="alert"` and `aria-atomic="true"` semantics as its generic error branch. Do not make `SESSION_TIMEOUT` retryable and do not make `INGEST_REJECTED` appear successful.

- [x] **Step 5: Run focused frontend tests**

Run: `npm --prefix frontend run test -- src/components/chat/FloatingChatWidget.test.tsx src/components/chat/ChatPanel.test.tsx`

Expected: all chat tests pass, with query errors recoverable and local context retained.

### Task 4: Verify the contract and prepare review

**Files:**
- Modify: `openspec/changes/make-dashboard-chat-truthful/tasks.md`
- Test: `tests/api/test_conversations.py`
- Test: `frontend/src/components/chat/FloatingChatWidget.test.tsx`
- Test: `frontend/src/components/chat/ChatPanel.test.tsx`

- [x] **Step 1: Run focused quality gates**

Run:

```bash
uv run pytest tests/api/test_conversations.py -q
npm --prefix frontend run lint
npm --prefix frontend run test -- \
  src/components/chat/FloatingChatWidget.test.tsx \
  src/components/chat/ChatPanel.test.tsx
uv run ruff check src/butlers/api/routers/conversations.py tests/api/test_conversations.py
uv run ruff format --check src/butlers/api/routers/conversations.py tests/api/test_conversations.py
```

Expected: each command exits 0.

- [x] **Step 2: Validate the OpenSpec change**

Run: `openspec validate make-dashboard-chat-truthful --strict`

Expected: strict validation exits 0 with both modified capabilities recognized.

- [x] **Step 3: Record completion and hand off a reviewable branch**

Mark every completed OpenSpec checkbox, inspect `git diff --check`, commit only the scoped files, push `codex/dashboard-chat-truth-loop-20260728`, and open a PR whose body lists the exact tests and confirms no live owner message was sent during validation.
