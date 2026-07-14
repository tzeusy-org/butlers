# Chronicler Intent/Evidence/Activity — Spec delta for chronicler-memory-writeback

## ADDED Requirements

### Requirement: Memory Write-Back Within Own Schema

The chronicler SHALL synthesize durable insights into a private memory schema
it owns (`chronicler_mem`) via the memory module, and MAY propose
entity-enrichment facts to the `relationship` butler over MCP. It SHALL NOT
write directly to another butler's schema, ingest external data, or notify the
owner. `chronicler_mem` is a bounded, module-private schema owned by the
chronicler (the memory module's `memory_schema` override), kept distinct from
the domain `chronicler.episodes` table; it is not generic cross-schema access.

#### Scenario: Insight written to own memory schema

- **WHEN** day-close synthesizes a durable insight (e.g. accumulating sleep debt)
- **THEN** the insight is written to the chronicler's own `chronicler_mem`
  memory tables with `source=chronicler` provenance and a confidence
- **AND** no other butler's schema is written directly

#### Scenario: Memory tables stay isolated from the domain schema

- **WHEN** the memory module is enabled for the chronicler
- **THEN** its `episodes`/`facts`/`rules` tables are created in `chronicler_mem`
- **AND** the domain `chronicler.episodes` table is preserved and unaffected

#### Scenario: Entity enrichment proposed over MCP

- **WHEN** repeated co-presence resolves to a person worth recording
- **THEN** the chronicler proposes the fact to `relationship` over MCP
- **AND** it does not write `entity_facts` directly

#### Scenario: Low-confidence block scheduled for re-reconciliation

- **WHEN** a block remains low-confidence at day-close
- **THEN** a self-reminder is recorded so a later day-close re-reconciles it
  after evidence backfill
- **AND** the owner is not notified
