## ADDED Requirements

### Requirement: Direct Producer Outcome Attribution
The dashboard audit log SHALL preserve an explicit, producer-meaningful
`result` for every current direct production `audit_router.append` writer. The
generic `audit_router.append` compatibility contract SHALL continue to allow
callers outside that direct-producer set to omit `result`.

#### Scenario: Direct producer records its observed outcome
- **WHEN** a direct production writer appends an audit row
- **THEN** it passes an explicit `result` that describes the producer's
  observed outcome, such as `success`, `detected`, `escalated`, or `delivered`
- **AND** the choice reflects the event boundary rather than a generic fallback
- **AND** no historical audit row is rewritten to infer that outcome.

#### Scenario: Model breaker notification records confirmed delivery
- **WHEN** the model-breaker open notification is confirmed delivered to the
  owner
- **THEN** its `model_breaker_open_notified` audit row has
  `result = "delivered"`
- **AND** suppressed, deferred, or failed delivery paths retain their existing
  behavior and do not manufacture a delivered audit marker.

#### Scenario: Future direct writer cannot omit outcome attribution
- **WHEN** a new direct production `audit_router.append` call omits the
  `result` keyword
- **THEN** focused source-level regression coverage fails and identifies the
  writer's source location
- **AND** generic router callers outside the direct-producer scan remain
  compatible with an omitted result.
