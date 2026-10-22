## MODIFIED Requirements

### Requirement: Conversation List Sidebar

Within the chat panel, a conversation list SHALL allow switching between threads or starting new ones. Existing conversations SHALL expose rename and reversible archive or unarchive actions through the list and the shared conversation header, using the existing owner-authenticated metadata PATCH contract. Actions SHALL preserve the selected conversation, its unsent draft identity, message history, and independent turn controls, while rendering optimistic metadata with truthful failure recovery and keyboard or command-palette access.

ID: REQ-dashboard-chat-ui-017
Source: about/heart-and-soul/vision.md § What Success Looks Like; dashboard-conversations § Conversation Lifecycle Management and Continue Conversation; dashboard-design-language § Interaction Affordances and Viewport and Modality Contract; this change design.md Decisions 1-8
Scope: v1-mandatory

#### Scenario: Conversation list renders

- **WHEN** the chat panel opens
- **THEN** a collapsible column beside the thread shows the conversation list for the current butler
- **AND** conversations are sorted by `updated_at DESC` (most recent first)
- **AND** each conversation shows the title (truncated to 2 lines) and a relative timestamp (e.g., "2h ago")
- **AND** the active conversation is visibly highlighted
- **AND** a "New conversation" action appears at the top of the list

#### Scenario: Conversation list empty state

- **WHEN** the butler has no conversations
- **THEN** an `EmptyState` component renders with message "No conversations yet" and a "Start a conversation" action button

#### Scenario: Conversation list collapsed mode

- **WHEN** the user clicks the collapse toggle on the conversation list
- **THEN** the list collapses to a narrow column showing only the first letter of each conversation title
- **AND** the chat area expands to fill the available width
- **AND** the collapse state is persisted per viewer

#### Scenario: Conversation actions identify their target

- **WHEN** an existing conversation appears in the expanded list or is selected in any chat posture
- **THEN** its actions expose Rename conversation and Archive conversation, or Unarchive conversation when archived
- **AND** each action identifies the exact conversation and its owning butler without changing the selected thread merely to open the action
- **AND** a new conversation without a persisted identity exposes no metadata mutation

#### Scenario: Rename commits a valid title

- **WHEN** the owner activates Rename conversation, edits the prefilled title, and presses Enter outside input-method composition
- **THEN** a title whose trimmed value contains 1 through 500 Unicode code points is applied optimistically and submitted once as a title-only PATCH for that conversation and owning butler
- **AND** the confirmed server title and update time reconcile every displayed instance of that conversation
- **AND** the editor closes and focus returns to the invoking control or its stable conversation-header fallback

#### Scenario: Rename cancellation preserves the title

- **WHEN** the owner presses Escape or activates Cancel while editing a title
- **THEN** the editor closes without issuing a PATCH or changing the stored or displayed title
- **AND** focus returns to the invoking control or its stable conversation-header fallback
- **AND** Escape is consumed by the editor before a containing chat surface closes

#### Scenario: Invalid title stays editable

- **WHEN** the owner attempts to commit an empty, whitespace-only, or longer-than-500-code-point trimmed title
- **THEN** no PATCH is issued and the existing title remains unchanged
- **AND** the editor retains the entered text and focus, names the title-length error inline, and associates the error with the input for assistive technology

#### Scenario: Archive preserves the selected conversation and draft

- **WHEN** the owner archives conversation C
- **THEN** C is optimistically marked archived and removed from an Active chronological listing without a confirmation dialog
- **AND** if C is selected, its identity, messages, unsent text, and same-identity draft record remain unchanged and the header identifies its archived state
- **AND** the archive action issues only a status-archived PATCH and neither sends a message nor stops, detaches, resets, or declares completion of an existing turn
- **AND** successful confirmation offers an accessible Undo for C that remains reachable after its row leaves the Active listing

#### Scenario: Undo archive performs the supported inverse

- **WHEN** the owner invokes Undo for a confirmed archive of C
- **THEN** a separate status-active PATCH for C restores it to the Active chronological listing
- **AND** the action does not restore a stale title, change another conversation, or change the selected conversation or any draft
- **AND** success is announced only after that inverse is confirmed

#### Scenario: Archived conversations can be unarchived

- **WHEN** the owner activates Unarchive conversation for an archived conversation
- **THEN** a status-active PATCH is issued and membership changes optimistically from Archived to Active
- **AND** the server response reconciles the conversation's status and chronological order
- **AND** unarchiving does not select a different conversation or change its messages or draft

#### Scenario: Archived filter retrieves the existing archived list

- **WHEN** the owner opens conversation history with no search query or switches its Active or Archived filter
- **THEN** Active is the initial filter and the chosen status is requested through the existing paginated per-butler list API
- **AND** the selected filter is visibly and accessibly identified, including when the sidebar is collapsed
- **AND** an empty Archived result says that no archived conversations exist rather than claiming that all conversation history is empty
- **AND** a failed list request retains any cached rows beside its own retryable read error rather than an empty-state claim

#### Scenario: Search does not imply an unsupported status filter

- **WHEN** a non-empty conversation search is active in either chronological filter
- **THEN** the existing 300ms-debounced conversation search continues to search both active and archived history, with that scope stated visibly
- **AND** the chronological status control is unavailable while searching, and status is identified on each conversation result
- **AND** the separate owner-scoped cross-butler message results, same-butler message jump, and cross-butler deep-link behavior remain unchanged
- **AND** clearing search restores the previously selected chronological filter

#### Scenario: Failed metadata change restores confirmed state

- **WHEN** a rename, archive, or unarchive PATCH fails
- **THEN** the failed action's optimistic title or status and list membership return to their last confirmed values, and a named actionable error is visible and announced
- **AND** the rename text remains available for correction or deliberate retry, while selection, messages, and unsent drafts remain intact
- **AND** affected metadata is refreshed to reconcile a timeout or lost response without presenting rollback as proof that the server rejected the change
- **AND** authentication denial or transport uncertainty never triggers an automatic mutation replay

#### Scenario: Failed undo remains visibly archived

- **WHEN** the inverse PATCH for Undo archive fails
- **THEN** the optimistic inverse rolls back to the last confirmed archived state and its own error identifies that the undo was not confirmed
- **AND** the draft and selected thread remain intact and a deliberate retry or status refresh remains available
- **AND** the UI does not claim that the conversation was restored

#### Scenario: Duplicate or late actions keep their original target

- **WHEN** metadata is pending for C and the owner repeats the same action or navigates to D before it settles
- **THEN** duplicate metadata submission for C is prevented across its row, header, and palette actions until settlement
- **AND** C's completion, rollback, or error updates only C and never changes D's title, status, draft, selection, or focus
- **AND** editing or updating another conversation remains available

#### Scenario: Metadata refresh preserves unrelated conversation evidence

- **WHEN** a metadata PATCH settles while messages or other conversation metadata have also changed
- **THEN** displayed list, header, detail, search, and recent-recall metadata reconcile against the current server values for the affected conversation
- **AND** rollback of that action does not overwrite another conversation or newer message, route, cost, or assistant-reply evidence
- **AND** a title or status update alone does not announce a new assistant reply

#### Scenario: Automatic resume excludes archived conversations

- **WHEN** a chat surface automatically chooses a conversation on opening or remounting
- **THEN** only active conversations are eligible, even if archived rows are present in another cached list or search result
- **AND** when no active conversation is eligible the surface starts with its existing new-conversation state without selecting an archived thread
- **AND** this exclusion adds no recency window, route-prefix rule, server read state, or cross-device resume policy

#### Scenario: Explicit archived selection and continuation remain available

- **WHEN** the owner explicitly selects an archived history or search result, or visits its existing full-page deep link
- **THEN** that exact conversation remains selected and its history and same-identity draft remain accessible without an unarchive merely for viewing
- **AND** the header identifies that the conversation is archived and offers Unarchive conversation
- **AND** sending a follow-up remains governed by the existing Continue archived conversation behavior, with its automatic reactivation disclosed before send and reconciled from server state

#### Scenario: Archive does not alter the adopted draft lifecycle

- **WHEN** a conversation carrying an unsent draft is renamed, archived, unarchived, or restored through Undo
- **THEN** those metadata actions neither clear, migrate, duplicate, submit, expire, or discard the draft nor change its owning butler or conversation identity
- **AND** any eligible browser-local draft retains the adopted expiry, size, restore, discard, cross-tab, acceptance, and storage-failure rules
- **AND** a draft belonging to an archived conversation is recovered only when that conversation is explicitly selected, rather than making it eligible for automatic resume

#### Scenario: Keyboard and command palette perform the same actions

- **WHEN** the owner uses Tab and the conversation action menu's normal keyboard activation, or opens the existing command palette and selects Rename conversation or Archive conversation
- **THEN** the same target-specific rename editor or archive action runs as for pointer activation
- **AND** an archived selected conversation exposes the corresponding Unarchive conversation palette action
- **AND** simultaneously available targets are distinguished by conversation title and owning butler, and opening a palette or action menu does not send or select a different conversation
- **AND** Enter and Escape in the rename editor retain their commit and cancel meanings without firing a page action or message send

#### Scenario: Actions retain Dispatch accessibility and feedback

- **WHEN** conversation actions render or are operated at any supported viewport or pointer modality
- **THEN** their controls have accessible names, visible focus, non-hover-only state, and the existing coarse-pointer hit-area floor
- **AND** opening and closing menus, editor validation, pending metadata, failure, and undo feedback preserve the shared primitive's keyboard and focus ownership
- **AND** reversible metadata responds optimistically without blocking text entry or unrelated controls

## Source References

- `about/heart-and-soul/vision.md`, What Success Looks Like, and `about/heart-and-soul/development.md`, Spec Before Code, RFC Before Exception.
- `openspec/specs/dashboard-conversations/spec.md`, Conversation Lifecycle Management, Conversation List API, and Continue Conversation.
- `openspec/specs/dashboard-chat-ui/spec.md`, complete Conversation List Sidebar baseline, Global Chat Postures, Full-Page Chat Route, Conversation Search UI, Conversation Quick-Switch, and Recent-Thread Recall.
- Adopted `openspec/changes/persist-safe-dashboard-unsent-drafts/`, exact owner-adopted source d2dc32ffb001398af169363064b7610595512f9d; PR4497 proposes only ID004-to-014 and ID005-to-015 bookkeeping.
- `openspec/specs/dashboard-design-language/spec.md`, List Primitive, Button Forms, Interaction Affordances, Interface Copy, Primitive Component Library, and Viewport and Modality Contract.
