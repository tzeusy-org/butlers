## ADDED Requirements

### Requirement: Personal item identity and locate answers remain source-qualified

The authenticated private owner screen SHALL locate only an explicitly selected existing `general.collection_items.id`. It SHALL display reported location and custody separately with observed/recorded times, evidence availability, active episode, retirement and conflict state. Missing evidence SHALL remain unknown; old reports SHALL remain dated last reports, never live physical certainty. Source doors and full history SHALL resolve only inside the authenticated owner surface. No model-facing custody locate tool SHALL be registered.

ID: REQ-personal-item-custody-001
Source: owner direction 2026-09-27 option A; design.md private boundary; heart-and-soul/security.md
Scope: v1-mandatory

#### Scenario: Private selection distinguishes identical objects
- **WHEN** two items have the same name or model
- **THEN** the private screen SHALL require explicit item selection before enrollment or a transition
- **AND** no model shall receive candidate identities, labels or selection results

#### Scenario: Qualified report and unavailable evidence
- **WHEN** the owner privately opens an enrolled item
- **THEN** each reported facet SHALL retain its observation time and source qualification
- **AND** missing or unreadable evidence SHALL appear as unknown or source unavailable without inventing current location or erasing history

### Requirement: Owner-reported custody transitions preserve episode meaning

General SHALL offer a typed private owner transition operation for an opted-in existing item. Each accepted transition MUST contain the item UUID, `operation_id`, expected profile revision, action, validated server-held owner-report receipt, server-derived source reference, `observed_at`, server `recorded_at`, and a durable event ID. An LLM-supplied source reference or owner label MUST NOT authorize the write. Missing validated provenance or ambiguous item identity MUST refuse the typed write. The receipt MUST state what the owner reported, not that General performed a physical action. The reserved versioned profile and its event history SHALL remain attached to that item UUID; legacy items SHALL NOT be auto-enrolled. `move` changes only the reported location. `borrow` and `lend` SHALL open distinct episodes from the owner's perspective and record custodian separately from place. At most one borrow or lend episode SHALL be active for an item; another opening while one is active MUST fail without a write and identify the active episode. A `move` during an episode MAY update reported place but MUST NOT change its custodian. The active episode determines the reported borrower/lender custody; its exact return report determines the post-return custody. This capability SHALL NOT infer sub-lending or overlapping custody. `return` SHALL name one active `episode_id` and close only that episode on the same item upon an explicit owner report of physical return. `retire` SHALL preserve the item and history while excluding it from active-possession answers. An open borrow/lend episode MUST be resolved before retirement. `correct` SHALL name an event on the same item, append a replacement report, and recompute the projection without erasing the old event or asserting that a physical act was undone. It MUST preserve that event's action, event ID, episode ID, and borrow/lend direction. Before accepting it, General MUST validate every later dependent event under the same lock; a correction that would invalidate a return, episode order, or dependent transition MUST fail with dependent event references and no write.

ID: REQ-personal-item-custody-002
Source: owner direction 2026-09-27 option A; design.md private boundary; heart-and-soul/security.md
Scope: v1-mandatory

#### Scenario: Borrow, lend, and return have different custody effects

- **WHEN** the owner reports borrowing an item from a lender or lending an item to a borrower
- **THEN** General MUST open one episode of the specified direction with a server-issued episode ID
- **AND** owner-borrowed custody MUST be reported with the owner, while owner-lent custody MUST be reported with the borrower
- **AND** neither action MUST invent a physical location

#### Scenario: Return closes only the named active episode

- **WHEN** the owner reports a physical return with the exact active borrowing or lending episode ID
- **THEN** General MUST close that episode once and report custody back to the lender for a borrow or back to the owner for a lend
- **AND** an absent, stale, already closed, or different episode ID MUST be refused without changing any episode

#### Scenario: A second active custody episode is refused

- **WHEN** an item has an active borrow or lend episode and another borrow or lend is requested
- **THEN** General MUST refuse the new opening with the active episode reference and leave revision and history unchanged
- **AND** a reported move during that episode MAY change place but MUST NOT change custodian

#### Scenario: Retirement and correction retain historical truth

- **WHEN** the owner retires an item with no open episode or corrects a specified prior report
- **THEN** General MUST append the report and retain all prior events and their source references
- **AND** retirement MUST remove only the active-possession claim, while correction MUST recompute the projection without claiming a physical reversal

#### Scenario: Correction cannot orphan a dependent return

- **WHEN** an owner correction to an opening event would change its action or direction, or make its later return invalid
- **THEN** General MUST refuse it with the dependent event reference and leave the profile unchanged
- **AND** an accepted factual correction MUST keep episode identity and replay all later events before computing the new projection

### Requirement: Custody history is atomic, revisioned, and replay-safe

The opt-in profile SHALL contain a schema version, monotonic revision, append-only transition events, and a projection derived from accepted events. Every typed transition MUST use one PostgreSQL row lock or equivalent atomic compare-and-swap on the selected `collection_items` row. It MUST compare the expected revision and commit event, episode change, projection, and next revision together. A stale revision MUST return a recoverable conflict naming the current revision and intervening event reference, without a write. Under that same atomic boundary, replay of an `operation_id` with identical canonical input MUST return its original event and receipt, even after a later revision. Reuse with different content MUST fail. `observed_at` and `recorded_at` MUST remain distinct. A late historical observation MUST NOT silently replace a later current projection. Contradictory observations whose ordering or authority cannot be established MUST remain a visible conflict until an explicit owner correction resolves them.

ID: REQ-personal-item-custody-003
Source: owner direction 2026-09-27 option A; design.md private boundary; heart-and-soul/security.md
Scope: v1-mandatory

#### Scenario: Equal-revision transitions have one winner

- **WHEN** two different transitions race against the same item and expected revision in real PostgreSQL
- **THEN** exactly one MUST commit the next revision and the other MUST receive a conflict
- **AND** the loser MUST NOT append an event or alter the winner's projection

#### Scenario: Idempotent retry returns the original receipt

- **WHEN** a committed transition is retried with the same operation ID and canonical input
- **THEN** General MUST return the original event ID, revision, and receipt without another event
- **AND** using that operation ID with changed input MUST fail without mutation

#### Scenario: Late and contradictory observations do not invent certainty

- **WHEN** an owner reports an older observation after a newer one, or two claims cannot be ordered reliably
- **THEN** the older event MUST remain in history without silently replacing the newer projection
- **AND** unresolved contradictory claims MUST appear as a conflict with source and time references, not a guessed current location or custodian

### Requirement: Generic collection operations cannot erase an opted-in history

For an opted-in item and its private parent collection, every model-facing or generic API mutation SHALL refuse uniformly, including unrelated data/tag updates, moves, rename, item deletion and collection cascade deletion. The external refusal SHALL be identical to the same operation against an unavailable or nonexistent target and SHALL expose no protected classification, revision, item/collection identifier or dependent event. Only trusted deterministic private owner handlers may change unrelated fields; they SHALL merge against the latest locked row or fail a revision comparison without overwriting newer custody history. Private item deletion and private collection cascade deletion SHALL remain refused absent a separately adopted explicit owner deletion contract. Private owner export SHALL retain full history; generic exports SHALL omit the entire private collection. No generic opt-out may discard history or reopen exposure.

ID: REQ-personal-item-custody-004
Source: owner direction 2026-09-27 option A; design.md private boundary; heart-and-soul/security.md
Scope: v1-mandatory

#### Scenario: Generic mutation cannot reveal protected existence
- **WHEN** a generic caller updates tags or unrelated data, moves or deletes a protected item or parent collection, or supplies an unavailable target
- **THEN** it SHALL receive the same fixed refusal with no mutation or record-specific detail
- **AND** no success difference, revision conflict or special protected-key message SHALL disclose existence

#### Scenario: Trusted private update racing a transition preserves history
- **WHEN** a deterministic private owner update races a custody transition
- **THEN** it SHALL merge only after reading the latest row under lock or fail stale in the private response
- **AND** it SHALL never restore an earlier history or projection

#### Scenario: Delete and enrollment races preserve records
- **WHEN** deletion or cascade competes with enrollment
- **THEN** serialization SHALL either finish the ordinary operation before enrollment or refuse deletion after protection activates
- **AND** no enrolled item or history SHALL be silently removed

### Requirement: Specialist evidence remains bounded and non-authoritative for custody

General SHALL retain custody as owner-reported private information. This first private slice SHALL NOT call Finance, Relationship, Home, Switchboard or any provider to enrich, resolve or interpret a report. Person/place labels SHALL remain unresolved owner labels; no canonical identity or purchase truth SHALL be inferred. Optional specialist integration requires a separate exact contract and is not mandatory implementation scope here. No capture, locate, confirmation or export SHALL send notifications, perform physical work or initiate provider requests.

ID: REQ-personal-item-custody-005
Source: owner direction 2026-09-27 option A; design.md private boundary; heart-and-soul/security.md
Scope: v1-mandatory

#### Scenario: A private report does not trigger enrichment
- **WHEN** a report contains a person name, place or receipt reference
- **THEN** the private screen SHALL retain the supplied label as unverified owner text
- **AND** it SHALL make no external or cross-butler lookup and no ownership inference

### Requirement: Typed custody writes require a server-held owner confirmation receipt

Only an authenticated owner request handled server-side through the private dashboard capture and confirmation routes SHALL authorize a transition. Model tools, ordinary connector messages, caller actor labels and submitted receipt-shaped objects SHALL NOT mint authority. The server SHALL bind an immutable confirmation receipt to the server-derived owner, selected item UUID, operation ID, action, episode, expected revision, exact normalized report fields, observed time, source of private capture, issue time and expiry. The owner SHALL see those bound fields before confirmation. New writes SHALL validate that binding and expiry, then atomically commit the event and receipt. The receipt and all record-specific handles SHALL stay outside model context. No Switchboard validation service is required: custody and its private owner-capture evidence are General-local and use the dashboard's server-derived principal.

ID: REQ-personal-item-custody-006
Source: owner direction 2026-09-27 option A; design.md private boundary; heart-and-soul/security.md
Scope: v1-mandatory

#### Scenario: Private capture then exact confirmation
- **WHEN** the owner enters a report privately and confirms the exact displayed item, fields, episode and expected revision
- **THEN** the server SHALL apply only that bound operation after revision and receipt validation
- **AND** the UI SHALL show a deterministic receipt without an LLM invocation

#### Scenario: Forged expired changed or missing confirmation fails closed
- **WHEN** a model, non-owner, forged receipt, expired confirmation or changed payload requests a transition
- **THEN** no history, episode or revision SHALL change
- **AND** a private owner can reopen a fresh confirmation; no provider fallback SHALL occur

#### Scenario: Repeat and interruption are safe
- **WHEN** the confirmed request is retried after completion or its response was interrupted
- **THEN** the same operation SHALL return its original private receipt without a second write
- **AND** an unconfirmed interrupted draft SHALL never become an event automatically

### Requirement: Custody values and record identity remain outside model context

For enrolled items, every model-facing generic item/list/search/export, prompt retrieval, catalog publication and session result SHALL omit the entire protected row and its identifiers, labels, tags, profile, history, source references, timestamps and derived counts. A denied exact request SHALL return the same fixed refusal whether the protected row exists or not. Search, pagination and totals SHALL operate on the permitted set before slicing; no protected-row placeholder or existence hint SHALL be returned. Enrollment SHALL atomically classify the entire parent collection as private, including its name, description, tags, aggregates and all member rows. Generic collection list/search/read/export and mutations SHALL omit or uniformly refuse that collection and every member, including ordinary siblings; no semantic secret detector SHALL decide whether metadata is safe. Existing nested or derived collection summaries SHALL exclude that collection deterministically. Protection SHALL not automatically disappear when an item moves, is retired or becomes unavailable. The collection classification SHALL be persisted as a reserved `collections.custody_private` boolean, default false for existing ordinary rows, set true only by private enrollment and never unset by generic operations or automatic lifecycle events. Generic collection-name resolution, creation and item auto-create/upsert SHALL operate exclusively in the ordinary namespace. A name used only by a private collection SHALL behave like an absent ordinary name: an ordinary create may create a distinct ordinary collection UUID and MUST NOT fail because of, reuse or disclose the private row. Ordinary collection names SHALL remain unique among ordinary rows; private rows SHALL not participate in that constraint. Private names may coincide and the owner screen SHALL distinguish their UUIDs. These guarantees concern feature-controlled responses, not erasing historical knowledge or indistinguishable timing side channels. The private custody route and descendants SHALL have page-context policy `none`. Private components SHALL never register page-context enrichment, visible resources, summaries, entity references, selected IDs, form drafts or snapshots. Floating chat and full conversation sends SHALL omit `page_context` entirely on the private surface, including a first send, subsequent turns, retries and a snapshot captured before navigation. Pending snapshots SHALL be invalidated at entry and exit; queued sends SHALL not attach stale private state. Server conversation admission SHALL drop any page-context payload naming a private custody route before storage, audit or model invocation. No context toggle may override this policy. Only the fixed generic route `/general/possessions` and a fixed capability availability category MAY be returned for custody; these SHALL be independent of item existence, count, content or operation outcome and SHALL carry no record-specific query, fragment, token or bearer handle. No custody lookup tool accepting item identity SHALL be exposed to models. The private owner API SHALL remain authenticated and SHALL never proxy through model tools. Only its dedicated private export may include full custody history. Generic MCP/API exports SHALL exclude protected custody content; generic API item reads SHALL use the same safe projection unless they are the named private owner routes. Generic writes SHALL not opt in, alter or opt out of custody protection. Existing generic-state risk acceptance is unchanged: these restrictions govern new custody-owned item data and do not adopt PR4219 or reclassify unrelated state keys.

ID: REQ-personal-item-custody-007
Source: owner direction 2026-09-27 option A; design.md private boundary; heart-and-soul/security.md
Scope: v1-mandatory

#### Scenario: Generic read search and export cannot recover a protected item
- **WHEN** a model or generic endpoint requests an enrolled item directly or through search, list, export, catalog or prompt retrieval
- **THEN** no protected value, identifier or existence-dependent result SHALL be disclosed
- **AND** permitted-row totals and pages SHALL be computed without protected contributions

#### Scenario: Conversation provides only a generic entry
- **WHEN** a conversation asks where an item was last reported
- **THEN** the custody capability SHALL return only its fixed private-screen entry and content-blind availability category
- **AND** no record-specific identifier, label, status or bearer handle SHALL enter the model result

#### Scenario: Browser conversation bridge cannot snapshot custody
- **WHEN** floating chat, conversation send, retry or navigation occurs while private custody data is displayed or after a private draft was open
- **THEN** outgoing requests SHALL omit page_context and every private enrichment or stale snapshot
- **AND** forged private-route context SHALL be dropped server-side before persistence or provider use

#### Scenario: Parent collection is deterministically hidden
- **WHEN** an item is enrolled in a collection with a name, description and ordinary siblings
- **THEN** generic tools and APIs SHALL exclude the parent metadata and all its rows from lists, counts, search and export
- **AND** private owner access SHALL retain those records without rewriting labels or deleting siblings

#### Scenario: Hidden collection names do not become creation oracles
- **WHEN** generic collection_create or item_create uses a name present only on a private collection
- **THEN** it SHALL follow the same ordinary creation behavior as an unused name and return only a new ordinary identity
- **AND** it SHALL never select the private row or surface a private-name uniqueness conflict

#### Scenario: Concurrent ordinary creation and enrollment remain isolated
- **WHEN** generic item creation by name races enrollment of the matching ordinary parent
- **THEN** the operation SHALL either complete before enrollment or re-resolve into the ordinary namespace after enrollment
- **AND** it SHALL not append a new item to a now-private collection or disclose its marker

#### Scenario: Errors telemetry and retries stay private
- **WHEN** private input is invalid or a custody operation fails or retries
- **THEN** provider payloads, logs, traces, audit and session artifacts SHALL contain no custody content or record identifiers
- **AND** private validation details SHALL appear only in the authenticated owner response, never raw framework exception payloads or URLs

### Requirement: Private enrollment and owner interaction are explicit and recoverable

Enrollment SHALL be an explicit authenticated private owner action on an existing item, atomically activating the parent-collection and member read/write fences before any new private field is stored. Before confirmation, the private UI SHALL state that the entire parent collection, including ordinary siblings, will become private and cease appearing in generic tools; canceling SHALL change nothing. This collection-level fence SHALL be part of the enrollment confirmation digest. Existing items SHALL not be auto-enrolled or rewritten. Enrollment SHALL not promise to retract data previously exposed through ordinary tools or conversation. The private screen SHALL provide keyboard-operable item selection, visible focus, accessible validation, loading acknowledgment, source/conflict states and deterministic confirmation. No model SHALL prefill, summarize or receive its fields. Unsubmitted drafts SHALL remain browser-memory only and SHALL be discarded on logout or navigation; receipt-backed pending confirmations SHALL expire after ten minutes without creating events. A minimal migration SHALL add the persistent collection classification and replace global collection-name uniqueness with ordinary-only uniqueness without changing existing IDs, names or contents. A downgrade SHALL refuse while private collections or globally duplicated names exist; no downgrade SHALL reset protection, rename, merge or delete records to fit the old schema. Disabling the feature SHALL retain protection and the private history/export path; if that path is unavailable, generic access SHALL remain denied rather than reopening exposure.

ID: REQ-personal-item-custody-008
Source: owner direction 2026-09-27 option A; design.md private boundary; heart-and-soul/security.md
Scope: v1-mandatory

#### Scenario: Enrollment is atomic with generic-reader exclusion
- **WHEN** enrollment races a generic item read or write
- **THEN** the operation SHALL serialize so a generic reader either sees the pre-enrollment item without new private data or receives the safe refusal
- **AND** no private write SHALL commit before all exposure fences are active

#### Scenario: Keyboard and failure recovery stay on the private surface
- **WHEN** the owner selects, captures, confirms or encounters an unavailable source using only a keyboard
- **THEN** focus, validation and outcome SHALL remain perceivable and the UI SHALL offer a safe retry without hidden provider processing
- **AND** waits SHALL acknowledge progress without marking an uncommitted report as saved

#### Scenario: Rollback retains protection and history
- **WHEN** new capture is disabled or the UI is rolled back
- **THEN** existing custody data SHALL remain protected from generic tools and preserved for private owner export
- **AND** no rollback SHALL assert that a physical act or previous disclosure was undone

