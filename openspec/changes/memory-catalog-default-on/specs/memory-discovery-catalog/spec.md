# memory-discovery-catalog

## ADDED Requirements

### Requirement: Catalog write-behind defaults to enabled

The memory module's `enable_shared_catalog` feature flag SHALL default to
`True`. A butler's `butler.toml` `[modules.memory]` block MAY still set
`enable_shared_catalog = false` to opt that specific butler out of write-behind
(e.g. a deployment with no `public.memory_catalog` table, or a butler whose
facts must never surface cross-butler); toml is an opt-out, not a required
opt-in. When `catalog_source_schema` is not explicitly configured, the
`source_schema` written to catalog rows SHALL be inferred from the module's
database handle (falling back to the butler's own name when the handle
carries no schema), so the default-on behavior produces correctly attributed
catalog rows without requiring per-butler toml configuration.

#### Scenario: A memory-enabled butler catalogs facts/rules with no toml configuration

- **WHEN** a butler has `[modules.memory]` enabled in its `butler.toml` and
  does not set `enable_shared_catalog` or `catalog_source_schema`
- **THEN** `store_fact()` and `store_rule()` calls made through that butler's
  MCP tools MUST write a corresponding `public.memory_catalog` row
- **AND** the row's `source_schema` MUST match the butler's own schema (or its
  butler name, if the database handle carries no schema)

#### Scenario: A butler explicitly opts out via toml

- **WHEN** a butler's `butler.toml` sets `enable_shared_catalog = false`
  under `[modules.memory]`
- **THEN** `store_fact()` and `store_rule()` calls made through that butler's
  MCP tools MUST NOT write to `public.memory_catalog`

---

### Requirement: Backfill drains pre-existing facts/rules into the catalog

A bounded, idempotent backfill mechanism SHALL exist that catalogs facts and
rules written before write-behind was enabled (or before a specific butler
opted in). Each invocation SHALL process at most a fixed batch of rows per
table so that no single run holds a long-lived transaction across the full
backlog, and SHALL be safe to invoke repeatedly — already-cataloged rows
(matched via the existing `UNIQUE(source_schema, source_table, source_id)`
key) MUST be skipped rather than re-inserted or duplicated. The backfill
SHALL run as a module-default scheduled job so the backlog drains without
requiring a copy-pasted `butler.toml` schedule block per butler.

#### Scenario: Backfill catalogs active facts and rules not yet cataloged

- **WHEN** the backfill runs against a butler's schema containing active
  facts and non-forgotten rules with no corresponding `public.memory_catalog`
  row
- **THEN** each such fact/rule MUST receive a `public.memory_catalog` row
  keyed by `(source_schema, source_table, source_id)`
- **AND** the row MUST be discoverable via cross-butler catalog search

#### Scenario: Backfill excludes retracted facts and forgotten rules

- **WHEN** the backfill runs against a butler's schema containing a retracted
  fact (`validity != 'active'`) or a forgotten rule (`metadata.forgotten =
  true`)
- **THEN** neither MUST receive a `public.memory_catalog` row

#### Scenario: Backfill is idempotent across repeated runs

- **WHEN** the backfill is invoked a second time after a prior run already
  cataloged a fact or rule
- **THEN** the second run MUST NOT re-process, duplicate, or error on that
  already-cataloged row

#### Scenario: Backfill runs as a module default, not per-butler toml

- **WHEN** a butler with the memory module enabled boots with no
  `[[butler.schedule]]` block referencing the backfill job
- **THEN** the backfill job MUST still be scheduled and dispatchable for that
  butler

## Source References

- `memory-discovery-catalog` spec (Requirement: Catalog write-behind on
  memory store; Requirement: Cross-butler search via catalog).
- `docs/redesigns/2026-07-04-jarvis-pursuit.md`, ranked move #15 ("Turn on
  the cross-butler knowledge plane").
