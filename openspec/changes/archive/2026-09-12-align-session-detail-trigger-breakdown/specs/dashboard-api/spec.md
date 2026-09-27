## ADDED Requirements

### Requirement: Session Trigger-Breakdown Degradation Is Distinct from Scalar Degradation

When `GET /api/sessions/aggregate` receives `include_trigger_breakdown=true`, the API SHALL preserve the failed source list from the optional `GROUP BY trigger_source` fan-out in
`data.trigger_breakdown_degraded_sources: string[]`. The existing
`meta.sources_degraded` field SHALL continue to represent only failures of the
scalar aggregate fan-out.

#### Scenario: Trigger breakdown loses a pool after a complete scalar aggregate
- **WHEN** the scalar aggregate answers from every queried pool but the opt-in
  trigger-breakdown query drops one or more pools
- **THEN** the response remains HTTP 200 with scalar counts from the complete
  scalar aggregate and trigger buckets from reachable pools
- **AND** `data.trigger_breakdown_degraded_sources` names the dropped
  trigger-breakdown pools
- **AND** `meta.sources_degraded` remains absent or empty

#### Scenario: Scalar and trigger-breakdown failures remain independently attributable
- **WHEN** either or both aggregate fan-outs drop pools
- **THEN** `meta.sources_degraded` names only pools dropped from the scalar
  aggregate fan-out
- **AND** `data.trigger_breakdown_degraded_sources` names only pools dropped
  from the trigger-breakdown fan-out
- **AND** the API SHALL NOT merge one failure list into the other

#### Scenario: Complete or unrequested trigger breakdown has no degraded sources
- **WHEN** the trigger breakdown is healthy or is not requested
- **THEN** `data.trigger_breakdown_degraded_sources` is an empty list
