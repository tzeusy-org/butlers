## MODIFIED Requirements

### Requirement: Finance Butler Contribution
The Finance butler's `daily_briefing_contribution` job SHALL query its domain tables to extract: bills due within 48 hours, spending anomalies (categories whose spend so far this week is above their stored personal baseline band, per currency), and subscription renewals within the current week.

#### Scenario: Bills due soon
- **WHEN** there are bills due within 48 hours of the briefing date
- **THEN** the contribution includes a highlight per bill with priority "high" and category "bills"

#### Scenario: Spending anomaly detected
- **WHEN** a category with spend in the last 7 days has spend so far this owner-local week above its stored personal baseline band for that currency
- **THEN** the contribution includes a highlight with priority "medium" and category "spending" stating the week's spend and the usual range with the weeks it is drawn from

#### Scenario: Insufficient or stale history
- **WHEN** a category's baseline has fewer than 4 weeks of spend, is older than two weeks, or does not exist
- **THEN** the contribution includes no spending highlight for it

#### Scenario: No financial highlights
- **WHEN** no bills are due, no anomalies detected, and no subscription renewals this week
- **THEN** the contribution has `has_updates=false`
