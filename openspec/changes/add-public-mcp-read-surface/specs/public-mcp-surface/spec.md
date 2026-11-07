## Purpose

Serve the owner's own external AI clients (ChatGPT, including Dots, and claude.ai) a deterministic, read-only MCP tool surface over the Butlers projection database. Every result carries provenance and a freshness stamp; no request reaches a Butlers model, mutates Butlers state, or reveals data the allowlist manifest does not publish.

## ADDED Requirements

### Requirement: Standalone deterministic server with no model path
The public MCP server SHALL run as its own process (`butlers-public-mcp`) built from `butlers.mcp_public`, `butlers.capability_host` and `butlers.knowledge_read`, with no roster entry, scheduler, spawner, state store or Switchboard client. Its declared MCP server capabilities SHALL be exactly `tools`. No MCP call, of any method or tool, SHALL create a Butlers session, route, goal, schedule or ingestion event, or attempt a model-provider request. A contract check SHALL fail if any of the three packages imports, directly or transitively, a spawner, runtime, embedding, model SDK, Switchboard, notify, route or delegate module.

ID: REQ-public-mcp-surface-001
Source: RFC 0039 D1 and D14; owner decision OD8; heart-and-soul/vision.md Rules 2, 4 and 7
Scope: v1-mandatory

#### Scenario: Initialize declares tools only
- **WHEN** a client sends `initialize`
- **THEN** the declared server capabilities SHALL be exactly `tools`
- **AND** they SHALL include no `sampling`, `elicitation`, `resources`, `prompts` or `logging` capability

#### Scenario: A full tool run starts no reasoning
- **WHEN** every registered tool, `propose_fact` included, is called successfully and the proposal importer runs over the result
- **THEN** the counts of Butlers sessions, route inbox rows, goals, schedules and ingestion events SHALL be unchanged
- **AND** no outbound request to any model provider SHALL be attempted from the public server or the importer

#### Scenario: A banned import fails the contract check
- **WHEN** a change makes `butlers.knowledge_read` import `butlers.core.spawner` through any chain of modules
- **THEN** the import-ban contract check SHALL fail and name the import chain

### Requirement: Static pinned tool catalogue
The server SHALL register exactly the 19 tools of RFC 0039 D10 (`search`, `fetch`, `search_filtered`, `entity_lookup`, `entity_profile`, `graph_neighbors`, `graph_path`, `timeline`, `time_use`, `trips`, `describe`, `finance_summary`, `finance_ledger`, `finance_obligations`, `finance_records`, `health_series`, `health_profile`, `health_records`, `propose_fact`) and no generic `query`, `sql` or `invoke` tool. `tools/list` SHALL be identical regardless of kill-switch state or caller. Every tool SHALL declare `title`, `readOnlyHint`, `destructiveHint: false`, `idempotentHint: true` and `openWorldHint: false` annotations (`readOnlyHint: false` for `propose_fact` only) and an `outputSchema`. Every success result SHALL be returned as `structuredContent` and as the same object in one JSON text block. A call with an unknown argument or an argument outside its declared type, enum or length SHALL return `invalid_argument`, and text arguments SHALL be NFC-normalized before validation.

ID: REQ-public-mcp-surface-002
Source: RFC 0039 D5 and D10; owner decision OD17 (the two records tools); RFC 0027 (no meta-tools); craft-and-care/interfaces-and-dependencies.md Interface Hygiene
Scope: v1-mandatory

#### Scenario: The catalogue matches the pinned list
- **WHEN** `tools/list` is called
- **THEN** it SHALL return exactly the 19 named tools, with names and schema digests equal to the pinned catalogue

#### Scenario: A disabled domain does not change the list
- **WHEN** the `finance` domain is disabled and `tools/list` is called
- **THEN** the response SHALL be byte-identical to the response with every domain enabled

#### Scenario: Unknown argument is refused
- **WHEN** `entity_lookup` is called with an extra argument `sql`
- **THEN** the result SHALL be `invalid_argument` and no projection query SHALL run

#### Scenario: Results are duplicated as text for older clients
- **WHEN** any tool succeeds
- **THEN** the result SHALL carry `structuredContent` and one text block whose JSON parses to the same object

### Requirement: ChatGPT search contract
`search` SHALL take exactly one argument, `query` (a string of 1 to 256 characters), and SHALL return exactly `{results: [{id, title, url}]}` with at most 20 results, ranked lexically (full-text search blended with trigram similarity) over published `knowledge`, `relationship`, `time` and `travel` items only. It SHALL never return a `finance`, `health` or proposal record, and it SHALL omit items from disabled domains without error. The shape is a versioned compatibility contract: a field change requires a new tool version.

ID: REQ-public-mcp-surface-003
Source: RFC 0039 D10 (ChatGPT compatibility) and D11; owner decision OD6
Scope: v1-mandatory

#### Scenario: A name query returns ChatGPT-shaped results
- **WHEN** `search` is called with a published entity's name
- **THEN** the result SHALL be `{results: [...]}` whose items each carry exactly `id`, `title` and `url`
- **AND** there SHALL be no more than 20 items

#### Scenario: Ledger and health data never appear in search
- **WHEN** `search` is called with a merchant name that appears only in finance transactions
- **THEN** the result SHALL contain no finance item and SHALL be `{results: []}` when nothing else matches

#### Scenario: Disabled domains are silently omitted
- **WHEN** the `travel` domain is disabled and a query matches a trip
- **THEN** the trip SHALL be absent and the remaining matches SHALL be returned without an error

#### Scenario: Empty or oversized query is refused
- **WHEN** `search` is called with an empty query or one longer than 256 characters
- **THEN** the result SHALL be `invalid_argument`

### Requirement: ChatGPT fetch contract
`fetch` SHALL take exactly one argument, `id`, and SHALL return exactly `{id, title, text, url, metadata}`, where `metadata` is `{domain, kind, source, valid_at, truncated}` and `text` is at most 16 KiB; `truncated` SHALL be `true` exactly when `text` was cut to fit. An id that is not a published `search` item SHALL return `not_found`, including the id of a finance or health record. An id of the form `proposal:<uuid>` SHALL keep the same shape, with `title` = `"Proposal"`, `text` = the proposal's state, and `metadata` = `{domain: "proposals", kind: "proposal", source, valid_at: null, truncated: false, state, decided_at}`; it SHALL never carry the proposal's payload, note, evidence or submitter, and an unknown proposal id SHALL return `not_found`.

ID: REQ-public-mcp-surface-004
Source: RFC 0039 D10 and D13 step 6
Scope: v1-mandatory

#### Scenario: A search result is fetchable
- **WHEN** `fetch` is called with an `id` returned by `search`
- **THEN** the result SHALL carry exactly `id`, `title`, `text`, `url` and `metadata`, with `metadata.source` naming the source kind, uri, `as_of` and `content_authority`

#### Scenario: Long text is truncated and labelled
- **WHEN** the item's text exceeds 16 KiB
- **THEN** `text` SHALL be at most 16 KiB and `metadata.truncated` SHALL be `true`

#### Scenario: A finance id is not fetchable
- **WHEN** `fetch` is called with the `butlers://finance/...` uri of a transaction
- **THEN** the result SHALL be `not_found`

#### Scenario: Proposal status discloses state only
- **WHEN** `fetch("proposal:<uuid>")` is called for an applied proposal
- **THEN** the result SHALL carry exactly `id`, `title`, `text`, `url` and `metadata`, with `text` = `"applied"` and `metadata.state` = `applied` and its `decided_at`
- **AND** it SHALL contain no subject, predicate, object, note, evidence URL or submitter identity

### Requirement: Source envelope, deep links and freshness on every item
Every record item returned by a typed tool (an entity, fact, edge, timeline item, trip, ledger row, obligation, medication, condition, profile entry, or finance or health record) SHALL carry `source: {kind, uri, as_of, content_authority}`, where `uri` is `butlers://<domain>/<kind>/<id>`, `as_of` is the projection replication time read once per request, and `content_authority` is the source row's value or `null`. An aggregate output (`time_use`, `finance_summary`, `health_series`) SHALL carry one response-level `source` with `kind`, a `uri` naming the domain and tool, and `as_of`. Any `url` SHALL be a dashboard deep link built from a configured base URL that requires an owner dashboard session and carries no token, argument echo or query secret. When replication is between 2 and 15 minutes old, results SHALL still be served with `as_of`, and tools whose output has `sources_degraded` SHALL list each affected domain with reason `"lagging"`.

ID: REQ-public-mcp-surface-005
Source: RFC 0039 D10 rules and D17 (freshness); module-dashboard-read source envelope precedent
Scope: v1-mandatory

#### Scenario: An entity profile carries its provenance
- **WHEN** `entity_profile` returns facts
- **THEN** every fact SHALL carry a `source` with a `butlers://relationship/fact/<id>` uri, the request's `as_of`, and the fact's `content_authority`

#### Scenario: An aggregate carries one response-level source
- **WHEN** `health_series` returns points
- **THEN** the response SHALL carry one `source` with the request's `as_of`, and the points SHALL carry only `t` and `value`

#### Scenario: Lagging replication is reported, not hidden
- **WHEN** replication is 6 minutes behind and `search_filtered` is called
- **THEN** results SHALL be returned with `as_of` 6 minutes old
- **AND** `sources_degraded` SHALL list every domain with reason `"lagging"`

#### Scenario: Deep links carry no secret
- **WHEN** any item includes a `url`
- **THEN** the url SHALL start with the configured dashboard base and SHALL contain no query parameter derived from the arguments, the assertion or a token

### Requirement: Filtered search and describe
`search_filtered` SHALL accept `query`, optional `domains`, `kinds`, `since`, `until`, a `limit` of at most 50 and an optional `cursor`, and SHALL return `{items: [{id, title, snippet, domain, kind, url, source}], next_cursor, sources_degraded}` over the same published index as `search`, with disabled domains listed in `sources_degraded` with reason `"disabled"`. `describe` SHALL take no arguments and SHALL return `{server_version, manifest_digest, domains, as_of, tools, columns}`, where `domains` maps each domain to `enabled`, `disabled` or `stale`; a domain that any switch layer visible to the server disables SHALL report `disabled`, and otherwise a stale projection SHALL report `stale`. `describe` SHALL answer whenever the server and projection are reachable, including while every other tool refuses for staleness or kill switches.

ID: REQ-public-mcp-surface-006
Source: RFC 0039 D5, D10 and D17
Scope: v1-mandatory

#### Scenario: Requested disabled domain is named, not silently dropped
- **WHEN** `search_filtered` is called with `domains: ["travel"]` while `travel` is disabled
- **THEN** `items` SHALL be empty and `sources_degraded` SHALL include `{domain: "travel", reason: "disabled"}`

#### Scenario: Describe reports switch and freshness state
- **WHEN** `health` is disabled and `describe` is called
- **THEN** `domains.health` SHALL be `disabled`, every other domain `enabled`, and `manifest_digest` SHALL equal the replicated manifest digest

#### Scenario: Describe answers while stale
- **WHEN** replication is more than 15 minutes old
- **THEN** `describe` SHALL succeed with every enabled domain `stale`, while other tools return `projection_stale`

#### Scenario: Disabled wins over stale in describe
- **WHEN** replication is more than 15 minutes old and `finance` is disabled in the break-glass ConfigMap
- **THEN** `describe` SHALL report `domains.finance` as `disabled` and every other domain as `stale`

### Requirement: Entity and graph tools
`entity_lookup` SHALL return at most 10 matches `{entity_id, name, type, match, score}`, where `match` is `exact`, `alias` or `trigram`, SHALL resolve a merged entity to its surviving entity rather than return the merged one, and SHALL never return a deleted or unidentified entity, or one still named by its unknown-sender placeholder (REQ-public-mcp-projection-007). `entity_profile` SHALL accept `entity_id` and an optional `sections` subset of `facts` and `edges` (default both), SHALL return `{entity, facts, edges: {known, withheld}, coverage}` with the entity's published relationship facts and its published `relationship` memory facts (interactions, life events, notes, gifts, loans, tasks and reminders, each with its predicate and `valid_at`), whoever their subject is (owner decision OD21), and edge counts split into `known` and `withheld`, and SHALL never return a contact-kind (`has-email`, `has-phone`, `has-handle`, `has-address`, `has-birthday`, `has-website`), override-kind or memory contact- or identity-kind fact. `graph_neighbors` (1 to 2 hops, at most 200 results) and `graph_path` (at most 4 hops) SHALL match the behavior of REQ-entity-graph-003, -005, -006 and -009 over the replicated edges, including count-only withheld stubs, where the replicated edges and counts are only those REQ-public-mcp-projection-012 publishes, and an edge or count whose fact source's domain is disabled SHALL be dropped.

ID: REQ-public-mcp-surface-007
Source: RFC 0039 D2 (graph publish key and withheld counts), D4 (relationship row; people other than the owner) and D10; owner decision OD21; RFC 0031; REQ-entity-graph-003, -005, -006 and -009
Scope: v1-mandatory

#### Scenario: A merged name resolves to the survivor
- **WHEN** `entity_lookup` matches the canonical name of an entity merged into another
- **THEN** the match SHALL carry the surviving entity's id and name, and the merged entity SHALL not appear as its own match

#### Scenario: Contact facts never appear
- **WHEN** `entity_profile` is called for an entity that has a `has-email` fact
- **THEN** no returned fact SHALL have a contact-kind predicate and no email address SHALL appear in the result

#### Scenario: Withheld edges are counted, not shown
- **WHEN** an entity has 3 withheld stubs from `pii` `general` memory facts and 2 `normal` edges with allowlisted predicates
- **THEN** `graph_neighbors` SHALL return the 2 normal edges with receipts and report 3 withheld, with no withheld edge's endpoint, predicate or payload

#### Scenario: Interactions appear as profile facts
- **WHEN** `entity_profile` is called for a contact who has an `interaction` memory fact and a `contact_email` memory fact
- **THEN** `facts` SHALL include the interaction with its predicate and `valid_at`, and SHALL not include the email fact

#### Scenario: Edges from a disabled domain are dropped
- **WHEN** `knowledge` is disabled and an entity has a published edge from a `general` memory fact
- **THEN** `graph_neighbors` SHALL not return that edge

#### Scenario: Profile sections narrow the result
- **WHEN** `entity_profile` is called with `sections: ["edges"]`
- **THEN** the result SHALL carry the entity and edge counts and an empty `facts` list

#### Scenario: Unreachable pair returns a null path
- **WHEN** `graph_path` finds no path of at most `max_hops`
- **THEN** the result SHALL be `{path: null}` and not an error

### Requirement: Time and travel tools
`timeline` SHALL return `{items: [{at, end, kind, title, domain, source}], next_cursor, sources_degraded}` between `start` and `end` (a span of at most 92 days, at most 200 per page) from the published Chronicler episodes and trip legs. `time_use` SHALL return `{series: [{bucket_start, group, minutes}]}` (a span of at most 366 days, at most 400 points, bucket `day`, `week` or `month`, grouped by `category` or `kind`) from published rollups. `trips` SHALL return trips with legs, city-level places, carrier, status and travel-document expiry dates, and SHALL never return confirmation codes, document numbers, raw booking content or location fixes.

ID: REQ-public-mcp-surface-008
Source: RFC 0039 D2 (Chronicler `sensitive` episodes published) and D4 (time and travel rows) and D10; owner decisions OD17 and OD21; bu-s11n0s.7 location retention
Scope: v1-mandatory

#### Scenario: Restricted episodes are absent from the timeline
- **WHEN** the owner has restricted an episode through a Chronicler override and replication has caught up
- **THEN** `timeline` over that period SHALL not return the episode

#### Scenario: Sensitive episodes are on the timeline
- **WHEN** a `sensitive` workout episode and a `sensitive` Home Assistant presence episode fall in the requested period
- **THEN** `timeline` SHALL return both, with title, kind and times only

#### Scenario: Oversized span is refused
- **WHEN** `timeline` is called with a 120-day span
- **THEN** the result SHALL be `invalid_argument`

#### Scenario: Trips expose expiry, not document numbers
- **WHEN** `trips` returns a trip with a passport on file
- **THEN** the result SHALL include the passport expiry date and SHALL include no document number or booking confirmation code

### Requirement: Finance tools
`finance_summary` SHALL return totals per currency, never converted, with a `count`, grouped by `category`, `merchant` or `month` over a span of at most 366 days (at most 500 groups). `finance_ledger` SHALL return rows `{id, date, amount, currency, direction, merchant, category, description, notes, payment_method, receipt_url, external_ref, tags, account_label, account, source}` with `amount` as a decimal string, at most 500 per page with a cursor, excluding soft-deleted transactions, where `merchant` is the published `display_merchant` (the normalized-merchant overlay, else the raw merchant name), `category` is the published `display_category` (the inferred-category overlay, else the raw category), `account_label` is the account's institution and type, and `account` is `{id, institution, type, name, last_four}`; both are `null` when the transaction has no account. `finance_obligations` SHALL accept `horizon_days` from 1 to 90 and SHALL return `{items: [{payee, kind, amount, currency, cadence, next_due, status, payment_method, autopay, cancellation_url, notice_period_days, cancel_by, source}]}`, where `kind` is `bill` (from bills, `next_due` = `due_date`) or `subscription` (from active subscriptions, `next_due` = `next_renewal`), `amount` is a decimal string, a field the item's table lacks is `null`, and at most 200 items are returned. Finance tools SHALL return every published finance field they read, account names, `last_four` and balances included, and SHALL never return a credential-tier column or JSON path.

ID: REQ-public-mcp-surface-009
Source: RFC 0039 D2 (finance overlays), D4 (finance row; finance and health breadth) and D10; owner decisions OD3 and OD17
Scope: v1-mandatory

#### Scenario: Mixed currencies are not summed together
- **WHEN** a period has spend in SGD and USD and `finance_summary` is called
- **THEN** the result SHALL report one SGD total and one USD total and no converted total

#### Scenario: Amounts keep exact precision
- **WHEN** `finance_ledger` returns a transaction of 12.30
- **THEN** its `amount` SHALL be the string `"12.30"` and not a binary float

#### Scenario: Soft-deleted rows are hidden
- **WHEN** a transaction has `deleted_at` set and replication has caught up
- **THEN** `finance_ledger` and `finance_summary` SHALL exclude it

#### Scenario: Account identifiers are shared
- **WHEN** `finance_ledger` returns a row for an account that has `name` and `last_four` recorded
- **THEN** the row's `account` SHALL carry that name and `last_four` unredacted, and `account_label` SHALL be built from institution and type

#### Scenario: Raw merchant is shown without an overlay
- **WHEN** a transaction has no normalized-merchant overlay
- **THEN** its ledger row's `merchant` SHALL be the raw merchant name

#### Scenario: Obligations within the horizon
- **WHEN** `finance_obligations` is called with `horizon_days: 30` and a bill is due in 10 days and an active subscription renews in 45 days
- **THEN** the result SHALL list the bill with `kind: "bill"` and not the subscription

#### Scenario: Oversized horizon is refused
- **WHEN** `finance_obligations` is called with `horizon_days: 91`
- **THEN** the result SHALL be `invalid_argument`

### Requirement: Health tools
`health_series` SHALL accept a `metric` from an enum generated from the published `health.measurements_public` metric names, `start`, `end` (a span of at most 366 days) and a bucket of `raw`, `day` or `week`, and SHALL return `{unit, points: [{t, value}], source}` with numeric values only and at most 2,000 points; a `day` or `week` bucket SHALL return the arithmetic mean of the bucket's values, in the unit unchanged. `health_profile` SHALL accept an optional `sections` subset of `medications`, `conditions`, `allergies` and `providers` (default all) and SHALL return `{medications: [{name, dose, frequency, schedule, status, since, notes, source}], conditions: [{name, status, since, notes, source}], allergies: [{text, predicate, since, fields, source}], providers: [{text, predicate, entity_id, fields, source}]}`, where a medication's `status` is `active` or `stopped` and `since` is when it was recorded, a condition's `since` is its diagnosis date or `null`, allergies are the active `allergy` and `food_allergy` facts, providers are the active `doctor_name`, `doctor_primary_care` and `pharmacy` facts, `text` is the fact's content and `fields` its published JSON fields, with at most 200 entries per section. Health tools SHALL return notes, clinicians and providers, and SHALL never return a credential-tier column or JSON path.

ID: REQ-public-mcp-surface-010
Source: RFC 0039 D2 (health values), D4 (health row; finance and health breadth) and D10; owner decisions OD3 and OD17; [Observed] roster/health/.agents/skills/memory-taxonomy/SKILL.md (allergy and provider predicates)
Scope: v1-mandatory

#### Scenario: Unknown metric is refused
- **WHEN** `health_series` is called with a metric that is not in the generated enum
- **THEN** the result SHALL be `invalid_argument`

#### Scenario: Profile carries notes
- **WHEN** a medication fact carries a free-text note
- **THEN** `health_profile` SHALL return the medication's name, dose, schedule, status and note

#### Scenario: Allergies and providers are in the profile
- **WHEN** the owner has an active `allergy` fact "allergic to penicillin" and an active `doctor_primary_care` fact
- **THEN** `health_profile` SHALL list the allergy under `allergies` and the physician under `providers`, each with its `source`

#### Scenario: Day buckets return the mean
- **WHEN** `health_series` is called with `bucket: day` for a day with weight readings 70.0 and 71.0 kg
- **THEN** that day's point SHALL have `value` 70.5 and the response `unit` SHALL be `kg`

#### Scenario: Profile sections narrow the result
- **WHEN** `health_profile` is called with `sections: ["conditions"]`
- **THEN** the result SHALL carry conditions and empty `medications`, `allergies` and `providers` lists

#### Scenario: Too many raw points are refused
- **WHEN** a `raw` request would return more than 2,000 points
- **THEN** the result SHALL be `too_large` and name no data value

### Requirement: Reads are pure
No tool other than `propose_fact` SHALL write any row, update any counter or timestamp, or call memory search or recall code. In particular, a read SHALL NOT change any memory `reference_count`, last-fetch or decay state on the primary or the projection. `propose_fact` SHALL write only its inbox row.

ID: REQ-public-mcp-surface-011
Source: RFC 0039 D12
Scope: v1-mandatory

#### Scenario: Repeated reads leave ranking state unchanged
- **WHEN** the same fact is returned by `search`, `fetch` and `entity_profile` 50 times
- **THEN** the fact's `reference_count` and every last-access field SHALL be unchanged on the primary

#### Scenario: Read tools write nothing to the projection
- **WHEN** every read tool is called once
- **THEN** no table in the projection database SHALL have a changed row count or row version

### Requirement: Bounded responses and opaque cursors
Every tool SHALL enforce the per-tool limits of RFC 0039 D10. Every typed tool SHALL cap its response at 256 KiB, and a cut response SHALL carry a top-level `truncated: true` (absent otherwise). `search` SHALL never be truncated, so its exact shape never gains a field; `fetch` reports a cut `text` through `metadata.truncated`. Cursors SHALL be opaque, HMAC-bound to the caller `sub`, the tool, the argument digest and the position, SHALL expire after 15 minutes, and SHALL be invalid after a server restart. A cursor that is expired, tampered with, or presented by a different `sub` or with different arguments SHALL return `invalid_argument`.

ID: REQ-public-mcp-surface-012
Source: RFC 0039 D10 rules and performance budget
Scope: v1-mandatory

#### Scenario: A cursor pages through a result set
- **WHEN** `finance_ledger` returns `next_cursor` and the same caller repeats the call with that cursor and the same arguments
- **THEN** the next page SHALL follow the first without overlap or gap

#### Scenario: A cursor is bound to its caller
- **WHEN** a cursor issued to one `sub` is presented by another allowlisted `sub`
- **THEN** the result SHALL be `invalid_argument`

#### Scenario: A cursor dies with the process
- **WHEN** the server restarts and a cursor issued before the restart is presented
- **THEN** the result SHALL be `invalid_argument`

#### Scenario: Oversized response is truncated with notice
- **WHEN** a result would exceed 256 KiB
- **THEN** the response SHALL be at most 256 KiB and SHALL carry `truncated: true`

### Requirement: Fixed error shape and freshness refusal
A tool-level failure SHALL be an MCP tool result with `isError: true` and `structuredContent` `{error: {code, domain, retryable, retry_after_s, message}}`, where `code` is one of `domain_disabled`, `projection_stale`, `projection_version_mismatch`, `projection_apply_pending`, `budget_exhausted`, `invalid_argument`, `not_found`, `too_large`, `blocked` and `unavailable`, and `message` is a fixed string per code that never echoes an argument. When replication is more than 15 minutes old, or `mcp_read.replication_as_of()` is NULL, every tool except `describe` SHALL return `projection_stale`. When the server's built-in manifest digest differs from the replicated digest, every tool except `describe` SHALL return `projection_version_mismatch`. While the owner step is pending (REQ-public-mcp-projection-015), every tool except `describe` SHALL return `projection_apply_pending` with `retryable: true` and `retry_after_s: null`. When the projection is unreachable, tools SHALL return `unavailable` with `retryable: true`. When several refusals apply, the first in this order SHALL be returned: `blocked` (the canary latch), `invalid_argument`, `budget_exhausted`, `unavailable`, `projection_version_mismatch`, `projection_apply_pending`, `projection_stale`, `domain_disabled`, then `not_found` and `too_large`.

ID: REQ-public-mcp-surface-013
Source: RFC 0039 D4 (manifest digest; reconcile split) and D17 (owner step pending; precedence); owner decision OD14
Scope: v1-mandatory

#### Scenario: Staleness wins over a disabled domain
- **WHEN** replication is 16 minutes old and `finance` is disabled, and `finance_ledger` is called with valid arguments
- **THEN** the result SHALL be `projection_stale`

#### Scenario: An invalid call is refused before budgets
- **WHEN** a `(sub, cid)` has exhausted its call budget and sends a call with an unknown argument
- **THEN** the result SHALL be `invalid_argument`

#### Scenario: Stale projection refuses data
- **WHEN** replication last advanced 16 minutes ago and `entity_profile` is called
- **THEN** the result SHALL be `isError: true` with code `projection_stale` and no data

#### Scenario: Deploy skew refuses data
- **WHEN** the server was built from a manifest whose digest differs from `mcp_surface.manifest_state` on the projection
- **THEN** every tool except `describe` SHALL return `projection_version_mismatch`

#### Scenario: A pending owner step wins over staleness and switches
- **WHEN** the owner step is pending, replication is 16 minutes old and `finance` is disabled, and `finance_ledger` is called with valid arguments
- **THEN** the result SHALL be `projection_apply_pending`

#### Scenario: Deploy skew wins over a pending owner step
- **WHEN** the server's digest differs from both the replicated digest and the applied digest
- **THEN** every tool except `describe` SHALL return `projection_version_mismatch`

#### Scenario: Error messages never echo input
- **WHEN** `entity_lookup` is refused with `invalid_argument` for a 500-character name
- **THEN** the error `message` SHALL be the fixed `invalid_argument` string and SHALL contain no part of the name

#### Scenario: Projection outage is retryable
- **WHEN** the projection database refuses connections while the primary database keeps serving (for example because the connection limit is reached)
- **THEN** data tools SHALL return `unavailable` with `retryable: true`
- **AND** the primary database and butler daemons SHALL be unaffected

### Requirement: Finance and health records tools
`finance_records` and `health_records` SHALL make every published table of their domain readable. Each SHALL accept `kind` (a closed enum generated from the manifest's tables of that domain, `facts` included), optional `since` and `until` applied to the time column the manifest declares for that kind (a span of at most 366 days; a kind with no declared time column SHALL refuse them with `invalid_argument`), optional `predicate` and `query` (at most 256 characters, ranked lexically over `search_vector`) for `facts` only, an optional `cursor` and a `limit` of at most 200, and SHALL return `{items: [{kind, id, fields, json_fields, source}], next_cursor}`, where `id` is the value of the key column the manifest declares for that kind (REQ-public-mcp-projection-013), rendered as text and used as the `<id>` of the source uri, `fields` maps each published column of that kind to its value, with NUMERIC values as decimal strings, and `json_fields` maps each published JSON path of the row (REQ-public-mcp-projection-013) to its value. They SHALL read one table per call, accept no query language, and return no unpublished column or path. Each is a single-domain tool of its domain.

ID: REQ-public-mcp-surface-014
Source: RFC 0039 D4 (finance and health breadth) and D10 (records tools); owner decision OD17; RFC 0027 (no meta-tools)
Scope: v1-mandatory

#### Scenario: Balance snapshots are readable
- **WHEN** `finance_records` is called with `kind: "balance_snapshots"` and a 90-day window
- **THEN** the result SHALL list each snapshot in the window with its `account_id`, `balance` as a decimal string, `currency` and `as_of_date`

#### Scenario: Visits are readable
- **WHEN** `health_records` is called with `kind: "calendar_events"` for a period with one appointment on Health's calendar
- **THEN** the result SHALL list the appointment with its title, description, location and times

#### Scenario: A kind outside the manifest is refused
- **WHEN** `finance_records` is called with `kind: "butler_secrets"` or `kind: "sessions"`
- **THEN** the result SHALL be `invalid_argument` and no projection query SHALL run

#### Scenario: Fact filters apply only to facts
- **WHEN** `health_records` is called with `kind: "visit_briefs"` and a `predicate`
- **THEN** the result SHALL be `invalid_argument`

#### Scenario: A disabled domain refuses its records tool
- **WHEN** `health` is disabled and `health_records` is called with valid arguments
- **THEN** the result SHALL be `domain_disabled` naming `health`

## Source References

- Non-Negotiable Rule 2 (modules only add tools), Rule 3 (cross-domain access; RFC 0039 is a named exception), Rule 4 (deterministic infrastructure) and Rule 7 (transport is an edge responsibility), `about/heart-and-soul/vision.md`.
- RFC 0039 (`about/legends-and-lore/rfcs/0039-public-mcp-read-surface.md`), D1, D4, D5, D10 to D14 and D17; owner decisions OD3, OD6, OD8 and OD17.
- RFC 0031 (public entity graph projection) and `openspec/specs/entity-graph/spec.md` REQ-entity-graph-003, -005, -006 and -009.
- RFC 0027 (tool surface discovery: no meta-tools).
- `openspec/specs/module-dashboard-read/spec.md`, source-envelope precedent (its live-view data source is not reused).
