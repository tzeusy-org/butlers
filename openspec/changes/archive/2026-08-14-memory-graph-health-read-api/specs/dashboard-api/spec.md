## ADDED Requirements

### Requirement: Memory stats expose typed graph-health coverage

`GET /api/memory/stats` SHALL add an additive `meta.graph_health` object with
this exact JSON shape:

```json
{
  "coverage": "complete | incomplete | unknown",
  "pools": [
    {
      "source_butler": "string",
      "source_schema": "string | null",
      "coverage": "complete | unknown",
      "reapable_expired_episodes": "integer | null",
      "retention_eligible_episodes": "integer | null",
      "reapable_expired_ratio": "number | null"
    }
  ]
}
```

The endpoint SHALL continue to be read-only and use its existing authorization
scope. Existing `data.expired_retained_episodes`,
`data.retention_eligible_episodes`, `data.expired_retained_ratio`,
`meta.retention_status`, `meta.retention_sources`, and
`meta.retention_pools_failed` SHALL retain their existing names, values, and
consumer semantics.

`meta.graph_health.coverage` SHALL be `complete` only when at least one
relevant pool completed and none failed, `incomplete` when both complete and
unknown pool observations exist, and `unknown` when no relevant pool completed.
Completed pool metric integers SHALL be non-null; their ratio SHALL be null
only for a zero denominator. Unknown pool metric values SHALL all be null.

#### Scenario: Graph-health metadata is additive to existing stats consumers

- **WHEN** a client calls `GET /api/memory/stats` and ignores
  `meta.graph_health`
- **THEN** it SHALL observe the established stats data and retention metadata
  shape and semantics unchanged
- **AND** the request SHALL not create, update, delete, schedule, or repair
  any memory state

#### Scenario: Partial fan-out remains explicit per pool

- **WHEN** a graph-health source query fails while another relevant source
  completes
- **THEN** `meta.graph_health.coverage` SHALL be `incomplete`
- **AND** the completed pool SHALL retain non-null metrics
- **AND** the failed pool SHALL appear in `meta.graph_health.pools` with
  `coverage="unknown"` and null metrics
- **AND** the existing retention failure metadata SHALL remain independently
  available to its current consumers

#### Scenario: Empty completed source set is unknown

- **WHEN** no relevant memory source completes the graph-health query
- **THEN** `meta.graph_health.coverage` SHALL be `unknown`
- **AND** the endpoint SHALL not emit a zero ratio or healthy coverage fallback

#### Scenario: Graph-health does not change memory authorization

- **WHEN** an authorized caller reads `GET /api/memory/stats`
- **THEN** graph-health metadata SHALL be calculated within the endpoint's
  existing read path and authorization scope
- **AND** no new endpoint, mutation permission, or cross-schema write
  capability SHALL be introduced
