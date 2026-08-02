# Dashboard Chat Current-Contract Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reconcile PR #3618 with the landed message-scoped dashboard-turn contract while retaining the owner's three approved guarantees: truthful dispatch receipts, a current-turn accountable Butler link only after durable proof, and non-destructive conversation-read recovery.

**Architecture:** The API observes the existing durable dashboard-turn read surface only after immutable ingress has bound as `accepted` (or a prior accepted ingress is reused). Its first safe active receipt is always targetless, even when that observation already has a durable route; only a separate later poll may emit one named route upgrade. It emits no receipt for legacy requests without an immutable `message_id`, unavailable status, cancellation/ambiguity, or a terminal-action target. Both chat surfaces treat that receipt as current-turn state, never as a historical conversation route. Query failures remain query failures: the selected thread and draft stay intact, cached rows/history remain visible, and local optimistic messages are rendered only for the conversation that owns them.

**Tech Stack:** Python/FastAPI SSE, existing `public.dashboard_turn_*` SECURITY DEFINER surface, React/TypeScript, TanStack Query, Vitest, pytest, OpenSpec.

## Global Constraints

- Merge `origin/main` into the PR update branch without force-pushing; resolve in favor of the landed durable message-scoped Stop and ingress controls.
- Preserve `claim_ingress`, `bind_ingress`, `claim_target`, `mark_route_enqueued`, `dispatch_status`, canonical message-scoped Stop, `SESSION_CANCELLED`, and `TURN_OUTCOME_UNKNOWN` behavior. Do not restore the retired conversation-scoped boolean cancellation API.
- A `triage_target` is a pre-routing decision, not receipt evidence. A non-null `routed_butler` receipt is allowed only when `DashboardTurnResult.target_kind == "route"` and the durable turn names that target.
- Emit `dispatch_accepted` only for an immutable-message request after `bind_ingress` (or reused `claim_ingress`) has durably reached `accepted` and a safe `dispatch_status` observation is available. The first receipt is exactly `{"routed_butler": null}` regardless of whether that first safe observation already has a durable route; one later distinct status poll may emit the named durable-route upgrade. Do not emit two receipts from the same observation, or any receipt for legacy requests, a missing/unsafe status observation, `cancelling`, cancellation/ambiguity, or a terminal-action target.
- A targetless receipt means only `Received by Switchboard; waiting for a reply.` It must never infer a destination from `conversation.routed_butler`, a prior message, or an optimistic `triage_target`.
- Render a `/butlers/{name}` link only for a non-null current-stream receipt. With no receipt, keep the existing plain Butler label; with a targetless receipt, do not create a Switchboard link.
- Keep the current animated typing dots. Mark the decorative dots `aria-hidden`; provide exactly one polite, atomic textual status while pending.
- Read errors must not become empty states. Retry must invoke the existing query's `refetch`, preserve the draft and selected conversation, keep already-loaded same-thread history visible, and never display optimistic messages owned by another conversation.
- Do not add a migration, direct database access, a retry/redelivery path, dependencies, or a new persistence model. `dispatch_status` is the existing safe observation surface.
- Keep PR text free of personal information, secrets, and session URLs. Remove the old competing OpenSpec change and obsolete July plan rather than retaining conflicting requirements.

---

## Task 1: Reconcile the branch, durable receipt contract, read recovery, and specification

**Files:**

- Modify: `src/butlers/api/routers/conversations.py`
- Modify: `tests/api/test_conversations.py`
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/components/chat/MessageThread.tsx`
- Modify: `frontend/src/components/chat/TypingIndicator.tsx`
- Modify: `frontend/src/components/chat/ConversationHeader.tsx`
- Add: `frontend/src/components/chat/ConversationReadError.tsx`
- Modify: `frontend/src/components/chat/ConversationList.tsx`
- Modify: `frontend/src/components/chat/ChatPanel.tsx`
- Modify: `frontend/src/components/chat/FloatingChatWidget.tsx`
- Modify: `frontend/src/components/chat/send-error.tsx`
- Add or modify focused tests beside each changed chat component, including `ChatPanel.test.tsx`, `FloatingChatWidget.test.tsx`, `ConversationList.test.tsx`, `ConversationReadError.test.tsx`, `ConversationHeader.test.tsx`, `MessageThread.test.tsx`, and `send-error.test.tsx`
- Modify: `openspec/changes/durable-dashboard-terminal-action-recovery/proposal.md`
- Modify: `openspec/changes/durable-dashboard-terminal-action-recovery/tasks.md`
- Modify: `openspec/changes/durable-dashboard-terminal-action-recovery/design.md`
- Modify: `openspec/changes/durable-dashboard-terminal-action-recovery/specs/dashboard-conversations/spec.md`
- Modify: `openspec/changes/durable-dashboard-terminal-action-recovery/specs/dashboard-chat-ui/spec.md`
- Modify: `about/legends-and-lore/rfcs/0007-dashboard-and-api-surface.md`
- Modify: `docs/frontend/backend-api-contract.md`
- Modify: `docs/redesigns/2026-07-28-talk-to-butlers-maturity-pursuit.md`
- Modify: `docs/redesigns/2026-07-28-talk-to-butlers-maturity-pursuit-data.json`
- Delete: `openspec/changes/make-dashboard-chat-truthful/`
- Delete: `docs/superpowers/plans/2026-07-28-dashboard-chat-truthful-loop.md`

- [x] **Step 1: Establish the current-base reconciliation and write failing focused tests.**

  - Merge `origin/main` into the isolated PR update branch and resolve conflicts by retaining the current durable ingress/Stop protocol, then remove stale implementations that depend on the old cancel endpoint or optimistic `triage_target` routing.
  - In `tests/api/test_conversations.py`, add focused stream tests showing: (a) a safe durable targetless observation after accepted ingress sends `dispatch_accepted` with `routed_butler: null` even when ingestion proposes `triage_target`; (b) an already durable `target_kind: "route"` still emits that targetless first receipt and only names the route from a later poll; (c) a third same-route poll does not duplicate the one named upgrade; (d) legacy requests, `bind_ingress: "cancelling"`, unavailable status, terminal-action targets, cancelled, ambiguous, and conflicting flows emit no fabricated receipt, while a `finished` bind continues normal reply polling.
  - In frontend tests, first assert the new failures: a targetless receipt overrides a stored historical `routed_butler` and produces no link; a durable receipt links only its exact current target; animated dots remain decorative with a single status; list/search/history errors render an alert and refetch while preserving cached rows/history; same-thread refresh error retains its messages; a newly selected failed/loading thread does not render previous-thread optimistic messages; retry preserves draft and selection; timeout/ambiguous/pending error banners are alerts.
  - Run the new tests before implementation. Expected result: failures demonstrating that `dispatch_accepted`, receipt ownership, and read recovery are not yet implemented on the reconciled baseline.

- [x] **Step 2: Implement receipt production from durable route evidence.**

  - Extend the server SSE vocabulary documentation and `ConversationSseEventType` with `dispatch_accepted`.
  - Add a small private helper in `conversations.py` that maps a `DashboardTurnResult` to a route target only when `target_kind == "route"` and `target_butler` is a non-empty string. It must not inspect `triage_decision`, `triage_target`, or `conversation.routed_butler` for receipt attribution.
  - After Switchboard ingress is accepted and the durable ingress binding is complete, observe `dispatch_status` before reply polling. For a durable-message request, the first safe active observation must emit exactly `{"routed_butler": null}` even if it already exposes a durable route; do not emit a second receipt from that observation. Do not emit a receipt when status is unavailable or terminal/unsafe, for a terminal-action target, or for a legacy request without an immutable durable `message_id`.
  - Keep a local receipt-stage sentinel during the poll. Only a later successful `dispatch_status` observation may emit one named `dispatch_accepted` update, and only when it exposes a durable route target. Continue to honor its cancellation and ambiguity outcomes exactly as current main does. Do not make route truth depend on sticky conversation routing or mutate existing durable route state.
  - Retain current timeout/session lookup behavior independently of the receipt; do not use receipt attribution to weaken cancellation or timeout recovery.

- [x] **Step 3: Consume current-turn receipts accessibly in both chat surfaces.**

  - Add `dispatchReceipt: { routedButler: string | null } | null` to `StreamingState`; both `ChatPanel` and `FloatingChatWidget` reset it for each outgoing turn and update it on every `dispatch_accepted` event.
  - Have `MessageThread` announce `Sending to Switchboard.`, `Received by Switchboard; waiting for a reply.`, or `Routed to <name>; waiting for a reply.` based solely on that current streaming receipt. Preserve the animated `TypingIndicator`; mark its dot spans `aria-hidden` and keep one `role="status"`, `aria-live="polite"`, `aria-atomic="true"` textual region. During cancelling or confirmed Stop, suppress receipt activity/dots so the Stop status is the only live region.
  - Pass the current receipt to `ConversationHeader`. Render the exact named route as a `Routed to` link to `/butlers/${encodeURIComponent(name)}` only when the receipt is non-null and names a Butler. Never use a historical `conversation.routed_butler` as a current-turn fallback; a targetless receipt has no destination link. Clear or suppress it across Stop/error handoff, and preserve the optimistic message when a Stop or already-finished response supplies the first real conversation id.

- [x] **Step 4: Make list/history recovery non-destructive.**

  - Add `ConversationReadError` with visible `role="alert"`, an accessible retry label, and the dashboard-standard 1.5 stroke icon. Use it for list/search errors and active-history errors; suppress the normal empty-state copy whenever a read failed.
  - In both chat containers, destructure `isError` and `refetch` from `useConversationMessages`, retain the current selection and input state on error, and invoke the supplied query `refetch` directly on retry.
  - Associate optimistic local messages with their owning conversation id. Continue showing local and loaded messages for a same-conversation refresh error, but show no prior conversation's optimistic/local messages under a newly selected thread while its history is loading or failed. Avoid a false `No messages yet` state in that transition.
  - Make the existing timeout, ambiguous, and pending send-error branch a polite atomic alert without altering its conservative retry policy or terminal truth semantics.

- [x] **Step 5: Reconcile the authoritative OpenSpec change and remove stale artifacts.**

  - Amend the active durable-recovery proposal/tasks/design to record the owner's retained #3618 guarantees and the non-competing reconciliation path; do not falsely mark terminal-action recovery work complete. Update RFC 0007 and the frontend backend-API contract with the current SSE receipt and conversation-read rules.
  - Add delta scenarios to `dashboard-conversations` for safe targetless receipt after accepted ingress, receipt upgrade only after a durable route claim, and no optimistic target attribution. Add UI scenarios to `dashboard-chat-ui` for the current-turn-only link, animated-but-decorative typing dots with one status announcement, and non-destructive list/search/history recovery including cross-thread isolation.
  - Delete the old `make-dashboard-chat-truthful` OpenSpec change and its superseded July plan. Keep this plan as the single implementation handoff.

- [x] **Step 6: Verify, review, commit, and prepare the fast-forward PR update.**

  - Run, in order:
    - `uv run pytest tests/api/test_conversations.py -q -n 0`
    - `npm --prefix frontend run test -- src/components/chat/ChatPanel.test.tsx src/components/chat/FloatingChatWidget.test.tsx src/components/chat/ConversationList.test.tsx src/components/chat/ConversationReadError.test.tsx src/components/chat/ConversationHeader.test.tsx src/components/chat/MessageThread.test.tsx src/components/chat/send-error.test.tsx`
    - `npm --prefix frontend run lint`
    - `npm --prefix frontend run lint:emdash`
    - `npm --prefix frontend run lint:query-coercion`
    - `npm --prefix frontend run build`
    - `uv run ruff check src/butlers/api/routers/conversations.py tests/api/test_conversations.py`
    - `uv run ruff format --check src/butlers/api/routers/conversations.py tests/api/test_conversations.py`
    - `openspec validate durable-dashboard-terminal-action-recovery --strict`
    - `git diff --check`
  - Inspect the staged file list and diff for only the listed reconciliation scope; obtain an independent task review before the parent performs exact-base push/merge validation.
  - Commit with a concise reconciliation message on the isolated branch and hand the commit to the parent; do not push or modify PR metadata from this worker.

## Plan Self-Review

- [x] All owner-approved #3618 guarantees map to server, UI, error-recovery, and test steps.
- [x] Route attribution is derived only from `DashboardTurnResult.target_kind == "route"` and its durable target, never from pre-routing triage or sticky history.
- [x] The plan preserves the landed Stop/ingress contract and explicitly forbids a migration or redelivery addition.
- [x] Every listed verification command is executable from the repository root and covers the modified layers.
- [x] The stale competing OpenSpec and July plan are explicitly removed, leaving the active durable-recovery change authoritative.
