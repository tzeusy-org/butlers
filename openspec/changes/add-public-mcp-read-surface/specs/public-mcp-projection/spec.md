## Purpose

Give the public MCP server a separate, read-only copy of exactly the Butlers data the owner chose to expose, fed one way by PostgreSQL logical replication from one checked-in allowlist manifest. Credentials and private material never reach it, later restrictions remove rows from it, drift fails CI, and the public server cannot reach the live database at all.

## ADDED Requirements

### Requirement: One allowlist manifest decides all exposure
`src/butlers/knowledge_read/projection.toml` SHALL be the only place where published tables, columns, row filters, domains and tool sources are declared. The primary SHALL have exactly one publication, `butlers_public_v1`, whose tables, column lists and row filters equal the manifest exactly, and the projection SHALL have exactly one subscription to it. A table or column not named in the manifest SHALL never be published, including tables and columns created after the manifest was written.

ID: REQ-public-mcp-projection-001
Source: RFC 0039 D2 and D4; owner decision OD1
Scope: v1-mandatory

#### Scenario: Publication equals manifest
- **WHEN** reconcile has run against a database with all migrations applied
- **THEN** `pg_publication_tables` for `butlers_public_v1` SHALL list exactly the manifest's tables, with attribute lists and row filters equal to the manifest

#### Scenario: A new column stays private by default
- **WHEN** a migration adds a column to a published table and the manifest is not edited
- **THEN** the column SHALL not be published and SHALL not exist on the projection

#### Scenario: A new table stays private by default
- **WHEN** a migration creates a new table in any schema
- **THEN** the table SHALL not be added to the publication

### Requirement: Credentials and private material are never published
The publication SHALL never include `butler_secrets`, `public.entity_info`, `credentials`, `oauth_*` or `*_tokens` tables, `pending_actions`, `approval_events`, private-custody tables, raw message tables, Chronicler `overrides` or location fixes. It SHALL never include a JSONB column, and no manifest entry SHALL extract a JSONB key; a field held only in JSONB SHALL reach the projection only through a source-owned materialization (REQ-public-mcp-projection-013). It SHALL never include a column whose name matches an entry of the column denylist: `secret`, `token`, `password`, `credential`, `otp`, `iban`, `account_number`, `card`, `cvv`, `cancellation_url`, `raw`, `body`, `metadata`, `tags`, `embedding`, `confirmation`, `pnr`, `seat`, `address`, `passport`, `document_number`, `last_four`, `phone`, `email`, `description`, `memo`, `note`, `notes`, `receipt_url`, `external_ref`, `source_message`, `source_value`, `source_schema`, `source_table`, `source_id`, `blob_ref` and `payment_method`. An entry SHALL match, case-insensitively, when its underscore-separated segments appear as a contiguous run of the column name's underscore-separated segments. Every published table that has a sensitivity or privacy column SHALL filter it to a literal safe value. After a full initial sync, none of the canary values planted in excluded sources SHALL be present anywhere in the projection, and a projection probe SHALL check this at least every 30 minutes.

ID: REQ-public-mcp-projection-002
Source: RFC 0039 D2 (Why, no JSONB), D4 drift checks 3 and 4, and D15 (canaries); owner decision OD3; heart-and-soul/security.md private custody
Scope: v1-mandatory

#### Scenario: Denylisted table in the manifest fails CI
- **WHEN** a manifest edit adds `relationship.credentials`
- **THEN** the drift check SHALL fail and name the denylisted table

#### Scenario: Denylisted column name fails CI
- **WHEN** a manifest edit adds a column named `access_token` to any table, or `confirmation_number` to `travel.legs`
- **THEN** the drift check SHALL fail and name the column

#### Scenario: Segment matching does not flag innocent names
- **WHEN** the manifest publishes columns named `direction` and `withdrawal_kind`
- **THEN** the denylist check SHALL pass for them, because no denylist entry matches a contiguous run of their segments

#### Scenario: A JSONB column fails CI
- **WHEN** a manifest edit publishes a column of type `jsonb`, under any name
- **THEN** the drift check SHALL fail and name the column

#### Scenario: Canaries are absent after a full sync
- **WHEN** a fresh projection completes its initial copy
- **THEN** a search of every projection table for every canary value SHALL find none

#### Scenario: A canary in the projection is an incident
- **WHEN** the periodic projection probe finds a canary value
- **THEN** a critical alert SHALL fire naming the projection table that holds it

### Requirement: Reconcile generates publication and subscriber DDL
`butlers public-mcp reconcile` SHALL compute, from the manifest alone, the primary-side publication statements and the subscriber-side table DDL (published columns plus declared local extras), and SHALL apply them idempotently in the migration Job after Alembic. It SHALL write the manifest's SHA-256 into `mcp_surface.manifest_state` on the primary, which replicates. Generated DDL SHALL not be checked into the repository. Subscriber tables SHALL be created before the subscription receives rows for them. When reconcile narrows a table (the table is removed, a column is removed or a row filter is tightened), it SHALL, in order, drop the removed subscriber columns, truncate the affected subscriber table, re-copy it from the publication, and re-run the canary probe, and the public server SHALL not serve that table's domain until the probe is clean.

ID: REQ-public-mcp-projection-003
Source: RFC 0039 D2 and D4 (narrowing purges); owner decision OD1 (low upkeep)
Scope: v1-mandatory

#### Scenario: Removing a published column purges its values
- **WHEN** a published column is removed from the manifest and reconcile runs
- **THEN** no projection row SHALL hold a value for that column afterwards

#### Scenario: Tightening a row filter purges rows that never change again
- **WHEN** a row filter is tightened so that an existing, unchanging source row no longer matches
- **THEN** after reconcile that row SHALL be absent from the projection

#### Scenario: Rerun is a no-op
- **WHEN** reconcile runs twice with an unchanged manifest
- **THEN** the second run SHALL make no catalog change and exit successfully

#### Scenario: Manifest digest replicates
- **WHEN** reconcile completes after a manifest edit
- **THEN** `mcp_surface.manifest_state` on the projection SHALL carry the new digest once replication catches up

#### Scenario: Adding a published table needs only a manifest edit
- **WHEN** a manifest edit adds a table that needs no row filter and reconcile runs
- **THEN** the table SHALL be published, created on the projection and initially copied without any hand-written DDL

### Requirement: Drift checks fail in the breaking change
CI SHALL run, on a PostgreSQL 17 test database with all migrations applied, checks that fail when: a manifest table or column does not exist; a row-filter column is nullable; a row-filtered table lacks a non-partial unique replica-identity index over `id` and its filter columns, or uses `REPLICA IDENTITY FULL`; the publication or subscriber tables differ from the manifest after reconcile; or a migration drops or renames a published or filter column.

ID: REQ-public-mcp-projection-004
Source: RFC 0039 D2 (PG15+ rules) and D4 drift checks 1, 2 and 5
Scope: v1-mandatory

#### Scenario: Dropping a published column fails its own PR
- **WHEN** a migration drops a column that the manifest publishes
- **THEN** the drift check SHALL fail in that PR and name the column and manifest entry

#### Scenario: Nullable filter column fails
- **WHEN** a manifest entry filters on a column that allows NULL
- **THEN** the drift check SHALL fail

#### Scenario: Full replica identity is refused
- **WHEN** a published table is set to `REPLICA IDENTITY FULL`
- **THEN** the drift check SHALL fail

### Requirement: Later restriction removes the row from the projection
For every row-filtered table, the filter columns SHALL be part of the replica identity, so that an update moving a row out of the filter arrives at the projection as a delete. A source row that stops matching its filter (for example a fact reclassified to `pii`, a predicate leaving the allowlist, or a fact leaving `active` validity) SHALL be absent from the projection once replication catches up, and a hard delete or erasure on the primary SHALL delete the projected row.

ID: REQ-public-mcp-projection-005
Source: RFC 0039 D2 (replica identity and restriction semantics); bu-3vm4xa (erasure propagates)
Scope: v1-mandatory

#### Scenario: Reclassification to pii removes the fact
- **WHEN** a published memory fact's sensitivity changes from `normal` to `pii` on the primary
- **THEN** the fact SHALL be absent from the projection after replication catches up

#### Scenario: Retraction removes a relationship fact
- **WHEN** a published relationship fact's validity changes from `active` to `retracted`
- **THEN** the fact SHALL be absent from the projection and from `entity_profile` results

#### Scenario: Erasure propagates
- **WHEN** a published health fact is deleted on the primary
- **THEN** it SHALL be deleted from the projection

### Requirement: Chronicler corrected visibility is published, not raw episodes
Chronicler episodes and rollups SHALL be published only through source-owned tables (`chronicler.episodes_public`, `chronicler.rollups_public`) that deterministic Chronicler code keeps equal to the corrected view. Each owner privacy or tombstone override SHALL update them in the same transaction as the override write, and a sweep at least every 15 minutes SHALL repair any drift. The manifest SHALL filter them to `effective_privacy = 'normal' AND tombstoned = false`. Raw `episodes` and `overrides` SHALL never be published.

ID: REQ-public-mcp-projection-006
Source: RFC 0039 D2 (Chronicler exception) and D4 (time row)
Scope: v1-mandatory

#### Scenario: Owner restriction leaves the projection
- **WHEN** the owner restricts a normal episode through a Chronicler override
- **THEN** in the same transaction its `episodes_public` row SHALL become non-normal
- **AND** the episode SHALL be absent from the projection after replication catches up

#### Scenario: Tombstoned episode leaves the projection
- **WHEN** the owner tombstones an episode
- **THEN** it SHALL be absent from the projection and from `timeline` and `time_use`

#### Scenario: Sweep repairs drift
- **WHEN** an `episodes_public` row disagrees with the corrected view
- **THEN** the next sweep SHALL rewrite it to match within 15 minutes

### Requirement: Entity flags are published as columns
`public.entities` SHALL carry three plain columns maintained on the primary by a trigger on every insert and update: `merged_into` (UUID, nullable) equal to `metadata ->> 'merged_into'`; `deleted` (BOOLEAN NOT NULL) true exactly when `metadata ->> 'deleted_at'` is not null; and `unidentified` (BOOLEAN NOT NULL) true exactly when `metadata ->> 'unidentified'` is `'true'`. The manifest SHALL publish `id`, `canonical_name`, `entity_type`, `aliases`, `roles` and `merged_into`, filtered to `deleted = false AND unidentified = false` with both filter columns in the replica identity, and SHALL never publish `metadata`.

ID: REQ-public-mcp-projection-007
Source: RFC 0039 D2 (entity flags) and D4 (relationship row); [Observed] alembic/versions/core/core_002_identity.py:124-127 merge and soft-delete keys in metadata; [Observed] src/butlers/identity.py unidentified-sender names embed the channel value
Scope: v1-mandatory

#### Scenario: Merge sets the published pointer
- **WHEN** an entity is merged into a survivor on the primary
- **THEN** its `merged_into` column SHALL equal the survivor's id in the same transaction
- **AND** the projection SHALL carry that pointer after replication catches up

#### Scenario: Columns and metadata never disagree
- **WHEN** a parity check compares `merged_into`, `deleted` and `unidentified` with their `metadata` keys across all entities
- **THEN** it SHALL find no mismatch

#### Scenario: A soft-deleted entity leaves the projection
- **WHEN** an entity's `metadata` gains `deleted_at` on the primary
- **THEN** the entity SHALL be absent from the projection after replication catches up, and `entity_lookup` SHALL not return it

#### Scenario: An unknown sender's address never leaves
- **WHEN** an entity was auto-created for an unknown email sender with a name such as `Unknown (email <address>)`
- **THEN** no projected column SHALL contain the sender address

### Requirement: The public role cannot reach the primary
The projection SHALL be a separate database from the primary, either on its own CNPG cluster or, if the owner chooses, as a second database on the primary's cluster. In either placement, a connection to the primary database as `mcp_public_ro` SHALL fail. When a second database is chosen, `pg_hba` SHALL admit `mcp_public_ro` only to `butlers_public` from the public pod's address range and SHALL reject it for every other database, in lines ordered before CNPG's defaults; a contract test SHALL connect as `mcp_public_ro` to `butlers`, `postgres` and `template1` and expect refusal, and that test SHALL replace the network-layer scenario of REQ-public-mcp-authorization-008 for the primary. The projection SHALL need no backups: every mirrored table SHALL be rebuildable from the publication.

ID: REQ-public-mcp-projection-008
Source: RFC 0039 D3 (placement pending the owner; fallback conditions); owner decision OD3 (defense beyond Cloudflare)
Scope: v1-mandatory

#### Scenario: Primary connection is refused
- **WHEN** a client connects to the primary database as `mcp_public_ro` with that role's valid password
- **THEN** the connection SHALL be refused

#### Scenario: Shared-cluster fallback refuses every other database
- **WHEN** the projection is a second database on the primary's cluster and `mcp_public_ro` connects to `postgres` or `template1`
- **THEN** the connection SHALL be refused by `pg_hba`

#### Scenario: Projection rebuilds from scratch
- **WHEN** the projection database is dropped and re-created with a fresh subscription
- **THEN** after the initial copy every mirrored table SHALL equal the publication's current content

### Requirement: Projection roles and grants
On the projection, `mcp_public_ro` SHALL have only `CONNECT`, `USAGE` on schema `mcp_read`, `SELECT` on `mcp_read` views (never on mirrored base tables), column-limited `INSERT` on `mcp_inbox.proposals`, and `EXECUTE` on `mcp_read.replication_as_of()`, with a `statement_timeout` of 2 seconds. `CONNECT` and `TEMPORARY` on the projection database SHALL be revoked from `PUBLIC`, so the role can create no table or temporary table. `mcp_proposals_reader` SHALL have only `SELECT` and `DELETE` on `mcp_inbox.proposals`. Nothing SHALL be granted to `PUBLIC`, and the projection SHALL contain exactly two `SECURITY DEFINER` functions, `mcp_read.replication_as_of()` and the inbox cap trigger function, each declared with `SET search_path = pg_catalog, pg_temp` and using only schema-qualified references. These grants SHALL persist across a rerun of the projection bootstrap.

ID: REQ-public-mcp-projection-009
Source: RFC 0039 D9 (projection roles and contract test); craft-and-care/security-and-secrets.md least privilege
Scope: v1-mandatory

#### Scenario: Privileges equal the allowlist
- **WHEN** the grant contract test enumerates `has_table_privilege` for `mcp_public_ro`, and `has_function_privilege` for every function outside `pg_catalog` and `information_schema`
- **THEN** the result SHALL equal the declared allowlist exactly

#### Scenario: No shadowing object can be created
- **WHEN** `mcp_public_ro` tries to create a temporary table named `proposals` or `pg_stat_subscription`
- **THEN** the statement SHALL fail with `insufficient_privilege`
- **AND** the inbox cap trigger and `replication_as_of()` SHALL behave as before

#### Scenario: Definer functions pin their search path
- **WHEN** the contract test reads `pg_proc.proconfig` for both definer functions
- **THEN** each SHALL carry `search_path=pg_catalog, pg_temp`

#### Scenario: Inbox is write-only for the public role
- **WHEN** `mcp_public_ro` runs `SELECT`, `UPDATE` or `DELETE` on `mcp_inbox.proposals`
- **THEN** each SHALL fail with `insufficient_privilege`

#### Scenario: Base tables are not readable
- **WHEN** `mcp_public_ro` selects from a mirrored base table instead of its `mcp_read` view
- **THEN** the query SHALL fail with `insufficient_privilege`

#### Scenario: Long query is cancelled
- **WHEN** a query as `mcp_public_ro` runs longer than 2 seconds
- **THEN** it SHALL be cancelled and the tool SHALL return `unavailable`

### Requirement: Replication freshness is measured on the projection
`mcp_read.replication_as_of()` SHALL return only the subscription's latest end time (`pg_stat_subscription.latest_end_time`) and SHALL be executable only by `mcp_public_ro`. Its owner SHALL hold no predefined monitoring role beyond what reading that column requires, and in the shared-cluster placement SHALL never hold `pg_read_all_stats`, which would expose the primary's session query text. The public server SHALL read it once per request and use it as `as_of` for every item in that response.

ID: REQ-public-mcp-projection-010
Source: RFC 0039 D9 (definer function) and D17 (freshness)
Scope: v1-mandatory

#### Scenario: Freshness comes from the subscription
- **WHEN** the subscription last confirmed a position at time T
- **THEN** `replication_as_of()` SHALL return T and the next response's `as_of` SHALL be T

#### Scenario: Function discloses nothing else
- **WHEN** `mcp_public_ro` calls `replication_as_of()`
- **THEN** the result SHALL be a single timestamp with no slot name, LSN, host or connection detail

### Requirement: Replication never endangers the primary
The primary SHALL have `max_slot_wal_keep_size` set to a finite value before the projection's slot is created. When the slot is invalidated by that cap, the primary SHALL continue unaffected, the public surface SHALL report `projection_stale` once the 15-minute threshold passes, and recovery SHALL be a rebuild of the projection from a fresh copy. No Butlers daemon SHALL depend on the projection or its slot being healthy.

ID: REQ-public-mcp-projection-011
Source: RFC 0039 D9 (slot safety) and D17 (failure table); heart-and-soul/vision.md anti-pattern (always-on internet dependency)
Scope: v1-mandatory

#### Scenario: Invalidated slot costs only the projection
- **WHEN** the projection is down long enough for the slot to exceed `max_slot_wal_keep_size`
- **THEN** the slot SHALL be invalidated, the primary SHALL keep accepting writes, and butler daemons SHALL keep running

#### Scenario: Recovery is a rebuild
- **WHEN** the owner recreates the subscription after an invalidation
- **THEN** the projection SHALL resync from a fresh copy and tools SHALL serve again once it is fresh

### Requirement: v1 domain exposure defaults
The v1 manifest SHALL publish only the following, each table in exactly one domain. No memory facts from the `relationship`, `home`, `switchboard`, `finance` or `health` schemas SHALL be published, and widening any domain SHALL require a manifest edit and an amendment of this requirement.
- **knowledge:** memory facts in `general`, `lifestyle` and `education` (`id`, `subject`, `predicate`, `content`, `valid_at`, `sensitivity`, `content_authority`, `search_vector`) with sensitivity `normal`, validity `active` and a predicate in a literal per-schema allowlist that contains no predicate whose registry scope is `relationship` and no contact-, interaction- or identity-kind predicate;
- **travel:** memory facts in `travel` under the same columns and filters; `travel.trips` (`id`, `name`, `destination`, `start_date`, `end_date`, `status`); `travel.legs` (`id`, `trip_id`, `type`, `carrier`, `departure_city`, `departure_at`, `arrival_city`, `arrival_at`); `travel.documents` (`id`, `trip_id`, `type`, `expiry_date`);
- **relationship:** entities as in REQ-public-mcp-projection-007; relationship facts of relational-kind predicates only; `public.entity_graph_edges` (`id`, `subject_entity_id`, `predicate`, `object_entity_id`, `sensitivity`, `created_at`) filtered to `sensitivity = 'normal'`; `public.entity_graph_withheld_counts` (`entity_id`, `withheld`) as in REQ-public-mcp-projection-013;
- **time:** the Chronicler public tables of REQ-public-mcp-projection-006;
- **finance:** `finance.transactions` (`id`, `account_id`, `posted_at`, `amount`, `currency`, `direction`, `display_merchant`, `display_category`, `deleted_at`); `finance.accounts` (`id`, `institution`, `type`); `finance.bills` (`id`, `payee`, `amount`, `currency`, `due_date`, `frequency`, `status`); `finance.subscriptions` (`id`, `service`, `amount`, `currency`, `frequency`, `next_renewal`, `status`);
- **health:** the health public tables of REQ-public-mcp-projection-013;
- **proposals:** `relationship.external_fact_proposals` (`proposal_id`, `state`, `decided_at`);
- **no domain:** `mcp_surface.controls` (`domain`, `state`, `changed_at`) and `mcp_surface.manifest_state` (`digest`).

ID: REQ-public-mcp-projection-012
Source: RFC 0039 D4 (initial contents) and D5 (domains attach to data); owner decision OD3
Scope: v1-mandatory

#### Scenario: Finance excludes account identifiers and balances
- **WHEN** the finance entries of the v1 manifest are inspected
- **THEN** they SHALL publish no account `name`, `last_four` or account number, no balance snapshot, and no transaction `description`, `notes`, `source_message_id`, `external_ref`, `receipt_url`, `payment_method`, `metadata` or cancellation URL

#### Scenario: Contact predicates are not published
- **WHEN** a relationship fact has predicate `has-phone`
- **THEN** it SHALL not be present on the projection

#### Scenario: Relationship-schema memory facts stay private
- **WHEN** a `normal` active memory fact in the `relationship` schema has predicate `contact_phone` or `interaction`
- **THEN** it SHALL be absent from the projection

#### Scenario: A predicate outside the allowlist stays private
- **WHEN** a `normal` active fact in the `general` memory schema has a predicate that is not in that schema's allowlist
- **THEN** it SHALL be absent from the projection

#### Scenario: Excluded memory schemas stay private
- **WHEN** a `normal` active fact exists in the `home` memory schema
- **THEN** it SHALL not be present on the projection

#### Scenario: A sensitive live edge never reaches the projection
- **WHEN** a live graph edge has sensitivity `pii`
- **THEN** no projection table SHALL hold its predicate or object entity id, and its subject's `withheld` count SHALL include it

#### Scenario: Kill switches and the digest replicate
- **WHEN** the owner disables `finance` and reconcile has written a new digest
- **THEN** the projection's `mcp_surface.controls` and `mcp_surface.manifest_state` SHALL carry both after replication catches up

### Requirement: Source-owned materializations for fields held in JSONB
A field that the v1 tools serve but that lives only in JSONB, or behind an overlay, SHALL be published only from a plain column or table on the primary, owned by the source's migration chain, kept equal to its source by a database trigger (or, for Chronicler, by the writer plus a sweep), and covered by a parity test. In v1:
- `finance.transactions.display_merchant` SHALL equal `COALESCE(metadata ->> 'normalized_merchant', merchant)` and `display_category` SHALL equal `COALESCE(metadata ->> 'inferred_category', category)`, maintained by a `BEFORE INSERT OR UPDATE` trigger.
- `public.entity_graph_withheld_counts(entity_id, withheld)` SHALL hold, for each entity, the number of `public.entity_graph_edges` rows with that subject whose sensitivity is not `normal`, maintained by a trigger on the edges table.
- `health.measurements_public(id, metric, value_numeric, unit, measured_at)`, `health.medications_public(id, name, dosage, frequency, schedule, active, recorded_at)` and `health.conditions_public(id, name, status, diagnosed_at)` SHALL be maintained from `health.facts` by an `AFTER INSERT OR UPDATE OR DELETE` trigger that copies only those keys, only for active, `normal` facts of the measurement, medication and condition predicates, never copies `notes` or any other key, emits one measurement row per numeric key of a dict-valued reading (`metric` = `<predicate>.<key>`), and deletes the public row when the source fact stops qualifying.

ID: REQ-public-mcp-projection-013
Source: RFC 0039 D2 (no JSONB; source-owned materializations); design.md S7; owner decision OD1 (exporter only as a fallback); [Observed] roster/finance/tools/spending.py overlay expressions; [Observed] roster/health/tools/measurements.py and medications.py metadata values
Scope: v1-mandatory

#### Scenario: A raw merchant shows when no normalized name exists
- **WHEN** a transaction has merchant `ACME*1234 SG` and no `normalized_merchant` overlay
- **THEN** its `display_merchant` SHALL be `ACME*1234 SG`, and `finance_ledger` SHALL return that value as `merchant`

#### Scenario: An overlay update reaches the published column
- **WHEN** a transaction's `metadata` gains `inferred_category = 'groceries'`
- **THEN** its `display_category` SHALL become `groceries` in the same transaction

#### Scenario: Health notes never reach a public table
- **WHEN** a medication fact is stored with a free-text `notes` value
- **THEN** its `health.medications_public` row SHALL hold name, dosage, frequency, schedule and active state, and no table in the projection SHALL hold the note

#### Scenario: A retracted measurement leaves the public table
- **WHEN** a measurement fact's validity changes from `active` to `retracted`
- **THEN** its `health.measurements_public` rows SHALL be deleted in the same transaction

#### Scenario: Parity holds across every source row
- **WHEN** the parity test compares each materialization with its source expression across all rows
- **THEN** it SHALL find no mismatch

### Requirement: Projection network ingress and egress
The projection database's pods SHALL run under NetworkPolicies that admit ingress only from the public server pod on 5432, the `butlers-up` pod in `butlers-<env>` on 5432 (the Relationship importer), the CNPG operator on its status port and 5432, and the projection's own instances, and that allow egress only to the kube-apiserver, cluster DNS and the primary database pods on 5432. Because `butlers-up` runs every roster butler in one pod, the network boundary for the importer SHALL be stated as pod-level, and the `mcp_proposals_reader` grant SHALL be what limits its access.

ID: REQ-public-mcp-projection-014
Source: RFC 0039 D8 item 3; about/lay-and-land/deployment.md (one `butlers up` Deployment)
Scope: v1-mandatory

#### Scenario: Another pod cannot reach the projection
- **WHEN** a pod other than the public server, the `butlers-up` pod, the CNPG operator or a projection instance connects to the projection on 5432
- **THEN** the connection SHALL be refused at the network layer

#### Scenario: The projection cluster can run under its policy
- **WHEN** the projection's policies are applied
- **THEN** the CNPG instance manager SHALL reach the kube-apiserver and DNS, the operator SHALL reach the status port, and the subscription SHALL reach the primary

## Source References

- Non-Negotiable Rule 3 (cross-domain access; RFC 0039 is a named exception that reads a replicated copy, not the live store), `about/heart-and-soul/vision.md`.
- `about/heart-and-soul/architecture.md` exception mechanism and single-PostgreSQL anti-pattern, as amended by RFC 0039.
- RFC 0039 (`about/legends-and-lore/rfcs/0039-public-mcp-read-surface.md`), D2 to D4, D9, D15 and D17; owner decisions OD1 and OD3.
- RFC 0030 (system-plane read exception), sixth guardrail column allowlist as the template for the manifest.
- `openspec/specs/entity-identity/spec.md`, entity info table for per-entity properties and credentials (the basis for never publishing `entity_info`).
