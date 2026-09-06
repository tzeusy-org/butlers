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

Eligible drafts SHALL use versioned records in `localStorage` under the dashboard origin, keyed only by a stable surface identifier, operation mode, and non-content domain identifiers. A record SHALL expire 24 hours after its last accepted edit, SHALL be removed when all eligible values are empty or reset to their initial values, and SHALL NOT exceed 64 KiB serialized.

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

#### Scenario: Empty or reset form removes its record

- **WHEN** every eligible value is empty or equals the form's current initial value
- **THEN** the stored record for that exact draft identity is removed
- **AND** later reopening does not show a restored-draft affordance

#### Scenario: Sliding expiry removes stale drafts

- **WHEN** 24 hours elapse after the last accepted edit to a stored draft
- **THEN** the draft is treated as expired and is removed before any content is rendered
- **AND** expiry of one record does not remove a newer or unrelated draft

#### Scenario: Unsupported or corrupt record fails closed

- **WHEN** a stored record has an unsupported schema version, invalid shape, invalid timestamps, or malformed serialized data
- **THEN** no value from that record is rendered into a form
- **AND** the invalid record is removed when possible
- **AND** the owner is told once that the saved draft could not be restored

#### Scenario: Oversize draft stays only in the current mount

- **WHEN** an eligible record would exceed 64 KiB serialized
- **THEN** the current form remains editable and keeps the complete in-memory values
- **AND** that record is not written partially or by silently truncating content
- **AND** the unavailable-recovery disclosure is shown

### Requirement: Draft Restore, Discard, Dismiss, and Submit Experience

The dashboard SHALL make recovered state and persistence loss visible without interrupting typing. Create-form drafts whose identity matches SHALL restore automatically; edit-form drafts SHALL restore automatically only when their recorded source baseline still matches the current record. Every restored draft SHALL expose an accessible Discard action, and every dirty eligible form/dialog dismissal SHALL require an explicit keep-or-discard choice.

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
- **AND** Keep draft and close leaves the stored record intact
- **AND** Discard and close removes only that draft before closing

#### Scenario: Successful submission clears only the submitted draft

- **WHEN** the existing mutation contract proves successful submission of draft K
- **THEN** the stored record and restored-state affordance for K are cleared
- **AND** no other draft key is changed

#### Scenario: Failed submission retains the draft

- **WHEN** validation, transport, authorization, conflict, or application failure prevents successful submission
- **THEN** the form remains populated and draft K remains stored
- **AND** the existing failure or conflict UI remains authoritative
- **AND** retrying does not create a second draft identity

#### Scenario: Repeated restore and discard are safe

- **WHEN** restore or discard handling is triggered more than once for the same record revision
- **THEN** values are applied at most once and deletion remains a no-op after the record is absent
- **AND** duplicate status announcements or duplicate submissions are not produced

### Requirement: Deterministic Cross-Tab Draft Conflict Handling

Draft records SHALL carry a last-edit timestamp and per-tab writer identifier, and storage ordering SHALL use the deterministic tuple `(updatedAt, writerId)`. A newer cross-tab record MAY replace an untouched local view, but SHALL NOT silently overwrite text edited in the current tab or merge field values.

ID: REQ-dashboard-shell-004
Source: design.md Decision 6
Scope: v1-mandatory

#### Scenario: Newer record refreshes an untouched local form

- **WHEN** another tab writes a deterministically newer revision for the same key and the current tab has not changed since its last restore
- **THEN** the current tab applies the newer complete record
- **AND** the updated restore status is announced once

#### Scenario: Newer record conflicts with current-tab edits

- **WHEN** another tab writes a deterministically newer revision for the same key after the current tab has made local edits
- **THEN** the current tab keeps its visible values and shows that the draft changed in another tab
- **AND** explicit Use this tab and Load other draft actions are offered
- **AND** choosing Use this tab writes a new complete revision while choosing Load other draft replaces the visible values with the stored revision

#### Scenario: Equal timestamps resolve consistently

- **WHEN** two revisions for the same key have equal last-edit timestamps
- **THEN** every tab selects the same winner by writer-identifier order
- **AND** no field-level merge occurs

#### Scenario: Unrelated keys do not conflict

- **WHEN** another tab writes or discards a different draft key
- **THEN** the current form and its conflict state remain unchanged

### Requirement: Draft Privacy, Authorization, and Forbidden Surfaces

Browser draft persistence SHALL be an explicit client-side recovery aid, not an authentication or authorization boundary. The persistence mechanism SHALL read or render content only inside a currently accessible eligible surface and SHALL NOT transmit draft records through an API, URL, log, telemetry event, event bus, error report, or provider call except when the owner invokes the surface's existing submission action.

Dedicated Secrets and provider-configuration surfaces, password controls, and fields whose declared purpose is a credential, API key, token, private key, recovery code, auth session, or copy-once revealed value SHALL never be eligible. The system SHALL make no claim that it detects secret material pasted into an otherwise eligible ordinary prose or JSON field.

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

Storage denial, quota exhaustion, serialization failure, or browser API unavailability SHALL never crash an eligible surface, block input, clear in-memory values, or fabricate successful persistence. The dashboard SHALL disclose loss of cross-unmount recovery once per affected surface state and SHALL avoid emitting draft content in diagnostics.

ID: REQ-dashboard-shell-006
Source: dashboard-shell § Utility Infrastructure / Local settings resilience; dashboard-design-language § Composure Doctrine and Interface Copy; craft-and-care/security-and-secrets.md; design.md Decision 8
Scope: v1-mandatory

#### Scenario: Storage write fails while typing

- **WHEN** browser storage throws, rejects, or runs out of quota while an eligible form is being edited
- **THEN** the current mount keeps the complete in-memory values and remains usable
- **AND** a visible `role="status"` message says "Draft saving unavailable. Keep this tab open."
- **AND** the failure is not reported as a successful save
- **AND** repeated failures do not produce repeated toasts or announcements

#### Scenario: Storage read is unavailable

- **WHEN** an eligible surface cannot read browser storage
- **THEN** it opens with its normal current source or initial values and remains editable
- **AND** the unavailable-recovery disclosure is shown without claiming that no draft exists

#### Scenario: Later persistence recovery is honest

- **WHEN** a later edit is successfully written and read back after an unavailable state
- **THEN** the unavailable-recovery disclosure clears
- **AND** no earlier failed write is claimed as recovered

#### Scenario: Diagnostics remain content-blind

- **WHEN** draft persistence handles corruption, expiry, quota, conflict, or another error
- **THEN** logs, telemetry, error reports, and user-facing error detail contain only a fixed failure category and surface identifier
- **AND** no field value, serialized record, content fragment, or content-derived fingerprint is emitted
