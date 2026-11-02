## Context

See proposal.md for motivation and the exact adoption boundary. This is a proposed frontend use of existing metadata authority, not evidence that the interactions or adopted draft persistence have shipped.

`ConversationUpdateRequest` in `src/butlers/api/models/conversation.py` accepts nullable title (1 through 500 characters) and active/archived status. The PATCH route in `src/butlers/api/routers/conversations.py` rejects an empty update, scopes mutation to the path's conversation ID and owning butler, and returns a raw `ConversationSummary`. The storage update changes `updated_at`; there is no revision precondition. Wrong-owner or unknown identity returns 404, an empty update 422, and unavailable shared storage 503. The existing server does not trim whitespace-only titles.

The list API defaults to active, accepts active/archived/all, paginates, and orders by `updated_at DESC`. Conversation message search has no status parameter: it searches both active and archived histories. GET-by-ID and message reads permit archived conversations without changing status. Existing follow-up POST automatically reactivates an archived conversation before normal processing.

`ConversationList`, the shared `ConversationHeader`, and all four posture owners already exist. `ChatPanel`, `ChatDock`, and `FloatingChatWidget` choose an initial active conversation once per mount but derive selected metadata from the active list. `ChatPage` resolves an explicit route ID independently and renders a separate recent rail. Those differences matter when a selected conversation leaves an Active listing. `use-conversation-turn` owns neither selected identity nor draft storage; its detach and Stop operations are not metadata actions.

`apiFetch` uses the existing `ownerFetch` cookie/CSRF boundary. Conversation caches include per-butler lists, detail, cross-butler by-ID, messages, search, and active recent recall. No frontend metadata mutation wrapper exists yet. The PATCH response does not compute `latest_assistant_reply_at`; its default null is not authoritative evidence that a reply watermark disappeared.

## Goals / Non-Goals

The design keeps one target and pending-action identity across list, header, and palette entry points; retains the selected conversation across membership changes; and reconciles reversible metadata without losing independent conversation evidence.

It does not introduce a server concurrency protocol, new identity/schema, custom authentication, storage implementation, routing change, new global shortcut chord, or queue cancellation policy. It does not change the existing Continue archived conversation contract. Original acceptance criteria and source-only limits are in proposal.md.

## Decisions

### 1. Use field-limited updates through existing owner authentication

Add a typed frontend update adapter in the future implementation and route it through `apiFetch`, with the exact owning butler and conversation ID. Rename sends only `title`; archive sends only `status: archived`; Undo and unarchive send only `status: active`. No caller-asserted actor, API-key bypass, schema field, or new endpoint is needed. The alternative of sending a whole conversation risks overwriting unrelated metadata and invents authority the deployed contract does not provide.

Handle denial and transport uncertainty through the existing owner-session policy. A denied mutation must not replay automatically after sign-in. A timed-out response is not proof of server rejection: show the unconfirmed outcome, reconcile through reads, and leave retries deliberate. Cross-client last-writer behavior remains the existing server behavior; this proposal does not promise compare-and-swap semantics.

### 2. Share target-bound optimistic metadata and reconciliation

Use one future mutation/pending seam across row, header, and palette. Prevent duplicate metadata submission for the same conversation while pending; actions on other conversations remain available. Carry the original target through completion, failure, and navigation. A late result for C must not rename, select, or focus D.

Cancel or reconcile overlapping reads appropriately, retain confirmed metadata, and roll back only the failed action's affected fields and membership. The generic whole-snapshot optimistic helper is not sufficient if its rollback replaces newer message or unrelated-row state. A response merges confirmed metadata rather than replacing the full summary and erasing assistant-reply evidence. Refresh affected active/archived lists, selected detail/by-ID, search, and recent recall against server truth. Metadata timestamps never become assistant-reply notifications.

### 3. Rename in a prefilled inline editor with local key ownership

Reuse contextual row/header menus and shared input primitives. The editor starts with the current title, commits on Enter outside input-method composition, and cancels on Escape or Cancel. Validate the trimmed string as 1 through 500 Unicode code points before submitting; the UI deliberately rejects whitespace-only text even though the current server accepts it. Preserve invalid or failed text for correction or deliberate retry and associate the inline error with the input.

Enter and Escape belong to the editor and cannot also send a chat message, activate a page action, or close its containing chat surface. Return focus to the invoking control or a stable selected-header fallback. A separate dialog for every rename adds friction without solving a different authority or validation need.

### 4. Archive reversibly and keep the selected thread available

Apply optimistic archived status and remove the row from an Active chronological listing without confirmation. Keep selected C, its header, messages, composer identity/input, draft record, and turn controls mounted. Archive is only metadata: it neither sends nor stops, detaches, resets, or completes a turn. Preserve explicit selected metadata independently of active-list membership in all four postures; auto-selecting the next active row would strand the current draft and change the user's context.

Offer accessible target-specific Undo after successful archive confirmation, reachable even after the row leaves Active. Undo is a separate inverse status-active PATCH, not restoration of a stale object. Direct Unarchive uses the same inverse metadata operation. Failure restores confirmed membership/status, names the unconfirmed action, and leaves deliberate retry or refresh available. Undo success is not announced before inverse confirmation.

### 5. Separate chronological status filtering from all-status search

Use Active as the initial chronological filter and request the selected Active or Archived status through the existing paginated list API. Preserve the user's filter when entering and clearing search; show its identity in expanded and collapsed history. Keep read failures distinct from genuine empty results and retain cached rows with their retryable error.

While a non-empty 300ms-debounced search is active, state that it searches active and archived history and make the chronological status control unavailable. Identify status on results. Keep the independent cross-butler message results and their existing jump/deep-link behavior. Client-side filtering of one search page would imply completeness the endpoint cannot provide; adding a backend search filter is outside this existing-contract feature.

### 6. Compose with adopted draft retention and existing resume/continuation

The adopted `persist-safe-dashboard-unsent-drafts` source reviewed at `d2dc32ffb001398af169363064b7610595512f9d` (merged PR4056) binds draft identity to owning butler and exact conversation or distinct new-conversation key. Its transactional browser-local lifecycle includes sliding expiry, size, immutable revisions, conflict/restore/discard, acceptance, and storage-failure truth. Metadata actions never clear, move, duplicate, submit, expire, or discard those drafts. Eligible drafts must remain recoverable after remount under that adopted contract; preserving component-local input during one mount alone cannot earn that claim.

The current four posture composers still hold local `inputValue`; shared retention and the separately required approved-and-implemented exact-message acceptance resolver/trusted projection remain pending with the original draft owner (bu-2jtfw.15). Future integration must consume the delivered seam and demonstrate the guarantee, not implement a parallel store or weaken retention. PR4396's per-identity composer/queue proposal remains independently pending; archive adds no dispatch/cancellation or queued-text retention policy.

Automatic opening/remount selection remains active-only, even if archived rows are cached in another query. If none is eligible, use the existing new-conversation state. Explicit archived selection or deep link retains that exact identity and can recover its draft without mutating merely to view it. Sending a follow-up retains existing automatic reactivation, disclosed before send and reconciled afterward. No recency window, route-prefix, server read state, or cross-device resume rule is borrowed from the separate bu-0ynlk.18 proposal. Pinning and future thread-spine identities remain in bu-0ynlk.15.

### 7. Reuse keyboard, palette, and Dispatch interaction contracts

Expose Rename conversation and Archive conversation, or Unarchive conversation for an archived target, through shared header/row actions and the existing palette registry. Distinguish simultaneous targets by title and owning butler; a new conversation without persisted identity has no metadata action. Opening an action must not select another thread or send a message.

Use normal Tab/menu navigation and existing Cmd/Ctrl+K rather than add an arbitrary chord. Preserve current shortcut suspension, editable/dialog ownership, input-method guards, and panel quick-switch behavior. Reuse shared menus/buttons/inputs, accessible names, visible focus, live pending/error/Undo feedback, non-hover-only state, and Dispatch's coarse-pointer hit-area floor. Keep reversible optimism responsive without blocking text entry or unrelated controls.

### 8. Separate spec admission, owner adoption, and implementation evidence

The delta is one complete MODIFIED Conversation List Sidebar requirement, with all three prior scenario bodies retained and eighteen added cases. `REQ-dashboard-chat-ui-017` was absent from the complete public-head reservation census; no active public delta restates this Sidebar requirement. Proposed source references and design decisions do not imply adoption of adjacent pending features.

Named OpenSpec strict validation, the overwrite guard, ordinary repository guards, and full trace diagnostics with affected-change nonregression qualify this source proposal. Full-tree historical trace debt remains visible and separate from affected admission. Independent review and explicit owner adoption of the exact reviewed spec precede any implementation. Native planning completeness is artifact status only.

Future tests drive actual List/Header events through a mocked PATCH boundary, exercise the shared cache seam and four posture integrations, and retain all original frontend gates. Tests must cover failure, inverse failure, target races, Unicode limits, owner denial without replay, focus ownership, archived explicit selection, active-only resume, running turns, and actual adopted remount retention. No source-grep test or fake preselected mutation result substitutes for executing those interactions.

## Risks / Trade-offs

- A lost response may have committed on the server. Reconcile metadata and name uncertainty; local rollback alone does not prove rejection.
- A PATCH summary lacks computed reply evidence. Merge only metadata and refresh authoritative list data instead of replacing unrelated fields.
- An archived selected row leaves Active. Keep its independent selected detail and stable header/composer identity; do not remount into another conversation.
- Browser-local retention is adopted but not implemented at the needed shared seam. Keep the original dependency explicit and do not claim remount recovery from the current local state.
- Existing queued follow-up behavior may later coexist with archive and reactivate a thread through the existing POST. This proposal does not silently alter that independently governed policy.
- No server revision precondition exists. Reconcile to current server truth without claiming cross-client linearizability or adding a backend protocol.

## Migration Plan

This phase adds only proposal artifacts; it deploys nothing and performs no schema migration or canonical-spec synchronization. After independent review, exact owner adoption, and separate implementation authorization, deliver the frontend seam and behavior tests against the existing API. Release and protected merge checks remain the normal repository process. Frontend rollback removes these affordances without deleting conversations or drafts or reversing already confirmed metadata; the existing PATCH and explicit unarchive remain the recovery authority.
