## Purpose

Give the public MCP server a separate, read-only copy of exactly the Butlers data the owner chose to expose, fed one way by PostgreSQL logical replication from one checked-in allowlist manifest. Credentials and private material never reach it, later restrictions remove rows from it, drift fails CI, and the public server cannot log in to the live database: the projection is a second database on the primary's cluster, and `pg_hba` and grants keep the public roles out of every other database. The migration Job owns the publication; the slot and subscription work is one generated owner step, run as the CNPG superuser by every `make deploy-dev` on dev (owner decision OD20) and by the owner elsewhere, and the public server refuses while that step is pending.

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
The publication SHALL never include `butler_secrets`, `public.entity_info`, `credentials`, `oauth_*` or `*_tokens` tables, `pending_actions`, `approval_events`, private-custody tables, raw message tables, Chronicler `overrides` or location fixes. It SHALL never include a JSONB column, and no manifest entry SHALL extract a JSONB key; a field held only in JSONB SHALL reach the projection only through a source-owned materialization (REQ-public-mcp-projection-013). The column denylist SHALL have two tiers. In every domain, the publication SHALL never include a column whose name matches an entry of the credential tier: `secret`, `token`, `password`, `credential`, `otp`, `cvv`, `card`, `api_key`, `private_key`, `access_key`, `client_key`, `signing_key`, `access_url`, `passcode`, `passphrase`, `pin`, `bearer`, `cookie` and `auth`; and, in every domain, no column whose name matches `embedding`, which is a column rule outside the credential tier. In every domain except `finance` and `health`, it SHALL never include a column whose name matches an entry of the breadth tier: `iban`, `account_number`, `cancellation_url`, `raw`, `body`, `metadata`, `tags`, `confirmation`, `pnr`, `seat`, `address`, `passport`, `document_number`, `last_four`, `phone`, `email`, `description`, `memo`, `note`, `notes`, `receipt_url`, `external_ref`, `source_message`, `source_value`, `source_schema`, `source_table`, `source_id`, `blob_ref` and `payment_method`. An entry SHALL match, case-insensitively, when its underscore-separated segments appear as a contiguous run of the column name's underscore-separated segments, and the JSON-field materialization (REQ-public-mcp-projection-013) SHALL apply the credential tier the same way to the segments of a JSON path, after splitting each key on `.`, `_`, `-`, whitespace and every lower-to-upper camelCase boundary (so `accessToken` yields `access` and `token`). Every published table outside `finance` and `health` that has a sensitivity or privacy column SHALL filter it to a literal list of safe values, and every published memory-fact table outside `finance` and `health` SHALL filter `predicate` to a literal list that holds no contact- or identity-kind predicate (REQ-public-mcp-projection-012). A column the manifest marks `dlp = "identifier"` SHALL belong to a `finance` or `health` table and SHALL not match the credential tier, and no column of `finance.json_fields_public` or `health.json_fields_public` SHALL carry that mark. A JSON path the manifest marks in `json_dlp_identifier` (a list of `{source_table, path}`, matched on the exact dotted path) SHALL belong to a `finance` or `health` source table and SHALL have no segment matching the credential tier. After a full initial sync, none of the canary values planted in excluded sources SHALL be present anywhere in the projection, and a projection probe SHALL check this at least every 30 minutes.

ID: REQ-public-mcp-projection-002
Source: RFC 0039 D2 (Why, no JSONB), D4 drift checks 3 and 4, and D15 (canaries and DLP scope); owner decisions OD3, OD17 and OD21; heart-and-soul/security.md private custody
Scope: v1-mandatory

#### Scenario: Denylisted table in the manifest fails CI
- **WHEN** a manifest edit adds `relationship.credentials` or `finance.butler_secrets`
- **THEN** the drift check SHALL fail and name the denylisted table

#### Scenario: Denylisted column name fails CI
- **WHEN** a manifest edit adds a column named `access_token` to any table, including a `finance` or `health` table, or `confirmation_number` to `travel.legs`
- **THEN** the drift check SHALL fail and name the column

#### Scenario: Breadth-tier names pass in finance and health only
- **WHEN** the manifest publishes `finance.accounts.last_four` and `finance.transactions.notes`, and a manifest edit adds a column named `notes` to a `travel` table
- **THEN** the drift check SHALL pass the finance and health columns and SHALL fail on the travel column

#### Scenario: A DLP mark on JSON field values fails CI
- **WHEN** a manifest edit marks `finance.json_fields_public.value_text` `dlp = "identifier"`, or lists `{source_table: "finance.facts", path: "portal_token"}` in `json_dlp_identifier`
- **THEN** the drift check SHALL fail and name the mark

#### Scenario: Segment matching does not flag innocent names
- **WHEN** the manifest publishes columns named `direction` and `withdrawal_kind`
- **THEN** the denylist check SHALL pass for them, because no denylist entry matches a contiguous run of their segments

#### Scenario: A JSONB column fails CI
- **WHEN** a manifest edit publishes a column of type `jsonb`, under any name
- **THEN** the drift check SHALL fail and name the column

#### Scenario: A memory-fact entry without a predicate list fails CI
- **WHEN** a manifest edit publishes `relationship` memory facts filtered on sensitivity and validity only, or adds `contact_phone` to a memory-fact predicate list
- **THEN** the drift check SHALL fail and name the entry

#### Scenario: Canaries are absent after a full sync
- **WHEN** a fresh projection completes its initial copy
- **THEN** a search of every projection table for every canary value SHALL find none

#### Scenario: A canary in the projection is an incident
- **WHEN** the periodic projection probe finds a canary value
- **THEN** a critical alert SHALL fire naming the projection table that holds it

### Requirement: Reconcile and the owner step generate publication and subscriber DDL
The work SHALL be split by who may do it. `butlers public-mcp reconcile` SHALL run idempotently in the migration Job (the step that applies migrations as the migration user, today the `dashboard-api` `db migrate` initContainer) after Alembic, as the migration user, and SHALL compute from the manifest alone and apply in database `butlers` the statements that make publication `butlers_public_v1` equal the manifest, and SHALL write the manifest's SHA-256 into `mcp_surface.manifest_state`, which replicates, in the same transaction as those publication statements. Reconcile SHALL create no slot and no subscription and SHALL change nothing in `butlers_public`. `butlers public-mcp owner-sql` SHALL print one SQL file, the owner step, computed from the manifest alone: it SHALL open no database connection, contain no secret, and be byte-identical for the same manifest. It is run as the CNPG superuser, by every `make deploy-dev` on dev (owner decision OD20) and by the owner elsewhere, with `kubectl exec -i -n postgres-<env> <instance> -- psql -X -U postgres -d butlers -f -`, the same path as `scripts/init-db.sql`. The file SHALL set `ON_ERROR_STOP` and SHALL switch databases only with psql `\connect`. It SHALL branch with `\gset` and `\if`, and every query feeding `\gset` SHALL return exactly one row; it SHALL never put `REFRESH PUBLICATION` or `DROP SUBSCRIPTION` in a `DO` block. Every stop in the file (both superuser checks, the digest check, the publication-match check and the no-credential check) SHALL end the run with a non-zero psql exit by raising an error under `ON_ERROR_STOP`, such as a `DO` block with `RAISE EXCEPTION` naming the check, and the file SHALL never contain `\q` or `\quit`. It SHALL, in order: (1) in `butlers`, stop unless the current user is a superuser, `mcp_surface.manifest_state` carries the file's digest, and `pg_publication_tables` for `butlers_public_v1` (schema, table, attnames and rowfilter) equals the expected rows embedded in the file, then run the database half of the no-credential live check (REQ-database-security-016) and stop if it fails; (2) in `butlers`, note whether the slot `butlers_public_sub` exists and whether it is invalidated; (3) decide that a rebuild is needed when no applied record exists, when the slot is invalidated, or when any already-applied table has a changed or removed manifest entry; (4) when a rebuild is needed, first delete the applied record, note the subscription's oid, disable the subscription, set its `slot_name` to `NONE`, drop it and truncate every mirrored table, then in `butlers` terminate any process still holding the old slot or a table-sync slot named `pg_<old oid>_sync_%`, wait a bounded time until each is inactive, and drop each of them; (5) in `butlers`, create the slot `butlers_public_sub` with `pg_create_logical_replication_slot` and the `pgoutput` plugin when it is absent; (6) in `butlers`, sync the publisher's `USAGE` to exactly the schemas and its `SELECT` to exactly the tables of `butlers_public_v1`, revoking both everywhere else; (7) in `butlers_public`, under `SET ROLE mcp_projection_owner`, converge the subscriber DDL (published columns plus declared local extras, the `mcp_read` views and the projection grants), acting only where the catalog differs and replacing a view only when its definition changed, then `RESET ROLE`; (8) check again that the current user is a superuser, create the subscription when absent with `connect = false`, `create_slot = false`, `enabled = false`, `password_required = false`, that slot name and conninfo on the instance's local socket as `butlers_projection_publisher`, and run `ALTER SUBSCRIPTION ... OWNER TO mcp_subscription_owner`, `ENABLE` and `REFRESH PUBLICATION`, which starts the initial copy of every table not yet copied; and (9), last, record the manifest's digest and canonical text in `mcp_local.manifest_applied` in `butlers_public`, keeping the existing row when it already carries both. Generated DDL and generated owner-step files SHALL not be checked into the repository. Subscriber tables SHALL be created before the subscription receives rows for them. The step SHALL run once at the first sync, once after each manifest change the migration Job has applied, and once after each slot invalidation. On dev the deploy SHALL run it: `make deploy-dev` SHALL run `scripts/k8s/public-mcp-owner-step.sh dev <tag>` on every deploy, after `bootstrap-dev` and after `record-deploy.sh` (the release, whose `dashboard-api` rollout runs the migrations and reconcile), whatever `record-deploy.sh` returned, and never before them; the recipe SHALL exit with the first non-zero status of its commands. The file's guard SHALL decide whether the step may act, so a release whose migrations did not reconcile this manifest SHALL make the guard refuse. The script SHALL select a running `dashboard-api` pod whose image tag equals `<tag>`, and SHALL fail when there is none. Before generating, it SHALL read that pod's command listing in a separate call and SHALL treat the image as older only when the listing has no exact `owner-sql` entry. It SHALL then generate the file in that pod with the image's `butlers public-mcp owner-sql`, SHALL capture the complete output before running anything and SHALL treat any non-zero exit of the listing or generation call, a usage error included, as a failure. It SHALL pipe the captured file to `psql` with `ON_ERROR_STOP=1` as the CNPG superuser in the `postgres-dev` primary through `kubectl exec`, selected by label, and SHALL write no file. On any failure it SHALL print a fixed `public-mcp owner step FAILED` marker with the error to stderr and SHALL make `make deploy-dev` exit non-zero, and it SHALL roll nothing back. When reconcile applied a manifest change, or the run had already deleted the applied record, the public server SHALL refuse with `projection_apply_pending` until a later run succeeds; otherwise the already-applied projection SHALL keep serving. After the owner step exits 0, `make deploy-dev` SHALL upgrade the public chart `deploy/helm/butlers-public-mcp` at the same tag, so no manual step remains on dev. A committed off switch in `deploy/helm/butlers/makefile` (`PUBLIC_MCP_OWNER_STEP_dev`) SHALL make the script print a notice and exit 0 before it does anything, and the recipe SHALL then skip the public chart, so a dev teardown stays down. When the deployed image has no `public-mcp owner-sql` command, and for any environment other than `dev`, the script SHALL print a notice and exit 0, so the step stays manual elsewhere.

ID: REQ-public-mcp-projection-003
Source: RFC 0039 D2, D3 (same-instance subscription; the owner step; the dev deploy runs it) and D4 (reconcile split; narrowing purges); owner decisions OD1 (low upkeep), OD10, OD14, OD18 and OD20; [Observed] `deploy/helm/butlers/makefile` (`deploy-%: bootstrap-%`) and `scripts/k8s/record-deploy.sh` (returns after the `dashboard-api` rollout, whose `migrations` initContainer runs reconcile); [Observed] PostgreSQL CREATE SUBSCRIPTION notes (a same-cluster subscription hangs if it creates its own slot); [Observed] PostgreSQL 17 DROP SUBSCRIPTION notes (after `slot_name = NONE` the slot and table-sync slots stay on the publisher) and psql `\gset` semantics (zero or several rows fail the command); [Observed] `postgres-dev` 2026-11-04 (the publisher holds `USAGE` on no Butlers schema); [Inferred] PostgreSQL 16+ `subscriptioncmds.c` (only a superuser sets `password_required = false`, and a non-superuser cannot alter or drop such a subscription)
Scope: v1-mandatory

#### Scenario: Same-instance subscription is created without hanging
- **WHEN** the owner step runs against a fresh `butlers_public` on the primary's instance after reconcile
- **THEN** the slot SHALL exist with plugin `pgoutput` before the subscription is created, `CREATE SUBSCRIPTION` SHALL complete without waiting, the subscription SHALL be owned by `mcp_subscription_owner` with `password_required = false`, and the initial copy SHALL fill every subscriber table

#### Scenario: Reconcile never touches the subscriber side
- **WHEN** reconcile runs in the migration Job
- **THEN** it SHALL change only the publication and `mcp_surface.manifest_state` in `butlers`, and SHALL create no slot, no subscription and no object in `butlers_public`

#### Scenario: The owner step refuses to run before reconcile
- **WHEN** the owner step for a new manifest runs while `mcp_surface.manifest_state` still carries the previous digest
- **THEN** it SHALL stop at its guard before changing any object

#### Scenario: A digest row alone does not pass the guard
- **WHEN** `mcp_surface.manifest_state` carries the file's digest but `butlers_public_v1` still has a wider column list or row filter than the manifest
- **THEN** the owner step SHALL stop at its guard before changing any object

#### Scenario: A first sync with nothing in place runs to the end
- **WHEN** the owner step runs where no slot, no subscription and no applied record exist
- **THEN** every existence probe SHALL return one row, no probe SHALL abort the run, and the run SHALL end with the applied digest recorded

#### Scenario: A rebuild leaves no slot behind
- **WHEN** the owner step rebuilds while a table's initial copy is still in progress
- **THEN** after the rebuild the primary SHALL hold no slot named `pg_<old oid>_sync_%` and no slot other than `butlers_public_sub` and the new subscription's table-sync slots

#### Scenario: The subscription is created as the superuser
- **WHEN** the owner step reaches the subscription statements after its subscriber DDL
- **THEN** the current user SHALL be the superuser, not `mcp_projection_owner`, and `CREATE SUBSCRIPTION` with `password_required = false` SHALL succeed

#### Scenario: Removing a published column purges its values
- **WHEN** a published column is removed from the manifest, reconcile runs and then the owner step runs
- **THEN** no projection row SHALL hold a value for that column afterwards

#### Scenario: Tightening a row filter purges rows that never change again
- **WHEN** a row filter is tightened so that an existing, unchanging source row no longer matches, and reconcile and the owner step run
- **THEN** that row SHALL be absent from the projection

#### Scenario: Rerun is a no-op
- **WHEN** reconcile, and then the owner step, each run twice with an unchanged manifest and a healthy slot
- **THEN** each second run SHALL rebuild nothing, truncate nothing, replace no view, leave the publication, the subscription, the slot and the applied record unchanged, and exit successfully

#### Scenario: Same manifest, same file
- **WHEN** `butlers public-mcp owner-sql` runs twice on the same manifest, on different machines
- **THEN** the two outputs SHALL be byte-identical and SHALL contain no password, certificate or token

#### Scenario: A run that stops midway is resumed by a rerun
- **WHEN** an owner step stops on an error after it deleted the applied record, and the owner reruns the same file
- **THEN** the rerun SHALL rebuild from the start and SHALL end with the applied digest equal to the manifest's

#### Scenario: Manifest digest replicates
- **WHEN** reconcile completes after a manifest edit
- **THEN** `mcp_surface.manifest_state` on the projection SHALL carry the new digest once replication catches up

#### Scenario: The dev deploy runs the owner step after its migrations
- **WHEN** `make deploy-dev` deploys a tag whose manifest adds a table
- **THEN** the owner step SHALL run after the `dashboard-api` rollout has completed, with a file generated in that rollout's image, and the deploy SHALL end with `mcp_read.manifest_applied` carrying the new digest and no manual step

#### Scenario: A failed dev owner step after a manifest change fails closed
- **WHEN** `make deploy-dev` ships a manifest change, reconcile applies it, and the owner step then stops on an error
- **THEN** `make deploy-dev` SHALL exit non-zero with the `public-mcp owner step FAILED` marker, the Butlers release and the reconciled publication SHALL stay in place, and every public tool except `describe` SHALL return `projection_apply_pending`

#### Scenario: A failed dev owner step with an unchanged manifest keeps serving
- **WHEN** `make deploy-dev` ships an unchanged manifest with a healthy slot, and the owner step fails before it deletes the applied record (for example on a failed grant sync)
- **THEN** `make deploy-dev` SHALL exit non-zero with the `public-mcp owner step FAILED` marker, and the public server SHALL keep serving the already-applied projection

#### Scenario: A guard stop fails the deploy
- **WHEN** the owner step's guard stops during `make deploy-dev`, for example because the no-credential check fails
- **THEN** psql SHALL exit non-zero, and `make deploy-dev` SHALL exit non-zero with the `public-mcp owner step FAILED` marker

#### Scenario: Failed migrations make the guard refuse
- **WHEN** the migrations fail during `make deploy-dev` before reconcile applies the new manifest
- **THEN** the owner step SHALL still run, its guard SHALL refuse before changing any object, the marker SHALL be printed, and `make deploy-dev` SHALL exit non-zero

#### Scenario: A readiness failure after the migrations still runs the owner step
- **WHEN** `make deploy-dev` ships a narrowed manifest, the `dashboard-api` rollout and its reconcile complete, and the readiness wait then fails
- **THEN** the owner step SHALL run and purge the narrowed rows, and `make deploy-dev` SHALL exit non-zero with the release's status

#### Scenario: Only the deployed tag's pod generates the file
- **WHEN** no running `dashboard-api` pod has the image tag being deployed
- **THEN** the script SHALL generate nothing, print the marker and exit non-zero

#### Scenario: A usage error is not an older image
- **WHEN** the image lists `owner-sql` but the generation call exits non-zero, a usage error included
- **THEN** the script SHALL print the marker and exit non-zero, and SHALL feed psql nothing

#### Scenario: The public chart follows the owner step
- **WHEN** `make deploy-dev` ships a manifest change and the owner step exits 0
- **THEN** the same recipe SHALL upgrade `deploy/helm/butlers-public-mcp` at the same tag, and the public server SHALL serve without `projection_version_mismatch` or a manual step

#### Scenario: The off switch keeps a dev teardown down
- **WHEN** `PUBLIC_MCP_OWNER_STEP_dev` is `off` and `make deploy-dev` runs after a teardown
- **THEN** the script SHALL print a notice and exit 0, the public chart SHALL not be reinstalled, and no slot named `butlers_public_sub` and no subscription SHALL be recreated

#### Scenario: A dev deploy with an unchanged manifest changes nothing
- **WHEN** `make deploy-dev` runs twice with the same manifest and a healthy slot
- **THEN** the second owner step SHALL rebuild nothing, truncate nothing and leave the applied record unchanged

#### Scenario: Adding a published table needs only a manifest edit and the owner step
- **WHEN** a manifest edit adds a table that needs no row filter, reconcile runs and the owner step runs
- **THEN** the table SHALL be published, created on the projection and initially copied without any hand-written DDL and without a rebuild of the other tables

### Requirement: Drift checks fail in the breaking change
CI SHALL run, on a PostgreSQL 17 test database with all migrations applied, checks that fail when: a manifest table or column does not exist; a row-filter column is nullable; a row-filtered table lacks a non-partial unique `NOT NULL` replica-identity index over its declared key columns plus its filter columns (a primary key that already covers the filter columns, with `REPLICA IDENTITY DEFAULT`, counts), or uses `REPLICA IDENTITY FULL`; an entry's column list omits a replica-identity column; an UPDATE or DELETE of a published row-filtered table fails on the primary with the real publication in place; the publication or subscriber tables differ from the manifest after reconcile; or a migration drops or renames a published or filter column.

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

#### Scenario: A filter column missing from the column list fails
- **WHEN** a manifest entry filters on `validity` but leaves `validity` out of its column list
- **THEN** the drift check SHALL fail and name the column, before any write to that table could fail on the primary

#### Scenario: Writes to every published row-filtered table succeed
- **WHEN** the drift check runs an UPDATE and a DELETE on each published row-filtered table, `public.entities`, the memory-fact tables and `public.entity_graph_withheld_counts` included, against a PG17 testcontainer with the real publication
- **THEN** every statement SHALL succeed

### Requirement: Later restriction removes the row from the projection
For every row-filtered table, the filter columns SHALL be part of the replica identity, so that an update moving a row out of the filter arrives at the projection as a delete. A source row that stops matching its filter (for example a fact reclassified to `pii`, a predicate leaving the allowlist, or a fact leaving the validity values its entry publishes) SHALL be absent from the projection once replication catches up, and a hard delete or erasure on the primary SHALL delete the projected row.

ID: REQ-public-mcp-projection-005
Source: RFC 0039 D2 (replica identity and restriction semantics); bu-3vm4xa (erasure propagates)
Scope: v1-mandatory

#### Scenario: Reclassification to pii removes the fact
- **WHEN** a published `general` memory fact's sensitivity changes from `normal` to `pii` on the primary
- **THEN** the fact SHALL be absent from the projection after replication catches up

#### Scenario: Retraction removes a relationship fact
- **WHEN** a published relationship fact's validity changes from `active` to `retracted`
- **THEN** the fact SHALL be absent from the projection and from `entity_profile` results

#### Scenario: Erasure propagates
- **WHEN** a published health fact is deleted on the primary
- **THEN** it SHALL be deleted from the projection

#### Scenario: A reclassified fact takes its edge with it
- **WHEN** a published `general` memory fact with an object entity is reclassified to `pii`, so its graph edge becomes a withheld stub
- **THEN** the edge's `publish_key` SHALL become empty and the edge SHALL be absent from the projection after replication catches up

### Requirement: Chronicler corrected visibility is published, not raw episodes
Chronicler episodes and rollups SHALL be published only through source-owned tables (`chronicler.episodes_public`, `chronicler.rollups_public`) that deterministic Chronicler code keeps equal to the corrected view, with one difference: `effective_privacy` SHALL fold an episode's privacy overrides oldest first, where an override whose `submitted_by` is `'owner'` (the passkey dashboard route) sets the value and any other override, such as one written through the LLM-callable `chronicler_submit_correction` tool, can only raise it in the order `normal` < `sensitive` < `restricted`. A model-chosen label therefore SHALL only narrow what the surface publishes, never widen it. Two further rules only withhold more: `tombstoned` SHALL stay true once any override has tombstoned the episode or a redaction names it for any reason but `source_privacy`, and a calendar title built from the event's location or description SHALL be replaced by a fixed label unless an override corrected it, because people who invite the owner write that text. Evidence-layer episodes SHALL have no row. Each privacy or tombstone override SHALL update the tables in the same transaction as the override write, and a sweep at least every 15 minutes SHALL repair any drift. The manifest SHALL filter them to `effective_privacy IN ('normal', 'sensitive') AND tombstoned = false`, so only `restricted` and tombstoned episodes are withheld. Raw `episodes`, episode payloads, point events and `overrides` SHALL never be published.

ID: REQ-public-mcp-projection-006
Source: RFC 0039 D2 (Chronicler exception; what `sensitive` covers; labels only narrow) and D4 (time row); owner decisions OD17 and OD21; [Observed] src/butlers/chronicler/rollups.py:55-56 (Chronicler's own default shows `normal` and `sensitive`); [Observed] roster/chronicler/modules/__init__.py:376-421 and roster/chronicler/api/router.py:518-523 (`submitted_by` is `'user'` from the tool and `'owner'` from the dashboard)
Scope: v1-mandatory

#### Scenario: A sensitive health episode is published
- **WHEN** the Google Health adapter writes a `sensitive` sleep episode
- **THEN** its `episodes_public` row SHALL reach the projection, and `timeline` SHALL return its title, kind and times without its payload

#### Scenario: A model cannot republish a restricted episode
- **WHEN** the owner restricts an episode on the dashboard, and a later override through `chronicler_submit_correction` sets its privacy to `normal`
- **THEN** its `episodes_public` row SHALL stay `restricted` and the episode SHALL stay absent from the projection

#### Scenario: The owner can lift a restriction
- **WHEN** the owner restricts an episode on the dashboard and later sets it back to `normal` on the dashboard
- **THEN** its `episodes_public` row SHALL become `normal` and the episode SHALL reach the projection

#### Scenario: Owner restriction leaves the projection
- **WHEN** the owner restricts a normal episode through a Chronicler override
- **THEN** in the same transaction its `episodes_public` row SHALL become `restricted`
- **AND** the episode SHALL be absent from the projection after replication catches up

#### Scenario: Tombstoned episode leaves the projection
- **WHEN** the owner tombstones an episode
- **THEN** it SHALL be absent from the projection and from `timeline` and `time_use`

#### Scenario: A later correction cannot undo a tombstone
- **WHEN** an episode is tombstoned through an override and a later override only adds a note
- **THEN** its `episodes_public` row SHALL stay tombstoned, although `v_episodes_corrected` reads only the latest override

#### Scenario: A calendar title from free text is withheld
- **WHEN** a calendar event has no summary, so its Chronicler title falls back to its location or description
- **THEN** its `episodes_public` title SHALL be the fixed label, and the episode SHALL keep its times and kind

#### Scenario: Sweep repairs drift
- **WHEN** an `episodes_public` row disagrees with the corrected view and the override fold
- **THEN** the next sweep SHALL rewrite it to match within 15 minutes

### Requirement: Entity flags are published as columns
`public.entities` SHALL carry four plain columns maintained on the primary by a trigger on every insert and update: `merged_into` (UUID, nullable) equal to `metadata ->> 'merged_into'` when that value is a UUID and NULL otherwise; `deleted` (BOOLEAN NOT NULL) true exactly when the entity is hidden from active surfaces: `metadata ->> 'deleted_at'` or `metadata ->> 'archived_at'` is not null, `metadata ->> 'tombstone'` (the dashboard forget route) or `metadata ->> 'archived'` (the dashboard archive route) is `'true'`, `listed` is false, or `metadata ->> 'merged_into'` is present but not a UUID, because readers treat any present value as a tombstone; `unidentified` (BOOLEAN NOT NULL) true exactly when `metadata ->> 'unidentified'` is `'true'`; and `placeholder_name` (BOOLEAN NOT NULL) true exactly when `canonical_name` equals `Unknown (` || `metadata ->> 'source_channel'` || ` ` || `metadata ->> 'source_value'` || `)`, the name an unknown sender's entity is created with. The manifest SHALL publish `id`, `canonical_name`, `entity_type`, `aliases`, `roles` and `merged_into`, plus the filter columns `deleted`, `unidentified` and `placeholder_name`, which no `mcp_read` view returns, filtered to `deleted = false AND unidentified = false AND placeholder_name = false` with all three filter columns in the replica identity and in the column list, and SHALL never publish `metadata`.

ID: REQ-public-mcp-projection-007
Source: RFC 0039 D2 (entity flags) and D4 (relationship row); [Observed] alembic/versions/core/core_002_identity.py:124-127 merge and soft-delete keys in metadata; [Observed] src/butlers/identity.py:1067-1071,1208-1210 unidentified-sender names embed the channel value, kept in `source_channel` and `source_value`; [Observed] src/butlers/api/routers/memory.py:2866-2872 promote clears `unidentified` without renaming; [Observed] roster/relationship/api/router.py:1161-1174 active-entity predicate; :5755 forget sets `tombstone`; roster/relationship/tools/contacts.py:336 sets `listed`
Scope: v1-mandatory

#### Scenario: Merge sets the published pointer
- **WHEN** an entity is merged into a survivor on the primary
- **THEN** its `merged_into` column SHALL equal the survivor's id in the same transaction
- **AND** the projection SHALL carry that pointer after replication catches up

#### Scenario: Columns and metadata never disagree
- **WHEN** a parity check compares `merged_into`, `deleted`, `unidentified` and `placeholder_name` with their `metadata` keys and `canonical_name` across all entities
- **THEN** it SHALL find no mismatch

#### Scenario: A soft-deleted entity leaves the projection
- **WHEN** an entity's `metadata` gains `deleted_at` on the primary
- **THEN** the entity SHALL be absent from the projection after replication catches up, and `entity_lookup` SHALL not return it

#### Scenario: A forgotten entity leaves the projection
- **WHEN** the dashboard forget route sets `metadata.tombstone` to true on the primary
- **THEN** the entity SHALL be absent from the projection after replication catches up

#### Scenario: An archived or unlisted entity leaves the projection
- **WHEN** the owner archives an entity, or its `listed` becomes false, on the primary
- **THEN** the entity SHALL be absent from the projection after replication catches up, and reversing it SHALL publish the entity again

#### Scenario: A malformed merge pointer withholds the entity
- **WHEN** an entity's `metadata ->> 'merged_into'` is present but not a UUID
- **THEN** the write SHALL succeed, its `merged_into` column SHALL be NULL, its `deleted` column SHALL be true and the entity SHALL be absent from the projection

#### Scenario: An unknown sender's address never leaves
- **WHEN** an entity was auto-created for an unknown email sender with a name such as `Unknown (email <address>)`
- **THEN** no projected column SHALL contain the sender address

#### Scenario: A promoted but unrenamed sender stays out
- **WHEN** the dashboard promotes an unknown email sender's entity, which clears `unidentified`, without renaming it
- **THEN** its `placeholder_name` SHALL stay true and the entity SHALL stay absent from the projection, and renaming it SHALL publish it under the new name

### Requirement: The public roles cannot reach the primary database
The projection SHALL be `butlers_public`, a second database on the primary's CNPG cluster `postgres-<env>`, and never the primary database `butlers`. Because the network cannot separate two databases on one instance, the database layer SHALL be the boundary. `pg_hba` user rules, which CNPG places after its fixed rules and before its catch-all, SHALL be `hostssl butlers_public mcp_public_ro <pod range> scram-sha-256` and `hostssl butlers_public mcp_proposals_reader <pod range> scram-sha-256`, then `host all mcp_public_ro all reject`, `host all mcp_proposals_reader all reject` and `host all butlers_projection_publisher all reject`; on dev the pod range is the single node's `10.42.0.0/24`. Because the public pod and the `butlers-<env>` pods share that range, roles and not source addresses SHALL separate them: the `mcp_public_ro` password SHALL be mounted only in `butlers-public-<env>` and the `mcp_proposals_reader` password only in `butlers-<env>`. A pod on a node outside the range SHALL fail to log in until the owner widens the rule. `CONNECT` on `butlers_public` SHALL be revoked from `PUBLIC`; neither role SHALL hold `CONNECT` on any other database, membership in any primary role, or `pg_read_all_stats`; and `mcp_public_ro` SHALL have a connection limit of 10, so it cannot exhaust the instance's connections. A contract test SHALL connect as each role to `butlers`, `postgres` and `template1` and expect refusal. The projection SHALL need no backups of its own: every mirrored table SHALL be rebuildable from the publication. A cluster restore SHALL drop `butlers_public` or keep its subscription disabled, so a restored copy never replicates from a live primary.

ID: REQ-public-mcp-projection-008
Source: RFC 0039 D3 (second database; `pg_hba` isolation; the address range does not separate pods; shared fate); owner decisions OD3 (defense beyond Cloudflare), OD10 (second database on the existing cluster) and OD14; [Observed] homelab PR Tzeusy/homelab#40 `pg_hba_file_rules` on `postgres-dev`
Scope: v1-mandatory

#### Scenario: Roles, not addresses, separate the pods in the shared range
- **WHEN** the rendered `butlers` and `butlers-public-mcp` charts are inspected for Secret mounts and ExternalSecrets
- **THEN** only the public server SHALL receive the `mcp_public_ro` password, and only the `butlers-up` pod SHALL receive the `mcp_proposals_reader` password

#### Scenario: Primary connection is refused
- **WHEN** a client connects to the primary database `butlers` as `mcp_public_ro` with that role's valid password
- **THEN** the connection SHALL be refused by `pg_hba`

#### Scenario: Every other database refuses the projection roles
- **WHEN** `mcp_public_ro` or `mcp_proposals_reader` connects to `butlers`, `postgres` or `template1`
- **THEN** the connection SHALL be refused by `pg_hba`, while the same role's connection to `butlers_public` SHALL succeed

#### Scenario: Projection rebuilds from scratch
- **WHEN** the projection database is dropped and re-created with a fresh subscription
- **THEN** after the initial copy every mirrored table SHALL equal the publication's current content

#### Scenario: A restored cluster does not replicate from the live primary
- **WHEN** a cluster backup that includes `butlers_public` is restored in a restore drill
- **THEN** `butlers_public` SHALL be dropped or its subscription SHALL stay disabled, and no slot on the live primary SHALL gain a consumer

### Requirement: Projection roles and grants
On the projection, `mcp_public_ro` SHALL have only `CONNECT`, `USAGE` on schema `mcp_read`, `SELECT` on `mcp_read` views (never on mirrored base tables, and never on `mcp_local`), column-limited `INSERT` on `mcp_inbox.proposals`, and `EXECUTE` on `mcp_read.replication_as_of()`, with a `statement_timeout` of 2 seconds. Because CNPG role specs cannot set `statement_timeout`, `scripts/init-db.sql`, run as the superuser (by `bootstrap-dev` in every `make deploy-dev` on dev, by the owner elsewhere), SHALL set it with `ALTER ROLE mcp_public_ro SET statement_timeout = '2s'`. `CONNECT` and `TEMPORARY` on the projection database SHALL be revoked from `PUBLIC`, so the role can create no table or temporary table. `mcp_proposals_reader` SHALL have only `SELECT` and `DELETE` on `mcp_inbox.proposals`. Nothing SHALL be granted to `PUBLIC`, and the projection SHALL contain exactly two `SECURITY DEFINER` functions, `mcp_read.replication_as_of()` and the inbox cap trigger function, each declared with `SET search_path = pg_catalog, pg_temp` and using only schema-qualified references. These grants SHALL persist across a rerun of the projection bootstrap.

ID: REQ-public-mcp-projection-009
Source: RFC 0039 D9 (projection roles and contract test); craft-and-care/security-and-secrets.md least privilege; [Observed] homelab report 2026-11-04 (CNPG role specs cannot set `statement_timeout`)
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
`mcp_read.replication_as_of()` SHALL return only the subscription's latest end time (`pg_stat_subscription.latest_end_time`) and SHALL be executable only by `mcp_public_ro`. Its owner SHALL hold no predefined monitoring role beyond what reading that column requires, and SHALL never hold `pg_read_all_stats`, which on the shared instance would expose the primary's session query text. The public server SHALL read it once per request and use it as `as_of` for every item in that response.

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
The primary SHALL have `max_slot_wal_keep_size` set to a finite value before the projection's slot is created (10GB on dev), and that cap plus the projection's own tables and indexes, which share the primary's disk, SHALL fit the disk's free space. The cap bounds only the WAL that slots retain; logical-decoding spill files and the WAL that the same-instance apply and initial copy write are outside it and SHALL be counted as unbounded terms in the capacity statement. When the slot is invalidated by that cap, the primary SHALL continue unaffected, the public surface SHALL report `projection_stale` once the 15-minute threshold passes, and recovery SHALL be a rebuild of the projection from a fresh copy by rerunning the owner step (REQ-public-mcp-projection-003). No Butlers daemon SHALL depend on the projection or its slot being healthy.

ID: REQ-public-mcp-projection-011
Source: RFC 0039 D3 (shared volume; shared disk), D9 (slot safety) and D17 (failure table); owner decisions OD10 and OD14; [Observed] homelab report 2026-11-04 (`max_slot_wal_keep_size = 10GB`; dev and prod share one 1.4T disk, 90 % full, 144G free); heart-and-soul/vision.md anti-pattern (always-on internet dependency)
Scope: v1-mandatory

#### Scenario: Invalidated slot costs only the projection
- **WHEN** the projection is down long enough for the slot to exceed `max_slot_wal_keep_size`
- **THEN** the slot SHALL be invalidated, the primary SHALL keep accepting writes, and butler daemons SHALL keep running

#### Scenario: Recovery is a rebuild
- **WHEN** the owner step reruns after an invalidation (on dev in the next deploy or by the script, elsewhere by the owner)
- **THEN** the step SHALL recreate the slot and the subscription, the projection SHALL resync from a fresh copy, and tools SHALL serve again once it is fresh

### Requirement: v1 domain exposure defaults
The v1 manifest SHALL publish only the following, each table in exactly one domain. No memory facts from the `home`, `switchboard` or `chronicler` schemas SHALL be published; memory facts of the `relationship`, `finance` and `health` schemas SHALL be published in their own domains as below. Facts whose subject is someone other than the owner SHALL be published under the same rules as any other fact (owner decision OD21); outside `finance` and `health` no contact- or identity-kind predicate SHALL be published in any domain: in memory facts `contact_phone`, `contact_email`, `birthday`, `lives_in`, `nickname` and any predicate naming a phone, email, address, handle, website, birthday or identity document, and in `relationship.entity_facts` every predicate the registry marks `contact` or `override`. In `finance` and `health` this exclusion does not apply in v1: those domains publish at the OD17 breadth, so other people's contact data held there (for example clinicians' phone numbers and Health calendar attendee and organizer emails in `health.json_fields_public`) is published, a carve-out from OD21 that the owner chose (OD22). Widening any domain SHALL require a manifest edit and an amendment of this requirement. In `finance` and `health`, which publish at full breadth (owner decision OD17), the butler infrastructure tables every schema carries (`butler_secrets`, `state`, `sessions`, `session_process_logs`, `scheduled_tasks`, `route_inbox`, `corrections`, and the memory module's tables other than `facts`) SHALL never be published, and memory facts SHALL be filtered to `validity = 'active'` with no sensitivity or predicate filter.
- **knowledge:** memory facts in `general`, `lifestyle` and `education` (`id`, `subject`, `predicate`, `content`, `valid_at`, `sensitivity`, `validity`, `content_authority`, `search_vector`) with sensitivity `normal`, validity `active` or `fading` (the lifecycle of their graph edges) and a predicate in a literal per-schema allowlist that contains no contact- or identity-kind predicate, with `sensitivity`, `validity` and `predicate` in the replica identity and in the column list;
- **travel:** memory facts in `travel` under the same columns and filters; `travel.trips` (`id`, `name`, `destination`, `start_date`, `end_date`, `status`); `travel.legs` (`id`, `trip_id`, `type`, `carrier`, `departure_city`, `departure_at`, `arrival_city`, `arrival_at`); `travel.documents` (`id`, `trip_id`, `type`, `expiry_date`);
- **relationship:** entities as in REQ-public-mcp-projection-007; relationship facts of every relational-kind predicate, the family-kind `family-of`, `partner-of`, `parent-of` and `child-of` included; memory facts in `relationship` under the knowledge columns and filters, so `interaction`, `life_event`, `contact_note`, `activity`, `gift`, `loan`, `contact_task` and `reminder` facts are published; `public.entity_graph_edges` (`id`, `subject_entity_id`, `predicate`, `object_entity_id`, `sensitivity`, `created_at`, and `publish_key` as a filter column that no `mcp_read` view returns) filtered to `sensitivity = 'normal' AND publish_key IN (...)`, where the literal list SHALL be generated from, and equal to, the union over the published `general`, `lifestyle`, `education`, `travel` and `relationship` memory-fact entries and `relationship.entity_facts` of `<source_schema>.<source_table>:<predicate>` for each predicate in that entry's allowlist, with `publish_key` and `sensitivity` in the replica identity; `public.entity_graph_withheld_counts` (`entity_id`, `source_key`, `withheld`) filtered to the `source_key` values of those same entries, as in REQ-public-mcp-projection-013; no edge or withheld count from a `finance`, `health`, `home`, `switchboard` or `chronicler` fact or from `public.owner_conditions`; and the read layer SHALL drop an edge or count whose source entry's domain is disabled;
- **time:** the Chronicler public tables of REQ-public-mcp-projection-006;
- **finance:** every column that is not JSONB and does not match the credential tier of `finance.transactions` (including `display_merchant` and `display_category`), `finance.accounts`, `finance.balance_snapshots`, `finance.bills`, `finance.subscriptions`, `finance.budgets`, `finance.categories`, `finance.merchant_mappings`, `finance.recurring_groups`, `finance.obligation_ledger`, `finance.transaction_corrections`, `finance.payment_destinations`, `finance.claim_match_bindings` and `finance.facts` (except `embedding`); and `finance.json_fields_public` (REQ-public-mcp-projection-013);
- **health:** every column that is not JSONB and does not match the credential tier of `health.facts` (except `embedding`), `health.visit_briefs`, Health's calendar tables `calendar_sources`, `calendar_events` and `calendar_event_instances`, and the health migration 001 tables still present (`medications`, `medication_doses`, `conditions`, `meals`, `symptoms`, `research`); the typed health tables and `health.json_fields_public` of REQ-public-mcp-projection-013; never `calendar_sync_cursors` or `calendar_action_log`;
- **proposals:** `relationship.external_fact_proposals` (`proposal_id`, `state`, `decided_at`);
- **no domain:** `mcp_surface.controls` (`domain`, `state`, `changed_at`) and `mcp_surface.manifest_state` (`digest`).

ID: REQ-public-mcp-projection-012
Source: RFC 0039 D2 (graph publish key and withheld counts), D4 (initial contents; finance and health breadth; people other than the owner) and D5 (domains attach to data); owner decisions OD3, OD17 and OD21; [Observed] src/butlers/modules/memory/storage.py:2066-2080 (every memory schema projects edges) and alembic/versions/core/core_215_entity_graph_edges.py:146 (`predicate` is nullable); [Observed] src/butlers/core/entity_graph_edges.py:598 and src/butlers/modules/memory/storage.py:3810-3819 (fading facts keep their live edges)
Scope: v1-mandatory

#### Scenario: Finance publishes account identifiers and balances
- **WHEN** the finance entries of the v1 manifest are inspected
- **THEN** they SHALL publish account `name` and `last_four`, every balance snapshot, and transaction `description`, `notes`, `source_message_id`, `external_ref`, `receipt_url` and `payment_method`, and subscription `cancellation_url`

#### Scenario: Finance and health memory facts of every sensitivity are published
- **WHEN** an active `health` memory fact has predicate `allergy` and sensitivity `pii`, and an active `finance` memory fact has sensitivity `confidential`
- **THEN** both SHALL be present on the projection, in the `health` and `finance` domains respectively

#### Scenario: Butler infrastructure stays private in finance and health
- **WHEN** the finance and health entries of the v1 manifest are inspected
- **THEN** they SHALL include no `butler_secrets`, `state`, `sessions`, `session_process_logs`, `scheduled_tasks`, `route_inbox`, `corrections`, memory `episodes` or `rules` table, and no `embedding` column

#### Scenario: Contact predicates are not published
- **WHEN** a relationship fact has predicate `has-phone`
- **THEN** it SHALL not be present on the projection

#### Scenario: Facts about other people are published, contact data is not
- **WHEN** `normal` active memory facts in the `relationship` schema about a contact who is not the owner have predicates `interaction` and `contact_phone`
- **THEN** the `interaction` fact SHALL be present on the projection in the `relationship` domain and the `contact_phone` fact SHALL be absent

#### Scenario: Family relationship facts are published
- **WHEN** an active relationship fact has predicate `parent-of` between two non-owner entities
- **THEN** it SHALL be present on the projection

#### Scenario: An edge never publishes what its fact withholds
- **WHEN** live `normal` graph edges come from a `home` memory fact, from a `general` memory fact whose predicate is off the `general` allowlist, and from a `health` memory fact
- **THEN** none of the three SHALL be present on the projection, and none SHALL count toward any published withheld count

#### Scenario: A fading fact stays published together with its edge
- **WHEN** a published `relationship` memory fact with an object entity decays from `active` to `fading`, and later expires
- **THEN** while it is `fading` the fact and its edge SHALL both be present on the projection, and once it expires both SHALL be absent

#### Scenario: Every published edge has a published fact
- **WHEN** the graph parity test compares published edges with published facts after a full sync
- **THEN** every published edge's source fact SHALL itself be present on the projection

#### Scenario: Health stubs are not reported as withheld
- **WHEN** a `confidential` `health` memory fact with an object entity has projected a withheld stub
- **THEN** no published `entity_graph_withheld_counts` row SHALL count it, while the fact itself SHALL be present in `health.facts` on the projection

#### Scenario: A predicate outside the allowlist stays private
- **WHEN** a `normal` active fact in the `general` memory schema has a predicate that is not in that schema's allowlist
- **THEN** it SHALL be absent from the projection

#### Scenario: Excluded memory schemas stay private
- **WHEN** a `normal` active fact exists in the `home` memory schema
- **THEN** it SHALL not be present on the projection

#### Scenario: A sensitive edge never reaches the projection
- **WHEN** a `pii` memory fact in the `general` schema with an object entity has projected a withheld stub
- **THEN** no projection table SHALL hold its predicate or object entity id, and its subject's published `withheld` count for `general.facts` SHALL include it

#### Scenario: Kill switches and the digest replicate
- **WHEN** the owner disables `finance` and reconcile has written a new digest, after the owner step has subscribed both tables
- **THEN** the projection's `mcp_surface.controls` and `mcp_surface.manifest_state` SHALL carry both after replication catches up

### Requirement: Source-owned materializations for fields held in JSONB
A field that the v1 tools serve but that lives only in JSONB, or behind an overlay, SHALL be published only from a plain column or table on the primary, owned by the source's migration chain, kept equal to its source by a database trigger (or, for Chronicler, by the writer plus a sweep), and covered by a parity test. In v1:
- `finance.transactions.display_merchant` SHALL equal `COALESCE(metadata ->> 'normalized_merchant', merchant)` and `display_category` SHALL equal `COALESCE(metadata ->> 'inferred_category', category)`, maintained by a `BEFORE INSERT OR UPDATE` trigger.
- `public.entity_graph_edges.publish_key` (TEXT NOT NULL) SHALL equal `<source_schema>.<source_table>:<predicate>` on a live edge and `''` on a withheld stub, maintained by a `BEFORE INSERT OR UPDATE` trigger on the edges table, because `predicate` is NULL on stubs and so cannot be a replica-identity column.
- `public.entity_graph_withheld_counts(entity_id, source_key, withheld)` SHALL have `PRIMARY KEY (entity_id, source_key)` with `REPLICA IDENTITY DEFAULT`, which covers its `source_key` filter, and SHALL hold, for each entity and each `<source_schema>.<source_table>`, the number of withheld stubs with that subject from that source, maintained by an `AFTER` trigger on the edges table.
- `health.measurements_public(id, metric, value_numeric, unit, measured_at)`, `health.medications_public(id, name, dosage, frequency, schedule, active, notes, recorded_at)` and `health.conditions_public(id, name, status, diagnosed_at, notes)` SHALL be maintained from `health.facts` by an `AFTER INSERT OR UPDATE OR DELETE` trigger that copies those keys, `notes` included, for active facts of every sensitivity of the measurement, medication and condition predicates, emits one measurement row per numeric key of a dict-valued reading (`metric` = `<predicate>.<key>`), and deletes the public row when the source fact stops qualifying.
- `finance.json_fields_public` and `health.json_fields_public` (`source_table`, `row_id`, `path`, `value_text`, `value_numeric`, keyed by the first three) SHALL be maintained by one generic trigger function, attached to every published `finance` and `health` table that has a JSONB column, holding one row per scalar leaf of those columns under its dotted path. `row_id` SHALL be TEXT holding the source row's single-column primary key rendered as text; the manifest SHALL declare each published finance and health table's key column (`id` by default, `event_id` for `health.visit_briefs`, `claim_id` for `finance.claim_match_bindings`), and the drift check SHALL refuse a published finance or health table without a single-column primary key. The function SHALL skip every path that has a segment matching the credential tier of the column denylist, with keys split into segments as REQ-public-mcp-projection-002 states, and SHALL delete a source row's fields when the row is deleted or stops qualifying for publication.

The dotted JSON path SHALL start with its source column name. Object keys SHALL escape backslash, dot and square brackets with backslash; zero-based array positions SHALL use unescaped `[n]` segments. Empty object keys SHALL retain empty segments and scalar roots SHALL use the column path. Credential matching SHALL precede encoding and retain ancestor token context. These rules SHALL distinguish literal dotted/backslash/bracket keys, arrays, numeric object keys and separate JSONB columns without dropping a permitted leaf.

Each JSON materialization SHALL retain `UNIQUE(source_table,row_id,path)` and an ordinary TEXT `id` primary key equal to `jsonb_build_array(source_table,row_id,path)::text`. `value_numeric` SHALL hold actual JSON numbers only. `value_text` SHALL hold string contents, number text or boolean text, or SQL NULL for a JSON null leaf; a plain `value_type` SHALL distinguish `string`, `number`, `boolean` and `null`. Empty containers SHALL emit no scalar row. The credential path exclusion SHALL also apply to numeric dict keys expanded into typed health measurements, so a typed metric cannot re-expose a denied JSON leaf.

Measurement IDs SHALL be TEXT source UUIDs for scalars and source UUID plus `:` plus the exact key for dict values. Metrics SHALL retain the full predicate, suffixed by `.<key>` for dict values. `measured_at` SHALL copy source `valid_at`, NULL included; a present metadata unit SHALL win, including explicit null, and omitted units SHALL use the existing writer unit maps, otherwise NULL. Medication `recorded_at` SHALL copy source `created_at`. Missing medication name/dosage/frequency SHALL default to empty strings, missing active to true and missing schedule to empty TEXT[]. Explicit null SHALL remain NULL. String schedules SHALL become one-element arrays; array order, duplicates and empty strings SHALL remain, with NULL slots for unsupported elements. Condition name SHALL fall back to content only when absent, status SHALL default to `active` only when absent, and `diagnosed_at` SHALL copy the stored string as TEXT. String notes SHALL copy exactly. Unsupported optional typed shapes SHALL become NULL without rejecting valid source writes; their permitted JSON scalar leaves SHALL remain available. No additional subject, scope, sensitivity or clinical-validation filter SHALL be introduced.

The source migration lifecycle SHALL install these boundaries both on populated upgrades and fresh core→butler→memory startup, including an already-at-head memory chain. Correct installed definitions and arguments SHALL require no recurring backfill or source-table lock on startup. Installation/backfill SHALL be transactional with bounded source locks, JSON backfill SHALL not UPDATE source rows, and downgrade/replay SHALL preserve independent schema consumers. These source guarantees SHALL NOT substitute for the manifest/reconcile/drift checks required by REQ-public-mcp-projection-004.

ID: REQ-public-mcp-projection-013
Source: RFC 0039 D2 (no JSONB; source-owned materializations; finance and health JSON fields; graph publish key and withheld counts); design.md S7; owner decisions OD1 (exporter only as a fallback), OD17 and OD19; [Observed] roster/finance/tools/spending.py overlay expressions; [Observed] roster/finance/tools/facts.py metadata keys; [Observed] roster/health/tools/measurements.py and medications.py metadata values
Scope: v1-mandatory

#### Scenario: A raw merchant shows when no normalized name exists
- **WHEN** a transaction has merchant `ACME*1234 SG` and no `normalized_merchant` overlay
- **THEN** its `display_merchant` SHALL be `ACME*1234 SG`, and `finance_ledger` SHALL return that value as `merchant`

#### Scenario: An overlay update reaches the published column
- **WHEN** a transaction's `metadata` gains `inferred_category = 'groceries'`
- **THEN** its `display_category` SHALL become `groceries` in the same transaction

#### Scenario: Health notes reach the public table
- **WHEN** a medication fact is stored with a free-text `notes` value
- **THEN** its `health.medications_public` row SHALL hold name, dosage, frequency, schedule, active state and the note

#### Scenario: JSON fields are published leaf by leaf
- **WHEN** a finance memory fact's `metadata` holds `{"amount": "12.30", "merchant": "ACME", "last_four": "4242"}`
- **THEN** `finance.json_fields_public` SHALL hold one row for each of the three paths, with `value_text` `"12.30"`, `"ACME"` and `"4242"`

#### Scenario: Credential-named JSON paths are never published
- **WHEN** a finance transaction's `metadata` holds `api_token` and a health fact's `metadata` holds `portal.password`
- **THEN** no `json_fields_public` row SHALL exist for either path, and the other paths of both rows SHALL be published

#### Scenario: camelCase, kebab-case and compound credential keys are never published
- **WHEN** a finance transaction's `metadata` holds `accessToken`, `Client-Secret`, `api_key` and `simplefin_access_url`
- **THEN** no `json_fields_public` row SHALL exist for any of the four paths

#### Scenario: A table keyed by another column publishes its key as text
- **WHEN** a `health.visit_briefs` row with `event_id` `evt-123:0` has a `digest` leaf `summary`
- **THEN** `health.json_fields_public` SHALL hold that leaf with `row_id` `evt-123:0`

#### Scenario: A retracted measurement leaves the public table
- **WHEN** a measurement fact's validity changes from `active` to `retracted`
- **THEN** its `health.measurements_public` rows SHALL be deleted in the same transaction

#### Scenario: A predicate change on an edge rewrites its publish key
- **WHEN** a live edge from `relationship.entity_facts` is upserted with a new predicate
- **THEN** its `publish_key` SHALL equal `relationship.entity_facts:<new predicate>` in the same transaction

#### Scenario: A withheld count updates without error
- **WHEN** a published `general` memory fact with an object entity is reclassified to `pii`, so its edge becomes a stub
- **THEN** the subject's `entity_graph_withheld_counts` row for `general.facts` SHALL update on the primary without error, and the projection's count SHALL change after replication catches up

#### Scenario: Parity holds across every source row
- **WHEN** the parity test compares each materialization with its source expression across all rows
- **THEN** it SHALL find no mismatch

### Requirement: Network path to the shared cluster
The projection's only new network path SHALL be from the public server pod to the `postgres-<env>` instance pods on 5432, and adding it SHALL widen no other rule. Where a default-deny ingress policy already selects the instance pods, exactly one allow SHALL be added, selecting only the instance pods and admitting only the public server pod (by namespace and pod label) on 5432; where none selects them, no policy SHALL be added, because a new policy would isolate the pods from the primary's other clients. On dev none selects them, so no policy is added; the allow SHALL be added only if a policy later selects the instance pods, and the live policy check (REQ-public-mcp-authorization-008) SHALL report when that happens. No other peer, port or egress rule on `postgres-<env>` SHALL change for the projection. The importer and the subscription SHALL use no new network path. Because the instance pods also serve the primary, the network layer SHALL NOT be relied on to separate the projection from the primary; `pg_hba` and grants (REQ-public-mcp-projection-008) SHALL be that boundary. Because `butlers-up` runs every roster butler in one pod, the `mcp_proposals_reader` grant and its `pg_hba` line SHALL be what limits the importer's access.

ID: REQ-public-mcp-projection-014
Source: RFC 0039 D3 (network) and D8 item 3; owner decision OD10; [Observed] homelab report 2026-11-04 (no NetworkPolicy, cluster-wide or CNI policy selects the `postgres-dev` pods); bu-kiuh8v (NetworkPolicy union trap); about/lay-and-land/deployment.md (one `butlers up` Deployment)
Scope: v1-mandatory

#### Scenario: The allow widens nothing else
- **WHEN** the union of policies selecting the `postgres-<env>` instance pods is compared before and after the change
- **THEN** it SHALL differ only by ingress from the public server pod on 5432

#### Scenario: The network does not separate the primary
- **WHEN** the public server pod opens a TCP connection to an instance pod on 5432 and requests database `butlers`
- **THEN** the TCP connection MAY succeed, and the login SHALL be refused by `pg_hba`

#### Scenario: The primary's existing clients are unaffected
- **WHEN** the change is applied on dev
- **THEN** the `butlers-up` pod, the migration Job, the CNPG operator and the subscription SHALL keep reaching the instance exactly as before

#### Scenario: A policy that later selects the instance pods is reported
- **WHEN** a NetworkPolicy that selects the `postgres-<env>` instance pods appears and admits no ingress from the public server pod
- **THEN** the live policy check SHALL fail and name the policy, so the public pod's single allow can be added

### Requirement: The public server fails closed while the owner step is pending
The owner step SHALL record, as its last statement, the applied manifest's digest and canonical text in `mcp_local.manifest_applied`, a subscriber-local table that is never published, and SHALL expose them to `mcp_public_ro` only through the view `mcp_read.manifest_applied`, which also reports whether the initial copy is complete. The view SHALL report the copy complete only when the subscription `butlers_public_sub` exists and is enabled and the set of its relations in state `r` equals the applied manifest's table set, so a missing, disabled or empty subscription is never complete. The public server SHALL read that view once per request and SHALL treat the owner step as pending when no applied record exists, when the applied digest differs from the digest of the manifest the server ships with (whether the shipped manifest is wider or narrower), or when the copy is not complete. A NULL `mcp_read.replication_as_of()` SHALL count as stale. While it is pending, every tool except `describe` SHALL return `projection_apply_pending` (REQ-public-mcp-surface-013). The server SHALL label a pending change `narrowing` when any applied table, column or row-filter value is absent from the shipped manifest or the shipped manifest filters a column the applied one did not, and `widening` otherwise, using the same deterministic comparison as the owner step's rebuild decision, and SHALL export `butlers_public_mcp_owner_step_pending` with that label as 1 while pending and 0 otherwise. When the digest matches and the copy is complete, the server SHALL run the projection canary probe once and SHALL serve only if it is clean. The migration Job MAY narrow the publication before the owner step purges the subscriber; this requirement is what keeps a server shipping the narrowed manifest from serving, in that window, rows the narrowed manifest excludes. A server still shipping the old manifest MAY serve the old rows until the new digest replicates and it refuses with `projection_version_mismatch`; because reconcile writes the digest in the same transaction as the publication change, that residual window SHALL be no longer than the replication lag, capped by the 15-minute stale rule. The guarantee assumes that only the owner step writes `mcp_local.manifest_applied`.

ID: REQ-public-mcp-projection-015
Source: RFC 0039 D3 (the owner step), D4 (reconcile split; narrowing purges), D16 (owner-step pending alerts) and D17 (owner step pending: fail closed); owner decision OD14 (the public server fails closed while the owner step is pending)
Scope: v1-mandatory

#### Scenario: The owner step is pending after a narrowing
- **WHEN** a manifest edit removes a published column, the migration Job has reconciled the publication and the new server is deployed, but the owner step has not run
- **THEN** every tool except `describe` SHALL return `projection_apply_pending`, `butlers_public_mcp_owner_step_pending{change="narrowing"}` SHALL be 1, and no response SHALL contain the removed column

#### Scenario: The owner step is pending after a widening
- **WHEN** a manifest edit adds a table, the migration Job has reconciled the publication and the new server is deployed, but the owner step has not run
- **THEN** every tool except `describe` SHALL return `projection_apply_pending`, `butlers_public_mcp_owner_step_pending{change="widening"}` SHALL be 1, and `butlers_public_mcp:warning_ok` SHALL be 0

#### Scenario: The digest matches again after the owner step
- **WHEN** the owner step runs for the deployed manifest (on dev in the deploy, elsewhere by the owner), every initial copy finishes and the canary probe is clean
- **THEN** `mcp_read.manifest_applied` SHALL carry the shipped digest, the tools SHALL serve again, and `butlers_public_mcp_owner_step_pending` SHALL be 0

#### Scenario: A re-copy in progress still refuses
- **WHEN** the applied digest matches the shipped digest but a table's initial copy has not finished
- **THEN** every tool except `describe` SHALL return `projection_apply_pending`

#### Scenario: A missing subscription is never a complete copy
- **WHEN** the applied digest matches the shipped digest but `butlers_public_sub` has been dropped or disabled, or lists none of the applied manifest's tables
- **THEN** `mcp_read.manifest_applied` SHALL report the copy incomplete and every tool except `describe` SHALL return `projection_apply_pending`

#### Scenario: The applied record is not readable as a table
- **WHEN** `mcp_public_ro` selects from `mcp_local.manifest_applied` directly
- **THEN** the query SHALL fail with `insufficient_privilege`, while the same columns SHALL be readable through `mcp_read.manifest_applied`

## Source References

- Non-Negotiable Rule 3 (cross-domain access; RFC 0039 is a named exception that reads a replicated copy, not the live store), `about/heart-and-soul/vision.md`.
- `about/heart-and-soul/architecture.md` exception mechanism and single-PostgreSQL anti-pattern, as amended by RFC 0039.
- RFC 0039 (`about/legends-and-lore/rfcs/0039-public-mcp-read-surface.md`), D2 to D4, D8, D9, D15 to D17; owner decisions OD1, OD3, OD10, OD14, OD15, OD17 and OD19 to OD21.
- Homelab PR Tzeusy/homelab#40 and its 2026-11-04 report (the CNPG state of `postgres-dev` that these requirements assume).
- RFC 0030 (system-plane read exception), sixth guardrail column allowlist as the template for the manifest.
- `openspec/specs/entity-identity/spec.md`, entity info table for per-entity properties and credentials (the basis for never publishing `entity_info`).
