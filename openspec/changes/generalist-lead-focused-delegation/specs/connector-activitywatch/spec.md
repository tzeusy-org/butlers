## MODIFIED Requirements

### Requirement: Switchboard Registration

ActivityWatch events SHALL NOT be admitted as goals; their value lives in
the durable evidence table and Chronicler projection, not in natural-language
summaries. A seeded global ingestion rule SHALL withhold goal admission for
every ActivityWatch envelope, and no classification or routing step runs.

Source: RFC 0038 D1, D2; RFC 0003 disposition
Scope: v1-mandatory

#### Scenario: Global skip rule

- **WHEN** an `ingest.v1` envelope with `source_channel = "activitywatch"`
  reaches the Switchboard ingestion policy evaluator
- **THEN** a `scope='global'`, `rule_type='source_channel'`, `action='skip'`
  rule short-circuits goal admission
- **AND** the envelope still lands in `public.ingestion_events` for direct
  DB querying
- **AND** no goal is admitted for it
