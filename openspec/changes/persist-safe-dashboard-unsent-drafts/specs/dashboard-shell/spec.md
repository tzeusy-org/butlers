## ADDED Requirements

### Requirement: Explicit Eligible Form and Dialog Draft Surfaces

The dashboard SHALL persist in-progress values only for this explicit form/dialog allowlist: health condition, meal, measurement, medication, research, and symptom create/edit forms; schedule create/edit; standing-rule creation; relationship log-interaction, gift-idea, draft-reach-out, and note forms; episode correction; new and existing butler-state values; and user-calendar and butler-calendar event create/edit dialogs. Every other form or text control SHALL remain ineligible until an approved spec adds it.

ID: REQ-dashboard-shell-001
Source: heart-and-soul/vision.md § What Success Looks Like; proposal.md § Proposed owner policy; design.md Decision 2
Scope: v1-mandatory

#### Scenario: Health forms preserve complete in-progress values

- **WHEN** the owner edits an eligible condition, meal, measurement, medication, research, or symptom form and leaves before successful submission
- **THEN** the complete in-progress values for that exact health form identity remain restorable
- **AND** create and edit drafts remain distinct
- **AND** edit drafts for different records remain distinct

#### Scenario: Schedule and standing-rule forms are eligible

- **WHEN** the owner edits a schedule create/edit form or the create-standing-rule dialog and leaves before successful submission
- **THEN** the complete in-progress values for that exact form identity remain restorable
- **AND** schedule drafts remain isolated by owning butler, create/edit mode, and schedule identity
- **AND** the standing-rule create draft does not appear on any other approval surface

#### Scenario: Relationship and episode forms are eligible

- **WHEN** the owner edits a relationship log-interaction, gift-idea, draft-reach-out, note, or episode-correction form and leaves before successful submission
- **THEN** its complete in-progress values remain restorable for the same action and entity or episode identity
- **AND** drafts do not cross between entities, episodes, or action types
- **AND** an episode that is currently sensitive never restores a corrected-title field that the form does not render

#### Scenario: Butler-state forms are eligible

- **WHEN** the owner edits a new or existing butler-state value and leaves before successful submission
- **THEN** its key and JSON value remain restorable for the same butler and create/edit identity
- **AND** drafts for different butlers or existing state keys remain distinct

#### Scenario: Calendar event dialogs are eligible

- **WHEN** the owner edits a user-calendar or butler-calendar event create/edit dialog and leaves before successful submission
- **THEN** the complete in-progress event values remain restorable for the same calendar lane, operation, and event identity
- **AND** user-event and butler-event drafts remain distinct
- **AND** inline event-description autosave outside those dialogs remains governed by its existing save-on-blur behavior

#### Scenario: Unlisted fields do not opt themselves in

- **WHEN** a new or existing dashboard form is absent from the approved allowlist
- **THEN** typing in that form creates no browser draft record
- **AND** reusing a shared input or textarea primitive does not make the form eligible

### Requirement: Stable Browser Draft Identity and Lifecycle

Eligible drafts SHALL use versioned transactional IndexedDB records under the dashboard origin, keyed only by a stable surface identifier, operation mode, and non-content domain identifiers. Every distinct in-memory field bundle SHALL receive an immutable random content revision that accepted persistence stores unchanged, distinct from its mutable store-epoch/write-revision stale-writer fence; compaction SHALL preserve content revision while changing the fence. Draft content SHALL expire 24 hours after its last accepted edit, SHALL be replaced by an ordered content-free tombstone when all eligible values are empty or reset to their initial values, and SHALL NOT exceed 64 KiB serialized.

ID: REQ-dashboard-shell-002
Source: dashboard-shell § Utility Infrastructure / Local settings resilience; design.md Decisions 1 and 3
Scope: v1-mandatory

#### Scenario: Remount and browser reload preserve an eligible create draft

- **WHEN** an eligible create-form draft is non-empty and valid browser storage remains available
- **THEN** closing, unmounting, navigating, or reloading the dashboard preserves the latest accepted draft for its stable identity
- **AND** a component remount or route-layout change does not create a new identity

#### Scenario: Record keys contain no owner-authored content

- **WHEN** a browser draft record is written
- **THEN** its key contains only the versioned namespace, approved surface identifier, operation mode, and required non-content domain identifiers
- **AND** no field value, label, title, free-text fragment, content hash, or other content-derived identifier appears in the key

#### Scenario: Empty or reset form removes content with an ordered tombstone

- **WHEN** every eligible value is empty or equals the form's current initial value
- **THEN** one read-write transaction replaces the current live fence with the next content-free tombstone fence
- **AND** later reopening does not show a restored-draft affordance
- **AND** a delayed writer based on the replaced fence cannot recreate the deleted content

#### Scenario: Sliding expiry removes stale drafts

- **WHEN** 24 hours elapse after the last accepted edit to a stored draft
- **THEN** the draft is treated as expired and its content is replaced by the next ordered tombstone before any content is rendered
- **AND** expiry of one record does not remove a newer or unrelated draft
- **AND** a concurrent transaction can expire only the exact revision it observed

#### Scenario: Unsupported or corrupt record fails closed

- **WHEN** a stored record has an unsupported schema version, invalid shape, invalid timestamps, or malformed serialized data
- **THEN** no value from that record is rendered into a form
- **AND** its content is replaced by an ordered tombstone when the key and ordering token can be read safely
- **AND** an unreadable ordering token triggers an epoch-advance transaction before the invalid record is removed
- **AND** the owner is told once that the saved draft could not be restored

#### Scenario: Oversize draft stays only in the current mount

- **WHEN** an eligible record would exceed 64 KiB serialized
- **THEN** the current form remains editable and keeps the complete in-memory values
- **AND** that record is not written partially or by silently truncating content
- **AND** the unavailable-recovery disclosure is shown

### Requirement: Draft Restore, Discard, Dismiss, and Submit Experience

The dashboard SHALL make recovered state and persistence loss visible without interrupting typing. Create-form drafts whose identity matches SHALL restore automatically; edit-form drafts SHALL restore automatically only when their recorded source baseline still matches the current record. Every restored draft SHALL expose an accessible Discard action. Every dirty eligible form/dialog dismissal SHALL require an explicit choice, and no surface SHALL claim saved or discarded content until the latest requested write or tombstone is committed and read back.

ID: REQ-dashboard-shell-003
Source: dashboard-design-language § Composure Doctrine, Interface Copy, and Interaction Affordances; th-design design-bar Recovery and Accessible defaults; design.md Decisions 4 and 5
Scope: v1-mandatory

#### Scenario: Matching draft restores with a quiet affordance

- **WHEN** an eligible create draft or baseline-matching edit draft opens within its lifetime
- **THEN** its values are restored before the owner begins editing
- **AND** a visible `role="status"` region announces "Draft restored" once
- **AND** an adjacent keyboard-operable Discard action is available without moving focus away from the first editable field

#### Scenario: Changed edit baseline requires a choice

- **WHEN** an edit draft exists but the current source record no longer matches the baseline captured with that draft
- **THEN** current source values render without being overwritten
- **AND** the UI offers explicit Load draft and Discard draft actions
- **AND** no field is merged automatically from the stale draft

#### Scenario: Dirty dialog dismissal preserves owner intent

- **WHEN** the owner tries to dismiss a dirty eligible form/dialog by Cancel, Escape, backdrop interaction, close control, or route navigation
- **THEN** the surface remains open and presents Keep editing, Keep draft and close, and Discard and close actions
- **AND** Keep draft and close closes only after the latest content revision is committed and read back
- **AND** Discard and close closes only after the ordered tombstone is committed and read back
- **AND** failure or timeout switches to the degraded dismissal contract without closing

#### Scenario: Successful submission clears only the submitted content revision

- **WHEN** the existing mutation contract proves successful submission of content revision C from draft K
- **THEN** one transaction replaces K with a tombstone only when its authoritative live content revision is still C
- **AND** the in-memory values and restored-state affordance clear only when they still represent C in the same surface instance and context
- **AND** no other draft key is changed

#### Scenario: Unrelated compaction preserves successful clear identity

- **WHEN** content revision C is submitted, unrelated tombstones trigger store-epoch compaction, and C then succeeds without an intervening edit to K
- **THEN** compaction preserves C while updating only K's mutable stale-writer fence
- **AND** the success transaction reads the current fence and tombstones the unchanged live C
- **AND** the submitted content does not reappear as an unsent draft

#### Scenario: Late success retains newer, reset, or recreated content

- **WHEN** submission of content revision C succeeds after a same-tab or cross-tab edit, reset, discard, expiry, auth reset, or recreation has changed K's live or tombstone state
- **THEN** the success handler does not delete, clear, overwrite, or resurrect the successor state
- **AND** byte-identical recreated content remains protected because it has a new content revision
- **AND** only the submitted operation receives its existing success feedback

#### Scenario: Failed submission retains the draft

- **WHEN** validation, transport, authorization, conflict, or application failure prevents successful submission
- **THEN** the form remains populated and draft K remains stored
- **AND** the existing failure or conflict UI remains authoritative
- **AND** retrying does not create a second draft identity

#### Scenario: Repeated restore and discard are safe

- **WHEN** restore or discard handling is triggered more than once for the same content revision and store fence
- **THEN** values are applied at most once and deletion remains a no-op after the record is absent
- **AND** duplicate status announcements or duplicate submissions are not produced

### Requirement: Transactional Cross-Tab Draft Ordering and Deletion

Every draft write, override, submission clear, discard, expiry, auth reset, invalid-record rejection, and tombstone compaction SHALL run as a single IndexedDB read-write transaction against the authoritative store epoch and per-key write revision. An ordinary write SHALL succeed only when its mutable fence matches the current live fence and SHALL store the caller's immutable content revision unchanged; deletion SHALL write the next content-free tombstone fence. A stale transaction SHALL report a conflict and SHALL NOT overwrite or resurrect the authoritative record. Tombstone compaction SHALL advance the store epoch transactionally before removing tombstones while preserving every live content revision.

ID: REQ-dashboard-shell-004
Source: design.md Decision 6
Scope: v1-mandatory

#### Scenario: Accepted remote revision refreshes an untouched local form

- **WHEN** another tab commits a higher authoritative write revision for the same key and the current tab has not changed since its last restore
- **THEN** a cross-tab notification or the next focus/visibility reconciliation loads and applies the higher complete record and its content revision
- **AND** the updated restore status is announced once

#### Scenario: Newer record conflicts with current-tab edits

- **WHEN** another tab commits a higher authoritative write revision for the same key after the current tab has made local edits
- **THEN** the current tab keeps its visible values and shows that the draft changed in another tab
- **AND** explicit Use this tab and Load other draft actions are offered
- **AND** ordinary debounced writes remain suspended while the choice is pending
- **AND** Use this tab explicitly writes over the current authoritative record with a new content revision while Load other draft adopts it

#### Scenario: Inverted write completion cannot regress storage

- **WHEN** transactions based on mutable fence F race and the transaction carrying the later user edit begins or completes in either order
- **THEN** at most one ordinary compare-and-swap from F succeeds
- **AND** the loser observes the committed revision and enters conflict instead of physically storing a lower or stale state
- **AND** a newly opened tab reads the same authoritative winner

#### Scenario: Ordered deletion defeats a delayed writer

- **WHEN** submit success, Discard, reset, expiry, auth reset, or invalid-record rejection commits tombstone T after a writer captured an older live fence
- **THEN** the delayed ordinary writer fails its base-token comparison against T
- **AND** no notification ordering or tab lifetime can restore the deleted content automatically

#### Scenario: Tombstone compaction fences old writers

- **WHEN** 256 tombstones exist or the oldest tombstone reaches 30 days
- **THEN** one transaction increments the store epoch, carries live records and their unchanged content revisions into that epoch, and removes prior tombstones
- **AND** any delayed writer holding the prior epoch is rejected even though its per-key tombstone was compacted
- **AND** compaction changes no submitted-content identity, live draft content, or expiry

#### Scenario: Unrelated keys do not conflict

- **WHEN** another tab writes or discards a different draft key
- **THEN** the current form and its conflict state remain unchanged

### Requirement: Draft Privacy, Authorization, and Forbidden Surfaces

Browser draft persistence SHALL be an explicit client-side recovery aid, not an authentication or authorization boundary. The persistence mechanism SHALL read or render content only inside a currently accessible eligible surface and SHALL NOT transmit draft records through an API, URL, log, telemetry event, event bus, error report, or provider call except when the owner invokes the surface's existing submission action.
Dedicated Secrets and provider-configuration surfaces, password controls, and fields whose declared purpose is a credential, API key, token, private key, recovery code, auth session, or copy-once revealed value SHALL never be eligible, and the system SHALL make no claim that it detects secret material pasted into an otherwise eligible ordinary prose or JSON field.

ID: REQ-dashboard-shell-005
Source: heart-and-soul/security.md § Credential Tiers and Constraints; dashboard-admin-gateway § Defense-in-Depth API-Key Authentication; craft-and-care/security-and-secrets.md; design.md Decisions 2 and 7
Scope: v1-mandatory

#### Scenario: Dedicated secret-bearing controls write no draft evidence

- **WHEN** the owner types into `/secrets`, a provider-configuration drawer, a password control, or a field declared to carry credential material
- **THEN** the draft persistence mechanism writes no value for that field
- **AND** it writes no content-derived key, hash, size, timestamp, or presence marker for that field

#### Scenario: Eligible prose is not content-classified

- **WHEN** the owner enters secret-looking text into an eligible ordinary prose or JSON field
- **THEN** the draft mechanism treats it as ordinary eligible content and may persist it browser-locally
- **AND** product copy and documentation do not claim secret detection, redaction, or credential-safe classification
- **AND** Discard and expiry remain the mechanisms that remove it

#### Scenario: Draft restore waits for the eligible surface

- **WHEN** browser storage contains a draft key
- **THEN** its content is not exposed through a global draft list, preload response, server endpoint, or unauthorized route
- **AND** it is read and rendered only after the matching eligible surface is available under existing dashboard access controls
- **AND** possession of the record is never accepted as proof of owner identity or permission

#### Scenario: Existing access posture is not weakened

- **WHEN** dashboard API-key authentication is enabled, disabled, or rejected under its existing contract
- **THEN** draft persistence neither bypasses nor changes that API behavior
- **AND** draft storage remains scoped to the browser profile and dashboard origin rather than the authenticated API principal

### Requirement: Honest Degradation When Browser Persistence Is Unavailable

Storage denial, blocked database access, quota exhaustion, oversize refusal, serialization failure, browser API unavailability, or an operation that does not settle within 1 second SHALL never crash an eligible surface, block input, clear in-memory values, wait indefinitely, or fabricate successful persistence or deletion. The dashboard SHALL disclose loss or uncertainty of cross-unmount recovery once per affected surface state, SHALL offer bounded truthful dismissal to both forms and chat, and SHALL prevent late callbacks from changing a successor surface or context.

ID: REQ-dashboard-shell-006
Source: dashboard-shell § Utility Infrastructure / Local settings resilience; dashboard-design-language § Composure Doctrine and Interface Copy; craft-and-care/security-and-secrets.md; design.md Decision 8
Scope: v1-mandatory

#### Scenario: Storage write is denied, rejected, or oversize while typing

- **WHEN** browser storage is denied, blocked, unavailable, over quota, or rejects an oversize or invalid write while an eligible surface is being edited
- **THEN** the current mount keeps the complete in-memory values and remains usable
- **AND** a visible `role="status"` message says "Draft saving unavailable. Keep this tab open."
- **AND** the failure is not reported as a successful save
- **AND** repeated failures do not produce repeated toasts or announcements

#### Scenario: Pending close reaches a bounded unknown outcome

- **WHEN** a form or chat close requests save or deletion and its transaction plus read-back does not settle within 1 second
- **THEN** the surface stops waiting, remains open and responsive, and reports the requested outcome as unconfirmed
- **AND** it attempts to abort the pending transaction without treating abort as proof of save or deletion
- **AND** no indefinite spinner, automatic close, or normal saved/discarded label is shown

#### Scenario: Degraded form dismissal stays truthful

- **WHEN** the latest form content is not known durable or a requested tombstone is unconfirmed
- **THEN** the form offers Keep editing, Retry saving or Retry discard, and Close with save unconfirmed or Close with deletion unconfirmed
- **AND** the close-with-uncertainty action states that latest changes may not be recoverable or that a browser draft may remain
- **AND** Keep draft and close and Discard and close are unavailable until their outcomes are committed and read back

#### Scenario: Degraded chat dismissal stays truthful

- **WHEN** chat close cannot confirm the latest content revision within the 1-second bound
- **THEN** chat remains open and offers Keep editing, Retry saving, and Close with save unconfirmed
- **AND** the close-with-uncertainty action states "Latest changes may not be recoverable. A browser draft may still appear."
- **AND** choosing it closes without claiming that the latest text was saved or discarded

#### Scenario: Degraded discard stays truthful on forms and chat

- **WHEN** an eligible form or chat surface cannot confirm its requested tombstone within the 1-second bound
- **THEN** the surface remains open and offers Keep editing, Retry discard, and Close with deletion unconfirmed
- **AND** the close-with-uncertainty action states "A browser draft may remain."
- **AND** no restored affordance or storage status reports Discarded before a tombstone is read back

#### Scenario: Successful submit with unconfirmed draft deletion remains distinct

- **WHEN** the existing domain or chat contract proves submission success but the submitted content's tombstone fails or does not settle within 1 second
- **THEN** the surface reports the real submission success and separately reports "Browser draft deletion unconfirmed"
- **AND** duplicate submission is disabled while Retry discard and Close with deletion unconfirmed remain available
- **AND** no UI claims the browser draft was cleared, and closing states that a saved copy may remain

#### Scenario: Unavailable draft persistence does not disable form submission

- **WHEN** the owner submits a valid eligible form whose latest content revision cannot be committed and read back within 1 second
- **THEN** the existing domain mutation proceeds once with the in-memory form values
- **AND** the UI does not claim that reload recovery or later draft deletion is available
- **AND** domain failure retains the current in-memory values, while domain success follows the unconfirmed-deletion scenario

#### Scenario: Late callback cannot mutate a successor context

- **WHEN** a storage operation or submit result settles after its surface closed, unmounted, changed target, or was replaced by another instance
- **THEN** its UI callback is ignored unless both the captured surface-instance token and exact draft key still match
- **AND** it cannot set values, announce success, close, or clear the successor surface
- **AND** durable storage effects remain subject to the authoritative fence and content-revision rules

#### Scenario: Storage read is unavailable

- **WHEN** an eligible surface cannot read browser storage
- **THEN** it opens with its normal current source or initial values and remains editable
- **AND** the unavailable-recovery disclosure is shown without claiming that no draft exists

#### Scenario: Later persistence recovery is honest

- **WHEN** a later edit is successfully written and read back after an unavailable state
- **THEN** the unavailable-recovery disclosure clears only when that read-back contains the latest in-memory content revision
- **AND** no earlier failed write is claimed as recovered

#### Scenario: Diagnostics remain content-blind

- **WHEN** draft persistence handles corruption, expiry, quota, conflict, or another error
- **THEN** logs, telemetry, error reports, and user-facing error detail contain only a fixed failure category and surface identifier
- **AND** no field value, serialized record, content fragment, or content-derived fingerprint is emitted
