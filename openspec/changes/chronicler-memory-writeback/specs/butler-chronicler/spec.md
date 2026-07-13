# Chronicler Butler — Spec delta for chronicler-memory-writeback

## MODIFIED Requirements

### Requirement: Retrospective-Only Scope

The Chronicler butler SHALL reconstruct past time from already-captured
evidence and SHALL NOT plan, schedule, dispatch, ingest externally, or send
proactive notifications. The single sanctioned owner-facing message is the
existing once-daily retrospective day-close summary: a scheduled recap, not a
proactive notification, and no other owner-facing messages are permitted.

**Amendment (memory write-back):** the Chronicler MAY synthesize durable
insights into a private memory schema it owns (`chronicler_mem`) via the memory
module, and MAY propose entity-enrichment facts to the `relationship` butler
over MCP. These derived write-backs add no new owner-facing message, do not
constitute ingestion or scheduler authority, and never write another butler's
schema directly. `chronicler_mem` is a bounded, module-private schema owned by
the Chronicler, kept distinct from the domain `chronicler.episodes` table; it
is not generic cross-schema access.

#### Scenario: No ingestion ownership

- **WHEN** a passive timestamped event arrives (Spotify playback change,
  Steam game start, OwnTracks point, Home Assistant state change, Google
  Health reading)
- **THEN** Chronicler SHALL NOT receive the event directly
- **AND** the event SHALL route to its owning domain butler per existing
  Switchboard rules
- **AND** Chronicler's projection of that event SHALL run asynchronously
  on schedule, reading only from the owning domain's approved read surface

#### Scenario: No scheduling or planning

- **WHEN** a user request asks "schedule X" or "plan Y"
- **THEN** the Switchboard SHALL NOT route the request to Chronicler
- **AND** Chronicler's tool surface SHALL NOT include scheduling tools

#### Scenario: Synthesized insight stays within own schema

- **WHEN** the Chronicler synthesizes a durable insight at day-close
- **THEN** it is written only to the Chronicler's own `chronicler_mem` schema
- **AND** no external data is ingested and the owner is not notified

#### Scenario: Cross-butler enrichment is an MCP proposal

- **WHEN** the Chronicler has a candidate entity fact worth sharing
- **THEN** it is proposed to `relationship` over MCP
- **AND** the Chronicler does not write `entity_facts` directly
