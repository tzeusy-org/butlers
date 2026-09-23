## Context and allocation

`general.collection_items` already has an item UUID and JSONB `data`.
`item_get` and the General entity detail API read that item. Today
`item_update` reads `data` outside a transaction and later writes a merged
whole document; `item_delete` deletes the row and `collection_delete`
cascades to its items. None of those paths can protect a custody history from
a concurrent or generic write. `bu-2jtfw.9` is changing collection capture,
vocabulary, and ownership refusal, so its final General contract must be
reconciled before touching those paths.

The proposed first slice stays on an existing collection item. The UUID is
`collection_items.id`, not the collection UUID, a person's entity ID, a model
name, or a receipt ID. The owner identifies or selects that row before any
typed action. Similar descriptions do not imply the same physical object.

## Profile and answer shape

General may answer a locate question from a qualified existing item note only
when `data.last_reported_location` or `data.last_reported_custody` is paired
with `data.observed_at` and `data.source_ref`. Otherwise the affected facet is
unknown. This read-only slice needs no
transition history. A later opt-in profile is stored under a reserved
`data.possession_profile` key with `schema_version`, monotonic `revision`,
append-only `events`, and a projection derived from those events. Legacy
items are not auto-enrolled and their arbitrary `data` keys are not silently
converted to custody facts. No separate asset table or schema is proposed.

`possession_locate(item_id)` returns the item ID, the last qualified location
and custody separately, their observed and recorded times and source
references, any active borrowing episode, explicit conflicts, and retirement
status. Each facet can be unknown independently. The user-facing answer says
"last reported ... as of ..."; it never calls that a live location or current
ownership proof. An older report remains visible with its age. An unavailable
source is labelled unavailable, not treated as proof of absence.

`possession_record` accepts the existing item UUID, caller-stable
`operation_id`, `expected_revision`, a typed action, explicit owner statement
source reference, and an `observed_at` time. If the owner says "now", the
server may use its receipt time for `observed_at`; otherwise it must retain
the owner-stated time rather than fabricate one. The service sets
`recorded_at` and `event_id`. The receipt returns the same item, event,
operation, accepted revision, recorded time, and resulting qualified
projection. It never claims the assistant performed the physical action.
An absent source reference or unresolved item identity prevents a typed
transition; it may remain an ordinary unqualified note.

| Action | Required report | Projection effect |
| --- | --- | --- |
| `move` | Reported place, which may be an unresolved owner label | Updates only last-reported location; it does not infer who holds the object. |
| `borrow` | Owner reports taking custody from a named lender | Opens a server-identified borrowing episode; owner is reported custodian, location may remain unknown. |
| `lend` | Owner reports handing an object to a named borrower | Opens a lending episode; borrower is reported custodian, location may remain unknown. |
| `return` | Exact active `episode_id` and owner report of physical return | Closes only that episode. Borrow return reports custody back to lender; lend return reports custody back to owner. |
| `retire` | Explicit owner report | Removes the item from active-possession answers, keeping its item and history searchable. Open borrow/lend episodes require resolution first. |
| `correct` | Exact `supersedes_event_id`, corrected owner report, and reason | Appends a superseding event and recomputes the projection; it does not undo a physical act. |

At most one borrow or lend episode may be active for an item in this first
capability. A second `borrow` or `lend` while one is open is refused with the
active episode reference. `move` may add a reported place during an episode
but cannot change its custodian. An active episode is the only source of the
current reported borrower/lender custody projection; closing it leaves the
custody stated by that exact return report. This slice does not model
sub-lending or overlapping custodians.

The borrower/lender is not an item identity. A resolved person may be linked
through the existing Relationship/public entity resolution seam; a bare name
stays an unresolved label. A location label is not promoted into a canonical
place. A receipt reference is optional evidence, never an ownership claim.

## Concurrency, replay, and uncertainty

Every typed transition locks the selected `collection_items` row in one
PostgreSQL transaction, checks the stored profile revision, validates the
episode state, appends one event, updates the projection, increments the
revision, and commits atomically. A stale revision returns a conflict with the
new revision and the intervening event reference, without a write. Equal
revision contenders cannot both commit. Operation identity is checked under
the same lock before revision comparison: identical canonical input returns
the original event and receipt, even after later revisions; reuse of that
identity with different content fails without mutation. A return names and
closes one active episode; a missing, stale, already closed, or different
episode fails rather than closing another.

`observed_at` and `recorded_at` are distinct. A late report about an earlier
time is appended but cannot silently displace a later observed current
projection. Competing observations whose ordering or authority cannot be
resolved create an explicit conflict set for owner clarification. No sensor
absence, elapsed time, receipt, or unverified person name resolves it.
Correction appends a superseding event and recomputes from the valid history
under the same row lock; historic event and receipt IDs remain retrievable.
Correction may revise report fields such as place, party label, observation
time, or source, but must preserve the original action, event ID, episode ID,
and borrow/lend direction. Before commit it must replay all later events under
the lock. If the revision would invalidate a later return, episode ordering,
or another dependent transition, it refuses the correction with dependent
event references and leaves the profile unchanged. Changing an episode's
action or direction requires a separately specified remediation path, never
an implicit rewrite or orphaned return.

Generic `item_update` must reject writes into the reserved profile namespace.
For an opted-in item, **every** generic whole-document data or tag update,
even one touching only an unrelated key, must acquire the same row lock and
merge against the locked current row, or use a comparison that refuses a
stale revision. A writer that read revision 1 before a custody transition
committed revision 2 cannot later replace the revision-2 history with its
revision-1 document. `item_delete` and `collection_delete` must inspect and
refuse deletion of opted-in rows atomically, including cascade paths and a
concurrent opt-in race. Export must retain the whole profile and history.
There is no generic opt-out that silently drops history; an explicit future
owner deletion contract would require its own review.

## Specialist and privacy boundaries

General records what the owner reported. It never queries `finance.*` or
`relationship.*`, copies a receipt image, publishes a private object location
into `public.entities` or the public memory catalog, or operates a Home
device. Optional Finance evidence uses a Switchboard-brokered, read-only
Finance MCP result with a minimal reference, verification status, and source
version. If an existing Finance tool cannot provide that bounded result,
design and review that tool separately before linking receipts. A receipt
lookup that fails, is revoked, or is unavailable changes only the evidence
status; it cannot create an item, an ownership assertion, or a custody event.
A separately supplied owner statement may still be recorded with the receipt
door marked unavailable. A receipt-only request never changes custody.

Person identity stays Relationship-owned. A failed identity resolution keeps
the owner label unresolved and does not select a person by name similarity.
Home remains the automation owner and its physical-work refusal remains in
force. The General read/API surface stays behind the existing owner boundary;
only the minimal qualified projection is surfaced to the owner. Raw location
history, borrower labels, full statement text, and opaque source references
are not emitted to public catalog/search or cross-butler summaries.

## Rollback and implementation order

An owner correction appends a superseding report; rollback of this feature
must disable its new tools and preserve already written item profiles and
exports. No migration or destructive data rewrite is part of this spec.
Future storage evolution, if evidence later justifies it, requires a separate
contract and migration plan rather than quietly repurposing the profile.

After exact spec and bounded manifesto/routing adoption, integrate the
settled `bu-2jtfw.9` collection vocabulary and generic-write path first.
Then allocate one General implementation owner for the profile validator,
typed tools, item/update/delete/export fences, and read projection. Allocate
the optional Finance read contract to Finance's owner, the optional identity
link to the existing Relationship resolver, and any routing change to
Switchboard's owner. None of those optional links gates the owner-statement
locate slice. No implementation begins from this proposal alone.
