## ADDED Requirements

### Requirement: Memory Overture renders graph-health coverage honestly

`MemoryOverture` SHALL consume `GET /api/memory/stats` metadata
`graph_health` as a read-only coverage observation. It SHALL distinguish
complete coverage from incomplete or unknown coverage without inferring a
healthy graph from missing metrics, an empty pool list, or a partial result.
Existing ordinary pool, catalog-drift, and retention notes SHALL remain
independent.

The Overture SHALL render a calm, explicitly coverage-only completion line for
`coverage='complete'`. It SHALL render a named incomplete or unknown coverage
note for every non-complete state, naming unknown sources when available. It
SHALL not add cleanup, repair, re-enable, drain, delete, owner-authorization,
or graph mutation controls; retrying the same read after an unavailable source
is permitted.

#### Scenario: Complete coverage is visible without a health claim

- **WHEN** `GET /api/memory/stats` returns `meta.graph_health.coverage='complete'`
  with completed pool observations
- **THEN** the Overture SHALL state that graph-health coverage is complete
- **AND** it SHALL not label the graph healthy solely because coverage is
  complete

#### Scenario: Incomplete coverage names unavailable pools

- **WHEN** `GET /api/memory/stats` returns
  `meta.graph_health.coverage='incomplete'` with one or more pool observations
  where `coverage='unknown'`
- **THEN** the Overture SHALL state that graph-health coverage is incomplete
- **AND** it SHALL name each unknown `source_butler`
- **AND** it SHALL not render a complete-coverage or healthy graph claim

#### Scenario: Unknown coverage does not become an empty all-clear

- **WHEN** `GET /api/memory/stats` returns
  `meta.graph_health.coverage='unknown'`
- **THEN** the Overture SHALL state that graph-health coverage is unknown
- **AND** it SHALL not substitute a zero metric, a complete ratio, or a healthy
  graph statement

#### Scenario: Coverage presentation has no repair affordance

- **WHEN** any graph-health coverage state is rendered
- **THEN** the Overture SHALL not offer a cleanup, graph-repair, retention
  mutation, or authorization control
- **AND** a retry control, when present for an unavailable source, SHALL only
  repeat the existing stats read
