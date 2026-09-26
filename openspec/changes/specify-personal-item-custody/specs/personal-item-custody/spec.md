## ADDED Requirements

### Requirement: Personal item identity and locate answers remain source-qualified

General SHALL locate only an existing, explicitly selected
`general.collection_items.id` object. A collection ID, name, model, receipt,
or person ID MUST NOT substitute for that item UUID. Two items with the same
description MUST remain distinct and MUST NOT be merged or selected by a
best-guess match. An ambiguous owner phrase MUST request item selection
before any typed transition.

`possession_locate` SHALL report last-reported location and custody as
separate qualified facets, each with its observation time, server record time,
and owner-statement source reference. A facet lacking a qualified report MUST
be `unknown`; it MUST NOT inherit the other facet's certainty. A long-old
report MUST retain its as-of time rather than become a present-tense physical
claim. A legacy item MAY supply a read-only locate answer without opt-in only
from `data.last_reported_location` or `data.last_reported_custody` paired with
`data.observed_at` and `data.source_ref`; arbitrary freeform text MUST NOT be
converted into a custody event or a verified fact.
If a saved source reference later cannot be read, General MAY show the
historical owner report with `source_unavailable` qualification; it MUST NOT
present the missing source as verified evidence or erase the report.

#### Scenario: Two identical objects require selection

- **WHEN** two collection items have the same name or model and the owner asks where one is
- **THEN** General MUST ask for the specific item UUID or an unambiguous selection
- **AND** it MUST NOT merge the rows, choose the newest, or record a transition

#### Scenario: Qualified last report is an as-of answer

- **WHEN** an identified item's latest usable location report says "cupboard" with a source reference and observation time
- **THEN** the locate answer MUST identify that item and say the location was last reported as "cupboard" as of that time
- **AND** it MUST include the source door and MUST NOT claim the object is physically there now

#### Scenario: Missing evidence or stale report stays honest

- **WHEN** an item has no qualified location report, or a prior report predates a later accepted report
- **THEN** the absent facet MUST be unknown and the prior report MUST remain historical
- **AND** an old last report MUST carry its actual as-of time rather than a fabricated freshness or absence claim

#### Scenario: Unavailable source is distinct from no report

- **WHEN** an item has a qualified owner report but its source reference cannot currently be opened
- **THEN** General MAY retain the historical as-of statement with an unavailable-source marker
- **AND** it MUST NOT claim the source was verified, silently delete the report, or turn the read failure into proof the object is absent

### Requirement: Owner-reported custody transitions preserve episode meaning

General SHALL offer a typed `possession_record` operation for an opted-in
existing item. Each accepted transition MUST contain the item UUID,
`operation_id`, expected profile revision, action, validated server-held
owner-report receipt, server-derived source reference, `observed_at`, server
`recorded_at`, and a durable event ID. An LLM-supplied source reference or
owner label MUST NOT authorize the write. Missing validated provenance or
ambiguous item identity MUST refuse the typed write. The receipt MUST state
what the owner reported, not that General performed a
physical action. The reserved versioned profile and its event history SHALL
remain attached to that item UUID; legacy items SHALL NOT be auto-enrolled.

`move` changes only the reported location. `borrow` and `lend` SHALL open
distinct episodes from the owner's perspective and record custodian separately
from place. At most one borrow or lend episode SHALL be active for an item;
another opening while one is active MUST fail without a write and identify
the active episode. A `move` during an episode MAY update reported place but
MUST NOT change its custodian. The active episode determines the reported
borrower/lender custody; its exact return report determines the post-return
custody. This capability SHALL NOT infer sub-lending or overlapping custody.
`return` SHALL name one active `episode_id` and close only that
episode on the same item upon an explicit owner report of physical return.
`retire` SHALL
preserve the item and history while excluding it from active-possession
answers. An open borrow/lend episode MUST be resolved before retirement.
`correct` SHALL name an event on the same item, append a replacement report,
and recompute the projection without erasing the old event or asserting that
a physical act was undone. It MUST preserve that event's action, event ID,
episode ID, and borrow/lend direction. Before accepting it, General MUST
validate every later dependent event under the same lock; a correction that
would invalidate a return, episode order, or dependent transition MUST fail
with dependent event references and no write.

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

The opt-in profile SHALL contain a schema version, monotonic revision,
append-only transition events, and a projection derived from accepted events.
Every typed transition MUST use one PostgreSQL row lock or equivalent atomic
compare-and-swap on the selected `collection_items` row. It MUST compare the
expected revision and commit event, episode change, projection, and next
revision together. A stale revision MUST return a recoverable conflict naming
the current revision and intervening event reference, without a write.

Under that same atomic boundary, replay of an `operation_id` with identical
canonical input MUST return its original event and receipt, even after a later
revision. Reuse with different content MUST fail. `observed_at` and
`recorded_at` MUST remain distinct. A late historical observation MUST NOT
silently replace a later current projection. Contradictory observations whose
ordering or authority cannot be established MUST remain a visible conflict
until an explicit owner correction resolves them.

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

For an opted-in item, `item_update` MUST refuse edits to the reserved profile
namespace. Every generic whole-document data or tag update, including one
touching only unrelated keys, MUST merge against the row locked after any
concurrent typed transition or fail a revision comparison; it MUST NOT write
an earlier copy of the profile over a later one. `item_delete` and
`collection_delete` MUST atomically refuse deletion of opted-in items,
including collection cascade deletion and a concurrent opt-in race, absent a
separately approved explicit owner deletion contract. `collection_export`
MUST retain the complete profile and event history for such items. No generic
opt-out may silently discard history.

#### Scenario: A generic update racing a transition preserves the history

- **WHEN** `item_update` reads revision 1, a custody transition commits revision 2, and the generic update then changes an unrelated field or tags
- **THEN** the generic write MUST re-read under the row lock and preserve revision 2, or fail as stale
- **AND** it MUST NOT restore the revision-1 event array or projection

#### Scenario: Delete and cascade cannot bypass the profile guard

- **WHEN** `item_delete` targets an opted-in item or `collection_delete` would cascade to one
- **THEN** the delete MUST be refused atomically and the item, profile, and history MUST remain
- **AND** a simultaneous profile opt-in MUST not evade the same guard

### Requirement: Specialist evidence remains bounded and non-authoritative for custody

The General profile SHALL record owner-reported physical custody, not Finance
purchase truth, Relationship identity, or Home device state. A linked Finance
transaction or receipt MAY be resolved only through Switchboard-brokered,
read-only Finance MCP under separately reviewed read authority. General MUST
NOT read `finance.*` directly, copy receipt payloads into its profile, or
infer item ownership, location, return, or retirement from a receipt. A
receipt-link request alone MUST NOT alter custody. If Finance evidence is
unavailable, the owner statement MAY remain recorded with a separately
unavailable evidence door; no verified-purchase claim may appear.

Optional person references SHALL use existing Relationship/public identity
resolution. An unresolved name MUST stay an unresolved label and MUST NOT
become a canonical person through name similarity. Private object locations,
borrower labels, full statement text, and source references MUST NOT be
published into public entity graph, public memory catalog, broad cross-butler
summaries, or a provider payload. Recording and locating SHALL perform no
external action, notification, financial write, or Home physical operation.
The typed write MUST be tied to an authenticated owner report through the
server-held confirmation seam below; a caller-supplied label cannot assert
owner attribution, and model inference alone cannot create a transition.

#### Scenario: Receipt failure cannot change custody

- **WHEN** a receipt-only link is attempted and Finance is unavailable or refuses its read
- **THEN** no custody event or revision change MUST occur and the receipt evidence MUST be marked unavailable
- **AND** a separately reported owner statement MUST retain its own source/time qualification without becoming purchase proof

#### Scenario: Person and place references remain qualified

- **WHEN** an owner names a borrower or place that cannot be resolved to a canonical entity
- **THEN** the label MUST remain unresolved and the locate answer MUST state its owner-reported status
- **AND** General MUST NOT create a Relationship identity, publish a private location, or invoke Home action

### Requirement: Typed custody writes require a server-held owner confirmation receipt

Before `possession_record` is enabled, Switchboard SHALL provide a narrow
server-held owner-report confirmation and read-only validation seam. An LLM
MAY propose a structured report, but only a server-handled confirmation from
an authenticated owner dashboard request or an ingress message independently
resolved to the owner MAY mint an immutable receipt. The server-held receipt
MUST bind the original owner statement and confirmation locators, verified
owner identity, item UUID, action, episode ID if any, operation ID, expected
profile revision, canonical report payload digest including observed time,
issue time, and expiry. A caller-supplied `source_ref`, role, quoted text, or
receipt-shaped object MUST NOT mint or alter it.

For a new write, General MUST validate the opaque receipt ID and exact
argument digest through Switchboard MCP, not through direct Switchboard SQL.
Validation MUST confirm owner identity, source and confirmation readability,
unexpired receipt, and exact item/action/episode/operation/revision/payload match. It
MUST return only a content-blind verdict and server-held source locator;
General SHALL derive the stored `source_ref` from that result. Absent,
fabricated, mismatched, unreadable, expired, non-owner, or unavailable
validation MUST fail closed before any item revision, event, episode, or
projection mutation. A receipt MAY authorize only its bound operation; a
second operation ID cannot reuse it. An identical replay of an already
committed operation MAY return the original General-held receipt without
revalidating external provenance because it does not write.

#### Scenario: Confirmed owner report authorizes only its exact transition

- **WHEN** an owner confirms the displayed item, action, episode, operation, expected revision, report fields, and observed time through the trusted confirmation path
- **THEN** Switchboard MAY mint one immutable receipt bound to that exact payload and the original owner statement
- **AND** General MUST accept a new transition only after the read-only validator confirms that binding and supplies the server-held source locator

#### Scenario: Spoofed or unavailable owner report cannot mutate custody

- **WHEN** an LLM supplies an invented source ID, a non-owner message, a receipt for different fields, or a receipt whose source cannot be read
- **THEN** Switchboard validation MUST refuse or report unavailable and General MUST leave revision, history, episode state, and projection unchanged
- **AND** a caller-asserted owner label, source reference, or statement quote MUST NOT bypass the validation seam
