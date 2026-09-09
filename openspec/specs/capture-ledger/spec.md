# Capture Ledger

## Purpose

General's second brain gains a verb: `capture()`, a fleet-wide core tool that
durably records a thought before any classification is attempted, so a dead
session or a routing failure never silently loses it. This spec covers the
`public.captures` ledger and its receipt contract, the ownership-refusal
boundary that keeps another butler's owned content from being absorbed into
General, the collection-vocabulary naming boundary `item_create` resolves
through instead of auto-vivifying, bounded keyword search over collection
items, and cross-butler catalog discovery of routed captures. It also states
explicitly what this contract does not yet cover, so a reviewer or a future
implementer does not mistake omission for oversight.

## Requirements

### Requirement: Capture Ledger Durability

`capture()` SHALL write a `held` row to `public.captures` synchronously and
return its `capture_id` before any routing to a target table is attempted.
The row SHALL NOT be deleted; un-routing resets `receipt_state` rather than
removing the row.

#### Scenario: Synchronous held receipt precedes routing

- **WHEN** `capture()` is called with a channel and content
- **THEN** a `public.captures` row with `receipt_state = 'held'` MUST be
  committed and its `capture_id` returned before any target-table write is
  attempted
- **AND** the caller MUST receive a `capture_id` even if the process is
  killed immediately afterward

#### Scenario: A dead routing session leaves the row discoverable, not lost

- **WHEN** the session that called `capture()` terminates before routing
  completes
- **THEN** the `public.captures` row MUST remain `receipt_state = 'held'`
- **AND** it MUST be discoverable via `GET /api/captures?state=held`

#### Scenario: A routing failure is distinguished from an unattempted routing

- **WHEN** a target-table write raises during routing
- **THEN** the row's `receipt_state` MUST remain `'held'`
- **AND** `refusal_reason` MUST be set to a value beginning
  `target_write_failed:`
- **AND** `target_row_id` MUST remain NULL

### Requirement: Capture Idempotency

`capture()` SHALL accept an optional `mutation_id`. A transport retry with
the same `mutation_id` SHALL return the same `capture_id` and SHALL NOT
create a second row or re-run routing.

#### Scenario: Concurrent calls with the same mutation_id yield one row

- **WHEN** two `capture()` calls race with an identical `mutation_id`
- **THEN** exactly one `public.captures` row MUST exist for that
  `mutation_id`
- **AND** both calls MUST return the same `capture_id`

#### Scenario: A replayed mutation_id does not re-route

- **WHEN** `capture()` is called again with a `mutation_id` that already has
  a row
- **THEN** the existing row's current `receipt_state` MUST be returned
  unchanged
- **AND** routing MUST NOT be attempted a second time

### Requirement: A Routed Receipt Cites a Real Target Row

When `capture()` promotes a capture to `receipt_state = 'routed'`, the
receipt SHALL cite `target_schema`, `target_table`, and `target_row_id`, and
that row SHALL genuinely exist in the named table.

#### Scenario: Routed receipt SELECTs in its named table

- **WHEN** `capture()` returns `receipt_state = 'routed'`
- **THEN** a row MUST exist at `target_row_id` in `target_schema.target_table`
- **AND** the database MUST enforce this: a `'routed'` row with any of
  `target_schema`, `target_table`, `target_row_id` NULL is a constraint
  violation, not a runtime-only convention

### Requirement: Ownership Refusal

`capture()` SHALL refuse content that plainly belongs to another butler's
owned domain, naming that butler and the tool to use, rather than absorbing
it into a General collection.

#### Scenario: Bank-transaction content is refused naming Finance

- **WHEN** `capture()` is called with content matching a bank-transaction
  pattern (e.g. "Your bank account balance is now $42.10 after a
  withdrawal")
- **THEN** the row's `receipt_state` MUST be `'refused'`
- **AND** `refusal_reason` MUST name "Finance" and an existing Finance tool
  (`finance.record_transaction`)
- **AND** `target_row_id` MUST remain NULL

#### Scenario: A refused capture is never silently retried into General

- **WHEN** a capture is refused
- **THEN** no `collections`/`collection_items` row MUST be created for that
  capture's content

### Requirement: Ownership Refusal Coverage Is Bounded By Citable Tools

The ownership-refusal map SHALL only refuse a content kind when it can name
a real, existing tool for the owning butler. A kind SHALL NOT be added to
the map on the strength of the owning butler's domain alone.

#### Scenario: A kind with no citable tool is not refused

- **WHEN** a butler that owns a plausible content kind exposes no MCP tool
  that could record it (as of this spec's authoring, the Lifestyle butler
  registers no MCP tools at all)
- **THEN** content of that kind MUST NOT be refused on ownership grounds
- **AND** it falls through to the default (General collection) path like any
  other unmatched content
- **AND** this is deferred scope, not an oversight: the map SHOULD gain that
  kind once the owning butler exposes a tool to cite

### Requirement: Collection Vocabulary Resolution

`item_create` SHALL resolve a caller-supplied collection name through a
declared vocabulary (`collection_vocabulary` / `collection_aliases`) rather
than auto-vivifying a collection from any string. A name already present in
`collections` (created via `collection_create` or a prior `item_create`) is
itself proof of a prior deliberate action and resolves directly without a
vocabulary lookup.

#### Scenario: Exact and punctuation-insensitive names resolve silently

- **WHEN** `item_create` is called with a name that exactly matches a
  declared canonical name or alias, or matches one after normalizing case,
  whitespace, hyphens, and underscores (e.g. `email_records` against a
  declared `email-records`)
- **THEN** the item MUST be created in the resolved canonical collection
- **AND** no second, duplicate collection MUST be created for the
  off-punctuation spelling

#### Scenario: A near match resolves via trigram similarity

- **WHEN** `item_create` is called with a name that does not exactly or
  normalized-match any declared name, but scores at or above the trigram
  similarity threshold against exactly one declared name
- **THEN** the item MUST be created in that declared collection

#### Scenario: An unresolvable name raises and creates nothing

- **WHEN** `item_create` is called with a name that matches no declared name
  above the trigram threshold
- **THEN** it MUST raise, naming the nearest candidate names (if any) and the
  `collection_declare` verb
- **AND** no row MUST be created in `collections` or `collection_vocabulary`

### Requirement: Collection Declaration

`collection_declare` SHALL require a non-empty `shape_description` and SHALL
be safe under a concurrent re-declare of the same exact name.

#### Scenario: A collection with no shape is rejected

- **WHEN** `collection_declare` is called with an empty or whitespace-only
  `shape_description`
- **THEN** it MUST raise
- **AND** no `collection_vocabulary` row MUST be created

#### Scenario: Concurrent declares of the same name race safely to one row

- **WHEN** two `collection_declare` calls race with the identical canonical
  name
- **THEN** exactly one `collection_vocabulary` row MUST exist for that name
- **AND** both calls MUST return the same id

#### Scenario: A punctuation-twin of an existing name is refused

- **WHEN** `collection_declare` is called with a name that normalizes to the
  same key as an already-declared canonical name or alias (e.g. declaring
  `banking_transactions` when `banking-transactions` already exists)
- **THEN** it MUST raise, naming the existing declared name
- **AND** no second `collection_vocabulary` row MUST be created

### Requirement: Bounded Capture Search

`capture_search` SHALL find items by keyword using an indexed tsvector
rather than the unbounded JSONB-containment scan, with keyset pagination
bounded by an explicit limit.

#### Scenario: Keyword search finds prose under an invented key

- **WHEN** `capture_search` is called with a keyword query
- **THEN** items whose generated `search_vector` matches the query MUST be
  returned, regardless of what JSONB key the matching text is stored under

#### Scenario: Results are bounded and paginate via cursor

- **WHEN** `capture_search` is called with a `limit`
- **THEN** at most `limit` items MUST be returned
- **AND** when more results exist, a `next_cursor` MUST be returned that
  yields the next page with no overlap and no gap

#### Scenario: Search degrades honestly when the index is absent

- **WHEN** `capture_search` runs against a butler whose schema has not yet
  applied the `search_vector` migration
- **THEN** it MUST fall back to the JSONB-containment scan
- **AND** it MUST report `degraded: true` rather than silently returning
  results indistinguishable from the indexed path

### Requirement: Cross-Butler Catalog Discovery of Routed Captures

A routed capture SHALL be admitted into `public.memory_catalog` so it is
discoverable by other butlers through the existing catalog search path, with
no changes required to that search path.

#### Scenario: A captured note is visible to another butler

- **WHEN** a capture is routed into General's `collection_items`
- **THEN** a `public.memory_catalog` row MUST exist keyed on
  `(source_schema, source_table, source_id)` for that item, with
  `memory_type = 'capture'`
- **AND** a fleet-knowledge search from a different butler for matching
  keywords MUST surface it

#### Scenario: A butler does not see its own captures echoed back

- **WHEN** the fleet-knowledge search is run by the same butler that owns
  the captured row (General)
- **THEN** that row MUST be excluded from the results, consistent with the
  existing own-butler exclusion the catalog search already applies to facts
  and rules

### Requirement: Held-Capture Discovery Endpoint

`GET /api/captures` SHALL support filtering by `receipt_state`, SHALL always
resolve HTTP 200, and SHALL report a degraded envelope rather than a false
empty page when its data source is unreachable.

#### Scenario: The held lane returns orphaned captures

- **WHEN** `GET /api/captures?state=held` is called
- **THEN** every currently-`held` capture MUST appear in the response, most
  recent first

#### Scenario: An unreachable pool degrades honestly

- **WHEN** the captures ledger's database pool is unavailable
- **THEN** the response MUST still be HTTP 200
- **AND** `meta.sources_degraded` MUST be non-empty
- **AND** an empty `data` array under that condition MUST NOT be read as "no
  held captures"

#### Scenario: A held capture renders as held, never as saved

- **WHEN** the dashboard renders a capture with `receipt_state = 'held'`
- **THEN** it MUST be labeled as held/pending
- **AND** it MUST NOT be presented as saved, filed, or otherwise complete

### Requirement: Deferred Scope

This contract intentionally does not yet cover cross-butler routing
dispatch, Switchboard classification, the Dispatch held-capture action lane,
or attachment extraction. These SHALL be treated as open follow-up work, not
silently-abandoned scope.

#### Scenario: A capture from a non-General butler stays held by design

- **WHEN** `capture()` is called on a butler other than General, and the
  content is not refused on ownership grounds
- **THEN** the row MUST remain `receipt_state = 'held'`
- **AND** this MUST NOT be treated as a failure: cross-butler routing
  dispatch (mirroring the existing Switchboard-brokered dispatch pattern
  used elsewhere in core tools) is deferred follow-up work, not part of this
  contract

#### Scenario: No Switchboard classification outcome exists yet

- **WHEN** a message arrives that a human would recognize as a capture-shaped
  thought
- **THEN** Switchboard's structured classifier MUST NOT be assumed to route
  it to `capture()` automatically: no `capture` classification outcome is
  wired in this contract, and a caller must invoke `capture()` directly

#### Scenario: No Dispatch action lane or attachment extraction exists yet

- **WHEN** a held capture needs an owner-facing action surface, or a capture
  carries an attachment
- **THEN** neither a Dispatch held-capture keyboard-verb lane nor attachment
  text extraction MUST be assumed to exist: both are out of scope for this
  contract
