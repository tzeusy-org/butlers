## MODIFIED Requirements

### Requirement: Category Taxonomy Mapping

The "where the time went" surface SHALL render the Activity lane taxonomy
(`sleep`, `exercise`, `work`, `butler_ops`, `play`, `social`, `travel`, `eat`,
`rest`) rather than source-shaped categories. The pie chart SHALL be removed;
lane time is presented via the Day Ribbon and balance rings. No "calendar" lane
is rendered.

The `work` lane SHALL represent the OWNER's occupation only — the `occupation`
source category, which folds the occupation-block inference plus the owner's
direct desk signals (`chronicler.focus_inferred`, `chronicler.reading_inferred`,
`activitywatch.window`). The butlers' own LLM sessions (`core.sessions`,
categories `conversations`/`tasks`) SHALL be a **separate** `butler_ops` lane,
never counted toward `work` and never a sub-series inside it: the
owner's workday and the butlers' cron work are distinct signals and must be
independently visible and toggleable.

#### Scenario: Lanes rendered, pie removed

- **WHEN** the chronicles page renders the time surface for a day
- **THEN** Activity lanes are shown via the Day Ribbon and balance rings
- **AND** no pie chart and no "calendar" lane are rendered

#### Scenario: Owner occupation and butler ops are distinct lanes

- **WHEN** a day has both owner-occupation episodes (e.g.
  `chronicler.occupation_inferred`/`activitywatch.window`) and butler LLM-session
  episodes (`core.sessions`)
- **THEN** the occupation time counts toward the `work` lane and the
  butler-session time counts toward the `butler_ops` lane
- **AND** no butler-session time is counted toward `work`, and no owner
  occupation time is counted toward `butler_ops`

### Requirement: Page Telemetry

Backend handlers serving the page SHALL emit OTel spans for its aggregate and source-state endpoints so that operational health is observable without log scraping. Client-side telemetry is not required.

#### Scenario: Backend spans

- **WHEN** an aggregate or source-state endpoint executes
- **THEN** an OTel span SHALL be emitted with the name pattern
  `chronicler.aggregate.by_category`,
  `chronicler.aggregate.by_day`,
  `chronicler.aggregate.day_close`, or `chronicler.source_state`
- **AND** the span SHALL record query latency and the resulting bucket
  count (or, for source-state and day-close, the row / cache state)
