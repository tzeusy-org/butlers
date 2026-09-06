## Context

This is a medium, user-facing specification change. The current dashboard keeps composer and form state in mounted React components. `ChatPanel` and `FloatingChatWidget` clear their input before their asynchronous send path resolves, while eligible dialogs reset or unmount their field state when they close. The active `durable-dashboard-terminal-action-recovery` change preserves a same-thread in-memory draft during history read failure; it does not define browser persistence.

The dashboard already uses `localStorage` for browser preferences and treats storage exceptions as recoverable. The deployment trust model is single-user, localhost-bound with Tailscale-mediated external access and optional API-key authentication. That boundary protects access to the dashboard service. It does not encrypt or authenticate data in a shared browser profile.

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

### Decision 1: Use a small versioned `localStorage` store

Use browser `localStorage` because the outcome requires recovery after component unmount, route navigation, panel close, and browser reload. `sessionStorage` cannot support cross-tab coordination and disappears with the tab; IndexedDB adds asynchronous transaction and migration complexity without a size or query need; a server store would cross a new privacy, auth, API, and database boundary.

Every logical draft uses this record shape:

```ts
type DraftRecordV1 = {
  schemaVersion: 1;
  surfaceId: DraftSurfaceId;
  context: DraftContext;
  fields: Record<string, string | number | boolean | string[] | null>;
  baseRevision: string | null;
  updatedAt: number;
  expiresAt: number;
  writerId: string;
};
```

`updatedAt` and `expiresAt` are UTC epoch milliseconds. `writerId` is a random per-tab UUID and contains no owner data. Each write replaces one complete record; partial field patches are not persisted. Storage writes debounce for at most 250 ms after an edit and synchronously flush the latest in-memory record on eligible-surface unmount and `pagehide`. A non-empty serialized record is capped at 64 KiB.

Storage keys use `butlers:draft:v1:<surfaceId>:<encoded-context>`. `encoded-context` is a canonical encoding of the structural key dimensions in Decision 3; it contains no draft field values. System-issued IDs may appear because they already define the target. When a target identity is itself owner-authored, such as an existing state-store key, the encoding uses its SHA-256 digest rather than plaintext. The record value retains the target identity only where the form already needs it.

An implementation may split records across keys or keep a versioned index only if it preserves per-record atomic replacement, cross-tab ordering, content-blind diagnostics, and the same observable keys and lifecycle. This flexibility permits mechanical storage layout changes without changing product behavior.

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

### Decision 4: Persist the task bundle, clear on proven success

For an eligible multi-field form, restoring only the textarea could pair old prose with new dates, modes, or targets. Each record therefore holds the complete eligible bundle in Decision 2. Fields hidden by the current mode remain in the record so mode switching does not destroy in-progress work, but submission continues to use only fields valid for the selected mode.

An empty draft, or a form reset exactly to its current initial values, deletes the record. A submit attempt does not delete it. The owning mutation clears the record only after its existing success contract is satisfied. Validation, HTTP, application, authorization, and domain-conflict failures retain the record and visible values.

For chat, “success” means durable acceptance of the exact owner message, not completion of the assistant response. A send whose outcome is unknown retains the text but follows the existing durable-status and exact-message recovery UI; retention must not introduce an automatic resend button or new message identity. This changes the present pre-send clear timing while preserving the baseline promise that a successfully sent message clears the composer.

### Decision 5: Restore automatically when it is safe, keep dismissal explicit

Matching create drafts auto-restore before first edit. Edit drafts capture `baseRevision`: an existing server `updated_at`/revision token when available, otherwise a canonical SHA-256 fingerprint of the initial eligible field bundle held only inside the local draft record. If the current source baseline matches, the draft auto-restores. If it differs, current source data renders first and the owner chooses Load draft or Discard draft; no automatic field merge occurs.

A restored surface shows one inline, visible `role="status"` message, “Draft restored”, with a keyboard-operable Discard action. It announces once per restored record revision and does not steal focus from the first editable field.

Dirty eligible forms/dialogs intercept Cancel, Escape, backdrop close, close controls, and in-app route navigation. The confirmation offers:

- **Keep editing**: close nothing and preserve focus.
- **Keep draft and close**: close while retaining the stored record.
- **Discard and close**: delete that record, then close.

Chat panels/widgets remain quick to dismiss because their draft restores automatically; they do not add a close confirmation. Browser/tab close remains best-effort through the synchronous `pagehide` flush and does not show a native unload prompt.

### Decision 6: Order storage globally, never overwrite focused local edits

Record ordering is lexicographic `(updatedAt, writerId)`, with larger values winning. A tab writes a complete record, and the browser `storage` event notifies peers.

- If the receiving surface has not changed since its last restore, it applies the newer complete record and announces the restored revision once.
- If it has local edits, it keeps them visible and offers **Use this tab** or **Load other draft**. Use this tab writes a new complete revision with a later tuple. Load other draft replaces the whole bundle.
- Equal timestamps resolve by `writerId`, so every tab selects the same stored winner.
- No field-level merge occurs. It would combine values that may never have been valid together.

The wall clock may move backward. A writer therefore selects `updatedAt = max(Date.now(), previousUpdatedAt + 1)` for that key. This makes same-browser ordering monotonic without inventing a server clock.

### Decision 7: Browser storage is not an auth or secret-classification boundary

The draft store has no API client, query mutation, telemetry producer, URL integration, event-bus publisher, or provider connector. It reads a record only after the corresponding eligible surface passes existing route/data access and is ready to render. It never treats possession of a local record as identity or authorization evidence.

The forbidden boundary is based on declared surface and field purpose:

- all Secrets/OAuth/provider configuration and copy-once reveal surfaces;
- every `type="password"` control;
- any field registered as credential, API key, token, private key, recovery code, auth session, or equivalent secret material.

Those surfaces create no draft value and no draft-derived metadata. Ordinary notes, messages, JSON, prompts, and descriptions can incidentally contain the same strings. The implementation must not claim to detect or redact them. The owner-approved policy therefore accepts that eligible content is readable to same-origin script and anyone with the same browser profile until discard, reset, expiry, or browser-data removal.

Existing network isolation and optional API-key middleware remain prerequisites for reaching the dashboard and APIs; neither changes the local browser risk. No new login/logout model is introduced. If the project later adds principal switching inside one origin, draft partitioning and logout erasure require a new approved spec before that feature may coexist with this store.

### Decision 8: Fail open for editing and fail closed for restoration

Every storage access, parse, validation, and serialization operation is exception-contained. A failed write keeps the current in-memory bundle. A corrupt, expired, or unsupported record never renders and is removed when possible.

The first unavailable condition on an affected surface renders a visible `role="status"`: “Draft saving unavailable. Keep this tab open.” Repeated failures neither toast nor re-announce. A successful write followed by a successful read-back clears the disclosure. Diagnostics may carry only a fixed category (`unavailable`, `quota`, `oversize`, `corrupt`, `unsupported_version`, `expired`, `conflict`) and `surfaceId`; they carry no record, values, fragment, size, target identity, or content-derived fingerprint.

### Decision 9: Version and expiry cleanup are lazy and bounded

Version 1 readers accept only the exact V1 shape and field names registered for the addressed surface. Unknown fields, invalid types, impossible timestamps, and unsupported versions reject the entire record rather than partially restoring it.

Expiry is sliding: an accepted edit sets `expiresAt = updatedAt + 24 hours`. Reads and writes remove the addressed expired record before use. Initialization may scan only the `butlers:draft:` namespace to remove expired/unsupported records; it must not inspect unrelated local storage. Since browser code cannot execute after 24 hours of no visits, expiry means “never render after the deadline and remove on the next bounded access,” not a background deletion guarantee.

## Risks / Trade-offs

- **[Risk] Eligible health, relationship, chat, state, and calendar prose is sensitive even when it is not a credential.** → Keep it browser-origin-local, expire it after 24 hours, provide one-click discard, disclose the shared-profile boundary, and add no server or telemetry path.
- **[Risk] An owner can paste a credential into an ordinary eligible field.** → State this limitation explicitly; rely on surface-purpose exclusion rather than a misleading content classifier.
- **[Risk] `localStorage` is synchronous and quota-limited.** → Cap records at 64 KiB, debounce normal writes, flush only the latest record, and preserve in-memory input with visible degradation.
- **[Risk] Restoring an edit over changed server data could overwrite newer facts.** → Compare the captured baseline, require an explicit load choice on mismatch, and keep the normal domain conflict UI authoritative.
- **[Risk] Two tabs can produce competing complete form states.** → Use deterministic last-write ordering while protecting a dirty focused tab from silent replacement; never merge fields.
- **[Risk] Rollback can leave V1 browser records behind.** → Version keys and records, ignore unsupported versions, and make cleanup safe and bounded.

## Migration Plan

1. Introduce the V1 store/hook and its privacy, corruption, quota, expiry, conflict, and lifecycle tests without registering consumers.
2. Register both chat surfaces under their shared conversation identity, then prove durable-acceptance clear and failure/unknown retention against existing recovery seams.
3. Register the exact Decision 2 form/dialog allowlist in cohesive domain slices, including dirty-dismiss handling and representative source-baseline mismatch coverage.
4. Add a structural allowlist/forbidden-surface gate and documentation that names eligible fields and the no-content-classification boundary.
5. Run the focused frontend suites, lint, typecheck, knip, repository guards, and terminal hosted CI on the exact implementation head before any release.

Rollback removes consumer registrations first, then the shared reader/writer. V1 records may be deleted by a best-effort namespace cleanup; an older build ignores them. No server rollback or data migration is required.

## Owner Review Gate

Owner approval is required for this exact set of product/privacy choices:

1. Explicit allowlist and field bundles in Decision 2, including ordinary health, relationship, state JSON, schedule prompt, and chat prose.
2. Browser-origin `localStorage`, no server sync or encryption, and acceptance of same-profile/same-origin readability.
3. No content classification: secret-bearing surfaces are forbidden, while secret-looking text in an eligible ordinary field can persist.
4. Sliding 24-hour expiry and 64 KiB per-record limit.
5. Automatic safe restore, one-click discard, three-way dirty-form dismissal, and visible non-blocking storage-failure disclosure.
6. Deterministic newest-write storage ordering with an explicit choice before replacing current-tab edits.
7. Clear only on proven successful submission; retain on failure or unknown chat outcome without adding an unsafe automatic resend.

Until the owner approves the exact artifact digest or commit, `bu-itw51` remains a draft prerequisite and no implementation is ready.
