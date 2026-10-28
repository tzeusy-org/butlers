## MODIFIED Requirements

### Requirement: Global Chat Postures

The global "Talk to Butlers" surface (Switchboard-routed conversations, distinct from the per-butler panel above) SHALL present exactly one of three postures at a time, chosen by viewport width and an explicit collapse/expand toggle, never stacked:
- **Docked rail** — `ChatDock`, rendered in the shell's `chatDock` slot at or above the `xl` breakpoint while open; the default posture there. The shell owns only the slot (see `dashboard-shell`, Requirement: Chat Dock Rail (>= xl breakpoint)). The dock is resizable within bounds, and both its width and its open/collapsed state are persisted per viewer.
- **Full page** — `/chat` and `/chat/:conversationId`, reached via the dock/popover's "Open in full page" action, a copy-link, or cmdk recall.
- **Popover** — the floating bottom-right widget (`FloatingChatWidget`), the only posture below `xl` or while the dock is collapsed.
- The dock and the popover share the same Switchboard-routed conversation set. All three postures render the same `MessageThread`/`MessageInput` components and drive their send/stream/stop turn state through the one shared `useConversationTurn` hook (`frontend/src/hooks/use-conversation-turn.ts`) — there is exactly one implementation of that turn logic, not a per-posture copy. On a new entry into the global dock or popover, automatic selection SHALL use only the last locally confirmed Switchboard conversation in the current dashboard tab. It SHALL resume that candidate only when its last confirmed owner submission is less than 30 minutes old, its recorded route prefix equals the current eligible route prefix, and its current identity is positively verified as active and owned by Switchboard. A missing or ineligible candidate SHALL start a new conversation rather than search older threads for a match. Every newer confirmed local global submission SHALL replace the one candidate; if that latest confirmed submission has an ineligible route or query, a `none` policy, or no eligible prefix, it SHALL clear the prior candidate instead of retaining an older eligible thread. An attempted submission whose acceptance is unknown SHALL NOT clear or replace a confirmed candidate merely by being attempted, and its unresolved-attempt identity SHALL retain precedence. A route prefix SHALL preserve the registered route's scope: a static route uses its first literal path segment (with `/` separate), while a dynamic route uses the concrete path through its last dynamic segment, including all intervening literal and bound parameter segments. Only exact equality of these normalized scoped prefixes matches; neither string-prefix comparison nor a shared shell category establishes a match. Query strings, fragments, and a single trailing slash do not affect it. Unregistered or malformed paths, routes whose context policy is `none`, and the full-page chat routes are ineligible for automatic selection. Presence of any query key `butler`, `selected`, `request`, `request_id`, `event`, `trace`, `case`, `bead`, `notification`, `rule`, `source`, `calendar`, or `review_source` SHALL also make an entry or submission ineligible for automatic resume, including an empty selector value, because current page contracts select owner, request, or target scope through these keys; `request_id` is additionally excluded as a conservative alias of the actual SessionsPage URL key `request`. No query value is retained or compared. Other query filters and presentation state deliberately do not establish a different prefix; matching a prefix is not a claim that the visible rows or current context are identical. The recency observation SHALL record only the confirmed conversation identity, eligible prefix, and local acceptance time, in memory for the current tab's dashboard lifetime. It SHALL survive global-posture remounts within that lifetime, but SHALL NOT be persisted, synchronized, inferred from server timestamps or message content, or recreated after reload. A successful existing create/send response proves acceptance of the exact local immutable user-message attempt; a new-conversation observation becomes usable only once that same attempt's conversation identity is known. Its age starts at acceptance, not at a later identity or assistant event. No automatic choice SHALL create or send a message. Explicit conversation selection, a current retained identity with unsent text or an unresolved attempt, and an explicit New conversation choice SHALL take precedence over automatic selection. Once an entry has selected an identity, a route change, query result, timer, or late event SHALL NOT silently replace it. All unsent composer text, queued text, and unresolved submissions SHALL retain their exact `{butler, conversation | new}` identity across either choice under the adopted browser-draft and durable-recovery contracts. This selection policy SHALL NOT weaken remount/reload draft recovery, persist a queue's auto-send intent, replay an attempt, or change the per-instance turn-ownership boundary.

ID: REQ-dashboard-chat-ui-018
Source: heart-and-soul/vision.md § What Success Looks Like; heart-and-soul/design-language.md § What the Dashboard Is Not; design.md Decisions 1-6
Scope: v1-mandatory

#### Scenario: Postures are mutually exclusive

- **WHEN** any dashboard route is rendered
- **THEN** at most one of {docked rail, full page, popover} is mounted at a time
- **AND** the docked rail and the popover in particular are never both mounted (the dock's presence is what decides whether the popover renders at all)

#### Scenario: Each mounted instance owns its own turn state

- **WHEN** a conversation is actively streaming in one posture (e.g. the dock) and the operator navigates to `/chat/{id}` for that same conversation in another tab or after the dock collapses
- **THEN** the full-page instance calls `useConversationTurn` independently and does not receive a live mirror of the other posture's in-flight stream — it resumes from the persisted message history once the turn completes
- **AND** this is a deliberate scope boundary (cross-posture live-turn mirroring is out of scope for this capability)

#### Scenario: Popover trigger reopens a collapsed dock

- **WHEN** the dock has been collapsed while the viewport is at or above the `xl` breakpoint and the operator activates the popover trigger
- **THEN** the dock reopens instead of the popover opening

#### Scenario: Recent same-prefix local use resumes

- **WHEN** the owner opens an otherwise unselected global dock or popover, the last locally confirmed Switchboard submission was accepted 12 minutes ago on a registered `/health/...` route, the current route is a registered `/health/...` route, and the exact candidate is verified active and owned by Switchboard
- **THEN** that conversation is selected and its own history and draft identity are loaded
- **AND** the surface visibly states "Resumed recent conversation" with its title or "Untitled conversation" and exposes New conversation
- **AND** no message is created, sent, retried, or queued by opening it

#### Scenario: Thirty-minute boundary starts fresh

- **WHEN** the candidate's acceptance age is exactly 30 minutes or greater at entry
- **THEN** the surface selects the Switchboard `new` identity and visibly states "New conversation"
- **AND** a late assistant reply or newer server `updated_at` does not make the candidate recent
- **AND** an available earlier thread remains explicitly reachable under Recent-Thread Recall (Command Palette)

#### Scenario: Other-prefix entry starts fresh without searching older matches

- **WHEN** the last locally confirmed candidate was used on `/health` and the owner opens global chat on `/spend`, even if an older locally used thread matches `/spend`
- **THEN** the surface selects the Switchboard `new` identity
- **AND** it does not fall back to that older thread or the first server-ordered row
- **AND** it offers explicit recall without sending or moving any draft

#### Scenario: Prefix matching uses registered path segments

- **WHEN** a candidate recorded on `/entities/A?tab=facts#section` is considered from the same registered `/entities/A/` route
- **THEN** both resolve to scoped prefix `/entities/A` and query, fragment, and the trailing slash do not prevent a match
- **AND** `/health` does not match `/healthcare`, `/` does not match every route, and the shell category shared by `/settings` and `/secrets` does not make them a matching pair
- **AND** static path matching retains the router's case sensitivity, and duplicate separators, encoded separators, malformed paths, or unknown routes supply no eligible prefix

#### Scenario: Dynamic ownership and record scopes stay distinct

- **WHEN** the candidate was accepted on `/butlers/health` and entry is on `/butlers/finance`, or a registered session, entity, memory fact/rule/episode, approval, bead, connector endpoint, QA patrol, or QA investigation has a different bound route identity
- **THEN** the scoped prefixes differ and an otherwise unselected global entry starts fresh, even if both routes share their first segment or shell family
- **AND** all dynamic parameters and their intervening literal segments participate, so two connector endpoints of one type and a patrol versus investigation with equal id text do not match
- **AND** query tabs for the same `/butlers/health` target do not change its prefix, while an explicit or protected selected identity and New conversation still take precedence over the heuristic

#### Scenario: Query-owned targets do not collapse into an aggregate family

- **WHEN** a submission or entry carries any query key `butler`, `selected`, `request`, `request_id`, `event`, `trace`, `case`, `bead`, `notification`, `rule`, `source`, `calendar`, or `review_source`, including an empty value
- **THEN** it supplies no automatic-resume eligibility, so an otherwise unselected global entry starts fresh without storing or comparing that selector's value
- **AND** `/sessions?selected=S1` does not automatically resume the conversation for S2, `/ingestion?event=E1` does not share an aggregate `/ingestion` choice, and `/calendar?calendar=C1` does not inherit C2's automatic choice
- **AND** `/sessions?request=R1`, `/sessions?request=R2`, and `/sessions?request=` are all ineligible, so a different or empty request selector cannot inherit an aggregate sessions choice
- **AND** ordinary presentation or range filters remain deliberately ignored by prefix matching, with current context recaptured at a later send; no matching prefix claims identical visible data
- **AND** explicit conversation selection, protected identity, and New conversation precedence remain unchanged

#### Scenario: Sensitive or unknown context cannot seed or satisfy resume

- **WHEN** a global submission or new chat entry occurs on a route whose context policy is `none`, an unregistered route, or a malformed path
- **THEN** it supplies no eligible resume observation or prefix, and entry starts fresh unless a higher-priority explicit or protected identity applies
- **AND** no page-context opt-out, route query, visible-resource payload, title, or message text is stored in the resume observation or sent as additional context
- **AND** the existing current-context capture and include-or-exclude choice remain authoritative for a later owner send

#### Scenario: Missing, reloaded, or invalid recency proof starts fresh

- **WHEN** there is no confirmed local observation, the dashboard reloads or opens in another tab, the clock observation is invalid or moves backwards, or the candidate has no known conversation identity
- **THEN** automatic selection uses the Switchboard `new` identity
- **AND** persisted draft records are neither deleted nor treated as a resume history
- **AND** a previously accepted browser draft still restores when its exact eligible identity is opened under the adopted draft contract

#### Scenario: Passive activity never renews owner recency

- **WHEN** a thread is viewed, explicitly recalled, renamed, refetched, automatically selected, receives an assistant reply, or has unsent or queued text edited without a confirmed owner submission
- **THEN** none of those events creates or renews the resume observation
- **AND** a queued item can renew it only after actual dispatch has established durable acceptance of its own immutable message ID
- **AND** failed or unknown submissions do not invent acceptance or extend the window, and unknown acceptance alone does not clear a confirmed candidate
- **AND** every newer confirmed local global submission replaces the sole candidate; confirmation on an ineligible route or query, under a `none` policy, or without an eligible prefix clears the previous candidate so an older eligible thread cannot reappear as a fallback

#### Scenario: Explicit selection wins regardless of heuristic eligibility

- **WHEN** the owner selects a conversation through history, search, a supported keyboard selector, or command-palette recall
- **THEN** that exact identity is opened under its existing access and read rules regardless of age or route prefix
- **AND** automatic selection does not substitute a more recent thread, Switchboard thread, or new identity
- **AND** an archived conversation explicitly addressable under the existing read contract is not rejected merely because it is ineligible for automatic resume

#### Scenario: Full-page route intent wins

- **WHEN** the owner visits `/chat/:conversationId`, including a copy-link or command-palette target
- **THEN** the URL identity is resolved cross-butler and remains authoritative, including its existing not-found and message-fragment behavior
- **AND** visiting bare `/chat` keeps its not-yet-created Switchboard composer instead of applying dock or popover automatic selection
- **AND** this policy changes neither full-page routing nor the butler-detail panel's separate conversation selection

#### Scenario: An open conversation remains selected during navigation or expiry

- **WHEN** a mounted chat entry already has a selected identity and the owner changes route, the recency window expires, or list data changes
- **THEN** its active identity, current composer, and current turn remain unchanged
- **AND** page context for any later owner send is captured under the existing send-time context contract
- **AND** only a later explicit selection or a later unprotected new entry can choose another identity

#### Scenario: New conversation and typing defeat delayed automatic results

- **WHEN** the owner activates New conversation, begins editing the fresh composer, explicitly selects a thread, or changes to another entry while automatic eligibility is pending
- **THEN** that intent settles the entry and invalidates every older automatic-selection result
- **AND** a later list, identity, or history response cannot snap back, clear text, focus another thread, or issue a send
- **AND** repeated render effects and repeated query notifications cannot apply a second automatic choice

#### Scenario: Unsent text stays with its exact identity across the choice

- **WHEN** current or restored unsent text belongs to the active `{butler, conversation | new}` identity while an automatic candidate is considered
- **THEN** that protected identity remains selected rather than silently changing its destination
- **AND** an explicit switch preserves the departing identity's text and displays only the destination's own draft; returning within the adopted lifetime restores the departing text
- **AND** a fresh draft is never copied into a resumed thread, and a thread draft is never copied into the fresh composer
- **AND** remounts, reloads, conflicts, expiry, Discard, late acceptance, and storage failure retain the adopted draft contract's exact content-revision and bounded truthful-recovery rules

#### Scenario: Queued text follows the adopted queue choice without replay

- **WHEN** queued-turn support is present under its governing amendment, a queued item exists, and an explicit choice leaves its conversation or its posture unmounts
- **THEN** the item is demoted into the ordinary unsent text of its own identity, ahead of existing composer text with a blank line between, and its unsent queue message ID and captured context are discarded under the queue contract
- **AND** changing choice or returning to that identity does not dispatch it; only the queue contract's committed prior `message_complete` can release an item that still remains queued in its original mounted turn
- **AND** the owner's Option A keeps queued text in that identity's adopted browser draft and restores it after reload marked "Not sent", without persisting auto-send intent, queue message ID, or captured context
- **AND** this selection amendment supplies no competing storage or queue implementation and claims no reload persistence before the adopted draft seam is delivered

#### Scenario: Unresolved attempts retain their ownership

- **WHEN** the departing identity has an in-flight, interrupted, pending, rejected, or unknown submitted attempt and the owner makes an explicit selection or starts fresh
- **THEN** its immutable message ID, submitted content revision, and durable outcome remain bound to that original identity under the existing recovery contract
- **AND** no selection path copies the attempt to the destination, clears newer text, treats client detachment as server cancellation, or retries automatically
- **AND** a same-thread history retry keeps that identity's draft beside the existing retryable read error

#### Scenario: Missing or unavailable eligibility remains honest and usable

- **WHEN** the exact candidate is missing, archived, owned by another butler, or cannot be positively verified from the existing reads
- **THEN** it is not automatically selected, and a missing row in a bounded list is not treated as proof of deletion
- **AND** the fresh composer remains available with its own text, while unavailable list or identity sources are named with their existing retry affordance
- **AND** a list failure is not described as "No conversations", a history failure is not a blank successful thread, and a later retry does not override an already settled entry

### Requirement: Recent-Thread Recall (Command Palette)

Recently-updated Switchboard conversations SHALL be reachable through the command palette (cmdk), alongside the existing "Talk to Butlers" command, navigating directly to `/chat/{id}`. When a global dock or popover starts fresh while an earlier active Switchboard conversation is available, it SHALL expose a visible, keyboard-operable "Earlier conversation" affordance naming the target by title or "Untitled conversation". This affordance SHALL be an explicit selection, not a delayed automatic switch, and SHALL navigate to that exact `/chat/{id}` using the existing route. The target SHALL be the verified local candidate when available, otherwise the most recently updated active thread in the already loaded recall list; that display fallback SHALL NOT affect automatic eligibility. No recency hint or draft content SHALL be required to register the existing recall commands. Any departure with protected text SHALL use the adopted draft contract's preservation and bounded truthful-dismissal behavior before navigation, without a global draft inventory.

ID: REQ-dashboard-chat-ui-019
Source: dashboard-design-language § Interaction Affordances, Interface Copy, and Viewport and Modality Contract; design.md Decision 7
Scope: v1-mandatory

#### Scenario: Recent threads appear as palette commands

- **WHEN** the command palette is opened
- **THEN** recent Switchboard conversations each appear as a command labeled with the conversation's title (or "Untitled conversation")
- **AND** selecting one navigates to `/chat/{conversationId}`
- **AND** this recall list is registered once, mounted regardless of which chat posture (dock or popover) is currently showing

#### Scenario: Earlier conversation is a disclosed explicit choice

- **WHEN** a global dock or popover starts fresh because an available earlier active Switchboard conversation is stale, belongs to another prefix, or has no local resume proof
- **THEN** "New conversation" and an "Earlier conversation" control with that conversation's title or "Untitled conversation" are visible without hover
- **AND** activating the control navigates to exactly `/chat/{conversationId}`, preserving the departing draft under its original identity and restoring only the destination's own draft
- **AND** it never sends text or silently redirects when the control first appears

#### Scenario: Resume choice is accessible without disrupting composition

- **WHEN** an automatic resume or fresh-entry decision is displayed
- **THEN** its state and target are available as text to assistive technology, with one polite status announcement for the settled decision and no repeated announcement on refetch
- **AND** composer focus is retained after the existing open choreography; selection disclosure does not steal focus
- **AND** Earlier conversation is Tab-reachable, operable with the native link or button keyboard interaction, has a target-specific accessible name, preserves the global focus outline, and meets the 44 by 44 pixel coarse-pointer floor
- **AND** Cmd/Ctrl+K remains the discoverable keyboard path to all existing recall commands without adding a shortcut that collides with typing

#### Scenario: Recall failure preserves recovery and unsent words

- **WHEN** the recall source is unavailable or leaving for Earlier conversation cannot confirm preservation of the latest draft within the adopted 1-second bound
- **THEN** the existing source retry or adopted Keep editing, Retry saving, and Close with save unconfirmed choice is shown, as applicable
- **AND** no control claims that there are no earlier conversations or that the draft is saved without evidence
- **AND** navigation waits for confirmed preservation or the owner's explicit close-with-uncertainty choice, and late callbacks cannot navigate or clear a successor entry

## Source References

- `about/heart-and-soul/vision.md`, What Success Looks Like; `about/heart-and-soul/development.md`, Spec Before Code, RFC Before Exception; `about/heart-and-soul/design-language.md`, What the Dashboard Is Not.
- `openspec/specs/dashboard-chat-ui/spec.md`, complete Global Chat Postures and Recent-Thread Recall predecessors, Full-Page Chat Route, Conversation Search UI, and Conversation Quick-Switch.
- `openspec/specs/dashboard-design-language/spec.md`, Composure Doctrine, Interaction Affordances, Interface Copy, Voice Surface, and Viewport and Modality Contract.
- Owner-adopted `openspec/changes/persist-safe-dashboard-unsent-drafts/`, exact source d2dc32ffb001398af169363064b7610595512f9d; its full draft lifecycle and exact-message recovery prerequisites remain binding.
- Public queue proposal PR4396 at e681a7a8fa089ebd5ca9cbb5b64c9779f0294d30, Single-Slot Queued Turn, Queued Turn Dispatch and Demotion, and the owner's 2026-10-09 Option A; full sibling adoption remains pending.
- This change's `design.md`, Decisions 1-7; `docs/redesigns/2026-09-02-dashboard-chat-pursuit.md`, move 14; native `bu-0ynlk.18`, complete description, design, and four acceptance criteria.
