## MODIFIED Requirements

### Requirement: Relationship overdue surfaces expose unmeasurable cadence honestly

Relationship dashboard surfaces MUST distinguish a complete set of measurable contacts from a
result in which stale-contact evaluation is unavailable. Unmeasurable contacts MUST NOT appear as
overdue, count toward overdue totals, or populate attention rails, and their suppression MUST NOT
be rendered as a complete cadence all-clear.

An entity PulseStrip cadence tile MUST bind its label, rolling query window, count, pagination
completeness, and error state as one evidence unit. Its cadence reader MUST echo the exact window
used for the observation and explicitly distinguish complete evidence from a bounded or paginated
subset. The tile MUST render "Quiet" only for a successful, complete, matching-window observation
with zero interactions. Incomplete, mismatched-window, or unavailable evidence MUST render a typed
attention state and MUST NOT render "Quiet" or an exact interaction count.

ID: REQ-dashboard-domain-pages-049
Source: relationship-stale-contact-producer-mapping design §6; heart-and-soul/vision.md;
`keep-relationship-cadence-evidence-honest` design
Scope: v1-mandatory

#### Scenario: Relationship Contacts tab does not turn suppression into calm

- **WHEN** the Relationship Contacts tab's overdue source includes an unmeasurable contact
- **THEN** that contact MUST NOT appear in the overdue list or overdue KPI
- **AND** the tab MUST identify cadence instrumentation or provenance as unavailable
- **AND** it MUST NOT render "Cadence all clear" or equivalent complete healthy copy

#### Scenario: Plex attention rail contains only measurable overdue contacts

- **WHEN** the Plex evaluates its "Worth attention" rail with one or more unmeasurable contacts
- **THEN** those contacts MUST NOT appear as overdue attention items
- **AND** the rail MUST expose incomplete cadence availability rather than a complete all-clear

#### Scenario: Healthy elapsed source keeps the existing overdue presentation

- **WHEN** a contact has exactly one healthy mapped producer and its existing effective cadence has
  elapsed
- **THEN** the existing overdue KPI, list, and attention-rail behavior MAY render that contact
- **AND** this source mapping MUST NOT change cadence, priority, ordering, or outreach copy

#### Scenario: Complete cadence evidence may render Quiet

- **WHEN** the cadence reader completes the requested rolling window with zero interactions and no
  additional page
- **THEN** the PulseStrip cadence label MUST name that same window
- **AND** the tile MAY render "Quiet"

#### Scenario: Paginated cadence evidence is attention, not calm

- **WHEN** the bounded cadence read reports incomplete evidence or an additional page
- **THEN** the PulseStrip MUST render a typed incomplete-attention state
- **AND** it MUST NOT render "Quiet" or present the observed subset as an exact count

#### Scenario: Cadence read failure is attention, not calm

- **WHEN** the cadence read fails
- **THEN** the PulseStrip MUST render a typed unavailable-attention state
- **AND** it MUST NOT render "Quiet"

#### Scenario: Window refresh recomputes label and value together

- **WHEN** the active cadence window changes
- **THEN** the cadence query MUST be re-keyed for that window
- **AND** the label and value MUST be derived only from evidence echoing the same window
- **AND** stale or mismatched-window evidence MUST NOT render "Quiet" or an exact count
