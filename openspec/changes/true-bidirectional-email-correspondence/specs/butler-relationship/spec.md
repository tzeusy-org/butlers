## ADDED Requirements

### Requirement: Freshness-aware aggregate correspondence enrichment

Relationship SHALL use only the bounded correspondence aggregate from a new,
separate deterministic known-entity correspondence enrichment job.  It SHALL
pass a bounded set of already-resolved entity IDs and consume only the fixed
aggregate result.  It SHALL not select Messenger tables, enumerate ledger
records, query a provider, or derive a positive bidirectional claim from
`public.ingestion_events` alone.  The existing
`run_email_identity_enrichment` unresolved-sender discovery/proposal job SHALL
remain inbound-only and SHALL not be relabeled or converted into correspondence
proof.

#### Scenario: Relationship consumes a bounded aggregate

- **WHEN** the separate deterministic known-entity correspondence enrichment job
  evaluates known entities
- **THEN** it calls the authorized aggregate with a bounded entity-ID batch
- **AND** it receives no peer address, account, provider reference, raw event,
  raw ledger row, or content
- **AND** it uses only the returned count, timestamps, freshness, and
  bidirectional tri-state

#### Scenario: Unresolved-sender discovery stays separate

- **WHEN** `run_email_identity_enrichment` finds an unresolved recurring sender
- **THEN** it continues to follow its existing proposal/approval contract
- **AND** it does not call the correspondence aggregate with an unresolved
  address or use inbound recurrence as proof of bidirectionality

#### Scenario: A stale aggregate does not create a fact or claim

- **WHEN** an aggregate row has `freshness='stale'` or `freshness='unknown'`
  and a null bidirectional field
- **THEN** Relationship does not assert a bidirectional-correspondence fact or
  present a negative correspondence conclusion
- **AND** it preserves the distinction between unavailable coverage and absent
  evidence

#### Scenario: Alias or incomplete account coverage cannot become a negative claim

- **WHEN** the aggregate returns `freshness='unknown'` and a null bidirectional
  field because the entity has no active literal `has-email` peer, has any active
  peer-alias authority absent a positive result, or lacks a complete
  account-universe continuity interval and all-member coverage for the requested
  window
- **THEN** Relationship does not present or materialize a `false` correspondence
  conclusion
- **AND** it does not ask Messenger for raw peer data to make the entity
  resolvable

#### Scenario: Fresh confirmed evidence can inform the existing heuristic

- **WHEN** an aggregate reports fresh bidirectional correspondence as `true`
  for an entity
- **THEN** the enrichment workflow may use that bounded result as a documented
  structured signal subject to its existing approval/provenance rules
- **AND** it does not materialize raw correspondence metadata into a
  Relationship fact or log
