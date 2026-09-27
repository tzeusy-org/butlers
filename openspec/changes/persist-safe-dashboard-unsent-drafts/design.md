## Context

This is a medium, user-facing specification change. The current dashboard keeps composer and form state in mounted React components. `ChatPanel` and `FloatingChatWidget` clear their input before their asynchronous send path resolves, while eligible dialogs reset or unmount their field state when they close. The active `durable-dashboard-terminal-action-recovery` change preserves a same-thread in-memory draft during history read failure; it does not define browser persistence.

The dashboard already treats browser-storage exceptions as recoverable. This feature needs stronger semantics than its existing preference use of `localStorage`: draft writes, conditional clears, and deletions must linearize across tabs. The deployment trust model is single-user, localhost-bound with Tailscale-mediated external access and optional API-key authentication. That boundary protects access to the dashboard service. It does not encrypt or authenticate data in a shared browser profile.

The binding interface language is Dispatch (`openspec/specs/dashboard-design-language/spec.md`): calm status, terse copy, visible keyboard focus, and at most one commit action per surface. The design-bar recovery pass additionally requires that closing, storage failure, and repeated actions remain understandable and recoverable.

This artifact is a proposal for exact owner review. It authorizes no implementation, baseline sync, archive, or release.

## Goals / Non-Goals

**Goals:**

- Preserve all owner-entered state needed to resume the explicitly eligible chat and form/dialog tasks.
- Make draft identity deterministic across remounts without coupling it to React component instances.
- Define a bounded browser record, expiry, conflict, submission, rollback, and disclosure contract.
- Keep credential-specific surfaces out of browser draft storage and state the limit of that exclusion honestly.
- Reconcile draft persistence with the active chat history and terminal-action recovery contracts.

**Non-Goals:**

- Create a server-side draft service, cross-device sync, draft history, or an API-visible draft inventory.
- Inspect, classify, redact, encrypt, or infer the sensitivity of arbitrary eligible prose.
- Change the existing form submission payloads, API authorization, provider behavior, backend state, or chat durable-outcome state machine.
- Extend persistence to textarea primitives or unlisted forms automatically.

## Decisions

### Decision 1: Use transactional IndexedDB with explicit live and tombstone records

Use an IndexedDB database named `butlers-drafts`, schema version 1, with `drafts`, `chatAttempts`, and `meta` object stores. IndexedDB read-write transactions serialize the read/compare/write operation needed to prevent a stale tab from physically replacing a newer record. `localStorage` cannot provide that guarantee; `sessionStorage` neither coordinates tabs nor survives tab close; a server store would cross a new privacy, auth, API, and database boundary.

Every logical draft record uses this shape:

```ts
type DraftRecordV1 = {
  schemaVersion: 1;
  state: "live" | "tombstone";
  draftKey: string;
  surfaceId: DraftSurfaceId;
  context: DraftContext;
  fields?: Record<string, string | number | boolean | string[] | null>;
  sourceBaseline: string | null;
  contentRevision?: string;
  storeEpoch: number;
  writeRevision: number;
  updatedAt: number;
  expiresAt?: number;
  deletedAt?: number;
  deletionReason?: "empty" | "submitted" | "discarded" | "expired" | "invalid" | "auth_reset";
};
```

`updatedAt`, `expiresAt`, `deletedAt` are UTC epoch milliseconds for expiry and disclosure only; they do not order writes. The mutable stale-writer fence is `(storeEpoch, writeRevision)`. Every distinct in-memory field bundle receives a new random UUID `contentRevision`, and a successful ordinary write stores that caller-supplied identity unchanged. It stays unchanged when compaction rewrites only storage fencing metadata. Tombstones contain neither `contentRevision` nor `fields`, base snapshot, target label, chat text, or other owner content. A non-empty serialized live record is capped at 64 KiB.

`draftKey` is `v1:<surfaceId>:<base64url-sha256(canonical-context)>`. `canonical-context` contains only Decision 3's structural identity dimensions and never a draft field bundle. Hashing also keeps an existing state-store key out of the IndexedDB primary key; the live record retains the target key only where the form already needs it.

The `meta` store has one `storeEpoch` row. Every write or delete transaction reads that row and the addressed draft before it acts. A hook schedules persistence immediately after each edit. At most one transaction per hook is in flight; edits arriving during it coalesce into one latest follow-up transaction. Intentional panel/dialog close and in-app navigation use the bounded Decision 5 flow before unmount. This preserves the latest accepted field bundle without placing synchronous storage in the keystroke path. A browser/process crash may lose an uncommitted transaction; no web storage mechanism can prove otherwise, so restore promises apply to committed and read-back content revisions.

`chatAttempts` holds one live record per existing immutable `message_id`: submitted draft key and immutable `submittedContentRevision`, submitted field snapshot, butler, nullable conversation ID, attempt state, mutable store fence, and 24-hour expiry. It uses the same conditional-write, tombstone, and epoch protocol as `drafts`; `contentRevision` identifies a browser field snapshot and does not create a second message-attempt identity. Separating attempts from the current live draft lets late durable completion retire the submitted attempt without deleting newer text.

### Decision 2: Eligibility is a field-level allowlist

Eligibility is registered at the owning surface. Shared `Input` and `Textarea` primitives never persist by themselves. This prevents a new credential form from inheriting storage merely because it reuses a control.

The approved candidate allowlist is exact:

| Surface ID | Existing user surface | Eligible field bundle |
|---|---|---|
| `chat.message` | Butler-detail `ChatPanel` and `FloatingChatWidget` | message text only; page context and the context-chip choice are recomputed and remain in memory |
| `health.condition` | Add/edit condition | name, status, diagnosed date, notes |
| `health.meal` | Add/edit meal | type, description, eaten time, nutrition fields, notes |
| `health.measurement` | Add/edit measurement | type, active measurement values, generic JSON value, measured time, notes |
| `health.medication` | Add/edit medication | name, dosage, frequency, schedule, active state, notes |
| `health.research` | Add/edit research | title, content, tags, source URL |
| `health.symptom` | Add/edit symptom | name, severity, occurrence time, notes |
| `schedule.form` | Add/edit schedule | name, cron, dispatch mode, prompt, job name, job arguments, complexity |
| `approvals.rule.create` | Create standing rule | tool name, description, argument constraints, max uses, expiry days |
| `relationship.interaction` | Log interaction | interaction type, summary |
| `relationship.gift` | Capture gift idea | description, occasion |
| `relationship.reach-out` | Draft a reach-out | message, channel |
| `relationship.note` | Add a note | content |
| `chronicles.correction` | Submit episode correction | corrected title when currently rendered, privacy choice, note |
| `butler-state.set` | Set new butler-state value | key, JSON value |
| `butler-state.edit` | Edit existing butler-state value | target key, JSON value |
| `calendar.user-event` | Create/edit user event dialog | source, title, start, end, timezone, description, location, selected entity IDs |
| `calendar.butler-event` | Create/edit butler event dialog | butler, event kind, title, start, end, timezone, recurrence, cron, until boundary and enabled choices |

The table includes the six health forms and every form family named in `bu-itw51`; it does not narrow the outcome to chat. Calendar inline description editing remains outside this change because it already has a direct save-on-blur contract rather than an unsent submit lifecycle. Model prompt, MCP/config, and ingestion-rule editors are unlisted and remain ineligible pending their own privacy and lifecycle review.

The entire `/secrets` subtree, provider configuration drawers, password controls, and semantically credential-bearing inputs are forbidden even if a future refactor moves them. A structural guard must fail when code tries to register a forbidden path, surface ID, input type, or credential-purpose field. The guard does not scan entered values.

### Decision 3: Stable keys follow user task identity

Logical identity dimensions are:

| Surface family | Stable context dimensions |
|---|---|
| Chat | butler name plus exact conversation ID, or the literal `new` before creation; both chat components share this identity |
| Health | record kind plus `create`, or `edit` plus record ID |
| Schedule | owning butler plus `create`, or `edit` plus schedule ID |
| Standing rule | singleton `create` identity |
| Relationship verbs | entity ID plus exact verb |
| Episode correction | episode ID |
| Butler state | butler plus `create`, or `edit` plus the existing state-key identity |
| User calendar | source/calendar lane plus `create`, or `edit` plus provider/event identity |
| Butler calendar | butler lane plus `create`, or `edit` plus event/schedule/reminder identity |

Route pathname, dialog open count, mount order, component name, labels, titles, and other mutable prose never participate. A chat draft therefore survives movement between matching chat surfaces. Floating-chat page context is excluded so a restored message is paired only with the context chip visibly current at send time.

### Decision 4: Persist the task bundle and clear by submitted content identity

For an eligible multi-field form, restoring only the textarea could pair old prose with new dates, modes, or targets. Each record therefore holds the complete eligible bundle in Decision 2. Fields hidden by the current mode remain in the record so mode switching does not destroy in-progress work, but submission continues to use only fields valid for the selected mode.

An empty draft, or a form reset exactly to its current initial values, transactionally replaces the observed live record with the next tombstone. A submit attempt first flushes the current bundle and captures `(draftKey, contentRevision)` as its immutable submission identity. The owning mutation may clear only by a transaction that reads the authoritative current store fence and tombstones a live record whose `contentRevision` still equals the submitted identity. Validation, HTTP, application, authorization, and domain-conflict failures retain the record and visible values.

Draft persistence never blocks a valid form submission beyond 1 second. If the latest content revision cannot be committed and read back by that bound, the existing domain mutation proceeds once from the in-memory values and the UI discloses that reload recovery and later browser cleanup are unconfirmed. Domain failure keeps the in-memory bundle. Domain success follows the separate cleanup outcome below.

The success handler also compares the current in-memory `contentRevision` with the submitted snapshot. Every accepted edit, reset-to-live transition, or recreation receives a new content revision even when its bytes equal an older bundle. If the owner changed the same form after submitting, another tab committed a newer bundle, or deletion/auth reset was followed by recreation, success retires only the completed submission state. It does not clear the newer in-memory bundle or stored record. If deletion wins a race before a queued newer edit, that edit's stale fence fails and enters the explicit conflict flow; it is never silently dropped.

Domain submission success and browser cleanup are separate outcomes. After real domain/chat success, the surface attempts the conditional tombstone for at most 1 second. If commit plus read-back is not confirmed, the domain success remains visible, duplicate submission is disabled, and the surface reports “Browser draft deletion unconfirmed” with Retry discard and Close with deletion unconfirmed. It does not roll back the real effect or claim that browser content was cleared.

For chat, submission allocates the existing immutable client `message_id`, flushes the submitted content revision, and writes its `chatAttempts` snapshot before issuing the request. “Success” means durable acceptance of that exact `message_id`, not completion of the assistant response. A known conversation reconciles the attempt only from an exact match in its bounded loaded message/dashboard-turn read. Accepted evidence runs one transaction across `drafts`, `chatAttempts`, and `meta`: it tombstones the attempt, and tombstones the submitted draft only when that live draft still has `submittedContentRevision`. Compaction may change either record's mutable fence but preserves that content identity. Newer draft text survives. Pending, retryable, rejected, cancelled, ambiguous, unavailable, and unknown evidence preserve the attempt snapshot with the existing honest UI state and no automatic resend.

### Decision 5: Restore automatically when it is safe, keep dismissal explicit

Matching create drafts auto-restore before first edit. Edit drafts capture `sourceBaseline`: an existing server `updated_at`/revision token when available, otherwise a canonical SHA-256 fingerprint of the initial eligible field bundle held only inside the local draft record. If the current source baseline matches, the draft auto-restores. If it differs, current source data renders first and the owner chooses Load draft or Discard draft; no automatic field merge occurs.

A restored surface shows one inline, visible `role="status"` message, “Draft restored”, with a keyboard-operable Discard action. It announces once per restored record revision and does not steal focus from the first editable field.

Dirty eligible forms/dialogs intercept Cancel, Escape, backdrop close, close controls, and in-app route navigation. When the latest `contentRevision` is already committed and read back, the confirmation offers:

- **Keep editing**: close nothing and preserve focus.
- **Keep draft and close**: close only after confirming the stored record still has the latest content revision.
- **Discard and close**: close only after the ordered tombstone commits and reads back.

If the latest form revision is not known durable, Keep draft and close first shows “Saving draft” in a visible status and waits at most 1 second for commit plus read-back. Discard and close uses the same bound for its tombstone. A confirmed operation closes with the corresponding claim. Failure or timeout leaves the form open and switches to the degraded choices in Decision 8; neither label is treated as completed before confirmation.

Chat panels/widgets remain quick to dismiss when the latest content revision is already committed and read back. Otherwise their close path immediately shows “Saving draft”, waits at most 1 second, and closes only on confirmation. Failure or timeout leaves chat open with the same degraded choices as forms. Browser/process termination still cannot wait for asynchronous storage and does not show a native unload prompt; only committed revisions are promised after a crash.

### Decision 6: Serialize compare-and-swap writes and retain ordered tombstones

An ordinary persistence request for either `drafts` or `chatAttempts` carries the mutable fence token last read by that hook. One IndexedDB read-write transaction reads `meta.storeEpoch` and the current key, then:

1. rejects the request as stale when the epoch or write revision differs;
2. otherwise writes the complete bundle and its caller-supplied `contentRevision` at `writeRevision + 1` in the same epoch; and
3. returns the accepted token to the hook before broadcasting a content-free invalidation on `BroadcastChannel("butlers-drafts-v1")`.

Notifications are advisory. Each surface rereads IndexedDB on notification, focus, and visibility regain, so a dropped or reordered broadcast cannot produce a different durable winner. A newly opened tab reads the same committed record.

If the receiving surface is unchanged since its mutable fence, it adopts the authoritative record. If it has local edits, it keeps them visible, suspends every ordinary/coalesced write, and offers **Use this tab** or **Load other draft**. Use this tab is an explicit override transaction that reads the current authoritative record and writes the local bundle at its next write revision with a new content revision. Load other draft adopts the complete authoritative bundle and fence. Further remote commits refresh the offered authoritative candidate without erasing the local candidate. No field-level merge occurs.

Deletion is a state transition, not physical removal. Submit success reads the current mutable fence but authorizes deletion only when the live record's immutable `contentRevision` equals the submitted identity. Empty/reset, Discard, expiry, invalid-record rejection, and auth/reset cleanup replace only the exact observed fence with `state: "tombstone"`, `writeRevision + 1`, a fixed reason, and no content revision. A delayed write based on the deleted fence is stale regardless of transaction completion or notification order. Any recreation receives a new content revision, even for byte-identical values, so a late success cannot clear it.

Tombstones across both content stores compact when either 256 exist or the oldest is 30 days old. One transaction over `meta`, `drafts`, and `chatAttempts` increments `storeEpoch`, rewrites every live record with the new epoch without changing its fields, `contentRevision`, `submittedContentRevision`, write revision, timestamps, or expiry, and removes the old tombstones. Any tab or queued write holding the previous epoch then fails closed even though its per-key tombstone is gone. A success callback uses immutable content identity, so compaction alone cannot prevent unchanged submitted content from clearing. This keeps deletion ordering permanent while bounding retained metadata.

### Decision 7: Browser storage is not an auth or secret-classification boundary

The draft store has no API client, query mutation, telemetry producer, URL integration, event-bus publisher, or provider connector. It reads a record only after the corresponding eligible surface passes existing route/data access and is ready to render. It never treats possession of a local record as identity or authorization evidence.

The forbidden boundary is based on declared surface and field purpose:

- all Secrets/OAuth/provider configuration and copy-once reveal surfaces;
- every `type="password"` control;
- any field registered as credential, API key, token, private key, recovery code, auth session, or equivalent secret material.

Those surfaces create no draft value and no draft-derived metadata. Ordinary notes, messages, JSON, prompts, and descriptions can incidentally contain the same strings. The implementation must not claim to detect or redact them. The owner-approved policy therefore accepts that eligible content is readable to same-origin script and anyone with the same browser profile until discard, reset, expiry, or browser-data removal.

Existing network isolation and optional API-key middleware remain prerequisites for reaching the dashboard and APIs; neither changes the local browser risk. No new login/logout model is introduced. If the project later adds principal switching inside one origin, draft partitioning and logout erasure require a new approved spec before that feature may coexist with this store.

### Decision 8: Fail open for editing and fail closed for restoration

Every storage access, parse, validation, and serialization operation is exception-contained. A failed write keeps the current in-memory bundle. A corrupt, expired, or unsupported record never renders and is conditionally tombstoned when its key and ordering token can be read safely. If the token itself is invalid, one transaction advances the epoch, rewrites valid live records into it, and removes the invalid row; no writer minted under the earlier epoch can resurrect it.

The first denied, blocked, quota, oversize, failed, or unknown storage condition on an affected surface renders a visible `role="status"`: “Draft saving unavailable. Keep this tab open.” Repeated failures neither toast nor re-announce. A successful write followed by a successful read-back of the latest content revision clears the disclosure. Diagnostics may carry only a fixed category (`unavailable`, `blocked`, `timeout`, `quota`, `oversize`, `corrupt`, `unsupported_version`, `expired`, `conflict`) and `surfaceId`; they carry no record, values, fragment, size, target identity, or content-derived fingerprint.

A storage operation is `unknown` after 1 second without a terminal transaction and read-back result. At that bound the UI attempts to abort the transaction, stops waiting, and remains responsive. It never converts timeout into saved or deleted. Known denied/quota/oversize states skip the wait and enter degraded dismissal immediately.

Both form and chat degraded dismissal offer exactly three truthful choices:

- **Keep editing**: remain on the current surface with the complete in-memory values.
- **Retry saving** or **Retry discard**: start the requested operation again, remain open, and apply the same 1-second bound.
- **Close with save unconfirmed** or **Close with deletion unconfirmed**: close immediately after explicit owner choice, while stating respectively “Latest changes may not be recoverable. A browser draft may still appear.” or “A browser draft may remain.”

The normal **Keep draft and close** and **Discard and close** labels do not appear in degraded state because neither outcome is known. There is no indefinite spinner, native unload prompt, or automatic close after a late callback.

Each mounted surface has a unique instance token paired with its exact `draftKey`. Close, route change, target change, and unmount retire that token before another context can mount. Storage and submit callbacks may update UI only when both token and key still match. Durable storage effects remain governed solely by transactional fences and immutable content identity: a late callback cannot set state, announce success, close, or clear a successor surface; a successor independently reads the authoritative store and receives the normal restored, degraded, or conflict presentation.

### Decision 9: Version and expiry cleanup are transactional and bounded

Version 1 readers accept only the exact V1 shape and field names registered for the addressed surface. Unknown fields, invalid types, impossible timestamps, and unsupported versions reject the entire record rather than partially restoring it.

Expiry is sliding: an accepted edit sets `expiresAt = updatedAt + 24 hours`. A read or write that observes an expired live revision opens a read-write transaction and conditionally replaces that revision with a tombstone before returning content. Initialization scans only this IndexedDB database, not unrelated browser storage. Since browser code cannot execute after 24 hours of no visits, expiry means “never render after the deadline and tombstone on the next bounded access,” not a background execution guarantee.

### Decision 10: Persist chat attempt identity and expose the missing new-conversation read prerequisite

Before either chat surface sends, it creates the existing client `message_id` and tries for at most 1 second to atomically bind it to the submitted draft key, immutable `submittedContentRevision`, text snapshot, butler, nullable conversation ID, and expiry in `chatAttempts`. When persistence confirms, it sends that same ID in the existing request field. When persistence fails or is unknown, the existing send still proceeds once with the same in-memory ID and content identity; the current instance retains them for outcome handling and states that reload recovery is unavailable. No draft-specific attempt ID or text comparison exists.

For a known conversation ID, reload reconciliation may consume the existing bounded message/dashboard-turn response only when it contains the exact message ID. It does not page or scan unbounded history to prove absence. A `conversation_created` event proves that a new-conversation message was persisted and supplies its conversation ID. One transaction records that association, tombstones the attempt, and tombstones the `new` draft only when it still has `submittedContentRevision`. It never copies submitted text into the real conversation as a fresh unsent draft, and it preserves any newer `contentRevision`. Pending evidence remains pending. Retryable, rejected, cancelled, ambiguous, missing, or unavailable evidence remains retained with the corresponding existing recovery presentation.

There is no current content-blind read endpoint that accepts `(butler, message_id)` and returns its conversation ID plus durable dashboard-turn outcome. The conversation list and message-list APIs either require a known conversation ID or an unbounded pagination scan; full-text message search is content-driven; the specified `retry-ingress` and current Stop endpoints are mutating controls and cannot be used as status reads. Therefore an attempt absent from the bounded loaded messages, including one that reloads before observing `conversation_created`, must remain visibly outcome-unknown, preserve its message ID and text snapshot, and offer no automatic resend. Complete automatic reload reconciliation is explicitly blocked by backend prerequisite `bu-z4eqza`: the separately owner-approved and implemented read-only exact-message capability. This change does not name its route, payload, authorization, or implementation and cannot become implementation-ready for the full chat outcome until that prerequisite exists.

#### Separate prerequisite packet: content-blind exact-message attempt resolver

**Dedupe key:** `dashboard-content-blind-message-attempt-resolver`

**Tracking prerequisite:** `bu-z4eqza`

**Existing seam and evidence:** Both chat clients already send an immutable client `message_id` in `CreateConversationRequest` / `SendMessageRequest`; the conversation write path persists that UUID before SSE streaming. `GET .../conversations/{conversation_id}/messages` exposes the durable message/dashboard-turn projection only after the conversation ID is known and only within a bounded page. `GET /api/conversations/messages/search` is content-driven, while `retry-ingress` and Stop are mutating controls. No current read resolves an exact owner attempt from `(butler, message_id)` alone.

**Required metadata and authority semantics:** A separately specified read capability must use the existing dashboard access boundary, accept the butler plus exact immutable message ID as identity, and return only content-blind attempt metadata sufficient to distinguish found, genuinely absent, and source-unavailable outcomes. Found metadata must include the exact message ID, conversation ID, and existing sanitized durable dashboard-turn outcome/version needed to reconcile acceptance, pending, retryable, rejected, cancelled, completed, or ambiguous state. It must never return message text, page context, raw error/provider data, credentials, or a caller-asserted owner identity.

**Non-goals:** No server-side browser draft, draft text upload, text search, unbounded conversation scan, second attempt identity, automatic retry/replay, Stop, ingress recovery, LLM session, provider call, database rewrite, or new authorization model.

**Gate distinction:** Recording and reviewing this prerequisite is sufficient to complete the present client-policy proposal. Landing an owner-approved implementation of it, together with the active durable message/dashboard-turn projection, blocks implementation readiness for the complete chat-draft outcome. The form/dialog draft policy does not depend on it and may be allocated separately only after owner approval and a non-overlapping work split.

## Risks / Trade-offs

- **[Risk] Eligible health, relationship, chat, state, and calendar prose is sensitive even when it is not a credential.** → Keep it browser-origin-local, expire it after 24 hours, provide one-click discard, disclose the shared-profile boundary, and add no server or telemetry path.
- **[Risk] An owner can paste a credential into an ordinary eligible field.** → State this limitation explicitly; rely on surface-purpose exclusion rather than a misleading content classifier.
- **[Risk] IndexedDB can be unavailable, quota-limited, blocked, or hung.** → Cap records at 64 KiB, keep one coalescing transaction queue per hook, bound controlled-unmount confirmation to 1 second, preserve in-memory input, and offer truthful degraded close choices.
- **[Risk] Restoring an edit over changed server data could overwrite newer facts.** → Compare the captured baseline, require an explicit load choice on mismatch, and keep the normal domain conflict UI authoritative.
- **[Risk] Two tabs can produce competing complete form states.** → Serialize conditional writes in IndexedDB, suspend ordinary writes during conflict, require explicit override or load, and never merge fields.
- **[Risk] A stale writer can outlive tombstone retention.** → Compact tombstones only by transactionally advancing the store epoch, which permanently fences every token minted under the prior epoch.
- **[Risk] A durable message can be absent from the bounded loaded conversation response, especially when the client misses a new conversation ID.** → Persist the existing message ID, render the attempt as unknown, forbid content search/unbounded scans/automatic replay, and block full implementation on the separate read-only exact-message prerequisite.
- **[Risk] Rollback can leave V1 browser records behind.** → Transactionally tombstone live content and advance the epoch before unregistering consumers; delete the database only after connections close.

## Migration Plan

1. Land and owner-approve the separate content-blind exact-message read prerequisite, plus the active durable message/dashboard-turn projection this design consumes; otherwise keep the full chat outcome blocked.
2. Introduce the V1 IndexedDB store/hook and its privacy, corruption, quota, expiry, conflict, content-identity, stale-writer fence, tombstone, epoch-compaction, timeout, and late-callback tests without registering consumers.
3. Register both chat surfaces under their shared conversation identity, then prove message-ID binding, new-to-created key movement, reload reconciliation, compaction-safe conditional acceptance clear, failure/unknown retention, and bounded degraded close against existing recovery seams.
4. Register the exact Decision 2 form/dialog allowlist in cohesive domain slices, including normal and degraded dirty-dismiss handling and representative source-baseline mismatch coverage.
5. Add a structural allowlist/forbidden-surface gate and documentation that names eligible fields and the no-content-classification boundary.
6. Run the focused frontend suites, lint, typecheck, knip, repository guards, and terminal hosted CI on the exact implementation head before any release.

Rollback first uses one IndexedDB transaction to advance the epoch and tombstone every live content record, then removes consumer registrations and the shared reader/writer. Database deletion is attempted only after app connections close; an older build ignores any remaining content-free V1 tombstones. No server rollback or data migration is required.

## Owner Review Gate

Owner approval is required for this exact set of product/privacy choices:

1. Explicit allowlist and field bundles in Decision 2, including ordinary health, relationship, state JSON, schedule prompt, and chat prose.
2. Browser-origin transactional IndexedDB, no server sync or encryption, and acceptance of same-profile/same-origin readability.
3. No content classification: secret-bearing surfaces are forbidden, while secret-looking text in an eligible ordinary field can persist.
4. Sliding 24-hour expiry and 64 KiB per-record limit.
5. Automatic safe restore, one-click discard, three-way dirty-form dismissal, a 1-second storage-operation bound for form/chat close and submit, valid submission continuation, and truthful degraded save/deletion choices.
6. Compare-and-swap stale-writer fencing, separate immutable content revisions, content-free tombstones, epoch compaction, and an explicit choice before replacing current-tab edits.
7. Clear only the exact submitted content revision on proven success, including across unrelated compaction; retain newer/reset/recreated edits and failed or unknown chat attempts without adding an unsafe automatic resend.
8. Keep the complete chat implementation blocked on `bu-z4eqza`, whose separately approved content-blind read must reconcile a message absent from the bounded loaded response, including a missed new-conversation identity after reload.

Until the owner approves the exact artifact digest or commit, `bu-itw51` remains a draft prerequisite and no implementation is ready.
