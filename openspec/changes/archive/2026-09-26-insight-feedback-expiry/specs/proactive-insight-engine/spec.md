## MODIFIED Requirements

### Requirement: Adaptive Delivery with Graceful Degradation
The system SHALL keep the owner's configured global delivery cap intact while
shaping candidate ordering with per-category engagement weights. A category's
disengagement SHALL never reduce another category's available delivery capacity.
The system SHALL retain total-disengagement auto-off when every delivery has
remained unengaged across the existing fourteen-day safety window.

#### Scenario: Engagement detection
- **WHEN** an insight is delivered
- **THEN** the system SHALL record a row in `public.insight_engagement` with
  `insight_id`, `delivered_at`, `engaged` (BOOLEAN, default FALSE), `category`,
  and `origin_butler`
- **AND** if the OWNER sends any message to any butler within 60 minutes of
  `delivered_at`, the `engaged` field SHALL be set to TRUE
- **AND** ingress from a connector, an automated source, or any non-owner
  (including unresolved/unknown) sender SHALL NOT count toward engagement

#### Scenario: Engagement rate computation
- **WHEN** the delivery cycle ranks eligible candidates
- **THEN** it SHALL derive each category's engagement signal from its last ten
  attributed deliveries
- **AND** a category with no attributed deliveries SHALL retain baseline weight
- **AND** no aggregate engagement rate SHALL reduce the configured global cap

#### Scenario: Budget reduction on low engagement
- **WHEN** a category's engagement rate is at least 0.5
- **THEN** that category's weight SHALL remain at baseline
- **AND** the effective global budget SHALL equal the owner's configured budget

#### Scenario: Moderate disengagement
- **WHEN** a category's engagement rate is at least 0.25 and below 0.5
- **THEN** that category's weight SHALL be reduced to 0.75
- **AND** other categories and the configured global budget SHALL remain unchanged

#### Scenario: Severe disengagement
- **WHEN** a category's engagement rate is below 0.25
- **THEN** that category's weight SHALL be reduced to 0.5
- **AND** other categories and the configured global budget SHALL remain unchanged

#### Scenario: Total disengagement auto-off
- **WHEN** every insight delivered on each of 14 consecutive days remains
  unengaged (at least 1 insight delivered per day)
- **THEN** the system SHALL auto-downgrade verbosity to `off`
- **AND** SHALL deliver a final notification: "I've paused proactive insights
  since you haven't found them useful. You can re-enable them anytime."
- **AND** this final notification SHALL be delivered via direct `notify` (not
  through the insight pipeline)
- **AND** for any day in the 14-day window no longer present in
  `public.insight_engagement` (purged), the day's delivered/engaged totals SHALL
  be read from `public.attention_daily_rollup`

#### Scenario: No automatic increase
- **WHEN** a category's engagement evidence improves after its weight was reduced
- **THEN** only that category's later attributed deliveries or an explicit useful
  verdict MAY restore its baseline weight
- **AND** no other category's weight or the configured global budget SHALL change

## ADDED Requirements

### Requirement: Bounded Explicit Insight Feedback
The broker SHALL expose useful, not-now, and never owner-feedback verbs without
adding another global verbosity control. Feedback attribution SHALL be derived by
the server and the persisted evidence SHALL remain content-blind.

#### Scenario: Useful reverses a family hold
- **WHEN** the owner marks an insight useful after snoozing or muting its family
- **THEN** the family cooldown SHALL be removed and its category weight SHALL be eligible to return to baseline

#### Scenario: Not-now is bounded
- **WHEN** the owner marks an insight not-now
- **THEN** a future `snooze_until` SHALL be required and the family SHALL resume after that instant

#### Scenario: Never remains reversible
- **WHEN** the owner marks an insight never
- **THEN** the family SHALL receive an indefinite cooldown
- **AND** a later useful verdict SHALL reverse it

#### Scenario: Bounded doors share one behavior
- **WHEN** feedback is invoked through Switchboard MCP, REST, delivered-message action metadata, or the dashboard insight row
- **THEN** every door SHALL call the same useful, not-now, or never behavior
- **AND** no door SHALL accept a caller-asserted actor

### Requirement: Per-Category Reversible Attention Shaping
The broker SHALL shape candidate ordering with per-category engagement weights
inside the existing global delivery cap. It SHALL publish a content-blind reason
for every reduced category weight.

#### Scenario: Uniform engagement preserves the prior budget
- **WHEN** every eligible category has the same engagement history
- **THEN** category weights SHALL be equal
- **AND** the effective global budget and candidate count SHALL equal the previous global-budget behavior

#### Scenario: One category does not quiet another
- **WHEN** nine of the last ten Health insights were ignored and another category remained engaged
- **THEN** Health SHALL receive a lower weight without reducing the other category's weight
- **AND** its reason SHALL read `hearing less from Health: 9 of last 10 ignored`

### Requirement: Expired-Unseen Attention Truth
Every pending candidate that expires unseen SHALL produce exactly one attention
ledger row with `outcome=expired` and a closed `blocked_by` reason from `budget`,
`cooldown`, `held_by`, or `dedup`.

#### Scenario: Expiry is visible per origin
- **WHEN** a pending candidate expires before delivery
- **THEN** one content-blind ledger row SHALL reference that candidate
- **AND** `GET /api/attention/ledger/summary` SHALL count it as `expired_unseen` for the originating butler

#### Scenario: Equal priority prefers the perishable candidate
- **WHEN** two eligible candidates have equal weighted priority and only one expires before the next regular cycle
- **THEN** the perishable candidate SHALL rank first
