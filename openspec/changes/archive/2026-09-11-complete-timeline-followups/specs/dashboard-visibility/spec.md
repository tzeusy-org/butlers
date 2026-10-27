## ADDED Requirements

### Requirement: Timeline minute density and historical interval selection
The dashboard SHALL display server-aggregated per-minute event density and SHALL load the complete selected minute through interval-filtered pagination, independently of which rows were previously loaded.

ID: REQ-dashboard-visibility-001
Source: bu-ddo0n; proposed complete-timeline-followups/design.md Density and interval contract; dashboard-visibility Unified Timeline
Scope: v1-mandatory

#### Scenario: Density includes events beyond the loaded page
- **WHEN** more than one list page of matching events exists in a selected interval
- **THEN** GET /api/timeline/histogram returns counts for every matching event in each UTC minute, using the same source/type/butler/trace predicates as the list
- **AND** each count represents raw events before display grouping, labelled All matching events

#### Scenario: Bounded and stable interval semantics
- **WHEN** an interval is supplied to the histogram or timeline list
- **THEN** both since and until must be timezone-aware whole-minute bounds, strictly ordered and no more than 24 hours apart, otherwise the API returns 422
- **AND** results include events at since and exclude events at until
- **AND** list cursor pagination preserves the interval without losing events sharing a timestamp
- **AND** a list request without either bound retains its existing behavior

#### Scenario: Select and restore an unloaded minute
- **WHEN** the owner selects a histogram minute containing events not in the currently loaded page
- **THEN** one atomic URL update records the resolved chart interval and selected minute, including when the chart hour was previously implicit and the list fetches that minute from the server with a reset cursor
- **AND** back, forward and reload after an hour rollover restore the same explicit interval and filters
- **AND** clearing the selection or invoking Jump to latest restores live stream behavior

#### Scenario: Invalid URL scope is visible
- **WHEN** chart bounds are malformed or a selected minute is unpaired, unaligned, longer than one minute or outside its chart interval
- **THEN** the page shows an invalid-interval message and Clear interval action
- **AND** it does not silently replace the requested interval with an unfiltered request

#### Scenario: Partial density is not a quiet fleet
- **WHEN** a selected event source fails
- **THEN** healthy-source counts remain available with named degraded sources and butlers, explicit expected_sources and healthy_sources counts, and availability complete, partial or unavailable
- **AND** complete zero counts are shown only when all selected sources are healthy
- **AND** all-source failure or an absent sole selected notifications pool renders unavailable with Retry rather than an empty histogram

#### Scenario: Availability distinguishes partial fan-out and absent configuration
- **WHEN** the selected session scope contains two butlers and only one answers
- **THEN** expected_sources is 2, healthy_sources is 1 and availability is partial

#### Scenario: Selected sources are unavailable
- **WHEN** neither selected session butler answers, or notifications alone are selected with no configured Switchboard pool
- **THEN** availability is unavailable with zero healthy sources and a positive expected source count

#### Scenario: Healthy selected sources have no events
- **WHEN** every selected source answers with zero events
- **THEN** availability is complete and zero counts are trustworthy for that selection

#### Scenario: Filters select no recognized sources
- **WHEN** the event-type selection names no recognized source family
- **THEN** expected_sources and healthy_sources are zero and the UI says No matching event sources rather than fleet all-clear

#### Scenario: Accessible and stable density interaction
- **WHEN** the owner operates the density control by keyboard or changes filters rapidly
- **THEN** arrow keys and Enter or Space select a minute with accessible time/count labels and without requiring one Tab per bucket
- **AND** stale requests cannot replace the current selection
- **AND** live arrivals do not reset historical selection or steal focus

### Requirement: Timeline bounded recent failures strip
The dashboard SHALL show a collapsible strip above the timeline listing recent records created in the last 24 hours that are currently marked failed, with server-derived counts independent of loaded timeline rows and no claim of unresolved work.

ID: REQ-dashboard-visibility-002
Source: bu-ddo0n; proposed complete-timeline-followups/design.md Recent-failure strip
Scope: v1-mandatory

#### Scenario: Counts and rows reflect canonical failures
- **WHEN** the strip loads for selected butlers and trace
- **THEN** it counts sessions whose success is false and notification attempts whose current status is failed in one server-chosen last-24h interval
- **AND** it lists at most five newest matching identifiers in stable order, displays separate source counts and total, and states truncation
- **AND** chart time/type filters do not change this explicitly labelled 24h scope
- **AND** its response exposes only identifiers, kind, butler, timestamps, aggregate counts and degradation metadata

#### Scenario: Acknowledgement and retry claim are not recovery evidence
- **WHEN** acknowledgement or retry claiming changes a notification status from failed to read before any successful delivery
- **THEN** the next strip read excludes that record and updates its count
- **AND** neither the strip nor its empty state claims historical failure absence or successful recovery
- **AND** labels describe records currently marked failed that were created in the last 24 hours

#### Scenario: Failure inspection preserves the target
- **WHEN** the owner activates a failed-run or failed-delivery-attempt row
- **THEN** a real link opens its session detail or timeline event drawer by persisted identifier
- **AND** notification navigation carries butler/trace scope and clears historical interval selection so an off-page event remains reachable
- **AND** inspection does not acknowledge, resolve or retry anything

#### Scenario: Collapse and unavailable state remain honest
- **WHEN** the owner collapses the strip
- **THEN** counts and source degradation remain visible and the disclosure exposes its expanded state accessibly
- **AND** remounting defaults to expanded without storing a new preference

#### Scenario: Failure strip sources are unavailable
- **WHEN** any source is unavailable
- **THEN** the strip marks partial or unavailable data using explicit expected/healthy source counts and availability, offers Retry and never claims all-clear

#### Scenario: Healthy failure sources have no matching records
- **WHEN** healthy sources return no failures
- **THEN** it states that no matching records are currently marked failed within the creation window

### Requirement: Timeline keyboard row traversal and existing-view actions
The dashboard SHALL support j/k traversal of rendered timeline disclosure controls and expose existing view presets through the page action registry while preserving shell search and current timeline shortcuts.

ID: REQ-dashboard-visibility-003
Source: bu-ddo0n; proposed complete-timeline-followups/design.md Keyboard flow; dashboard-shell Keyboard Shortcuts
Scope: v1-mandatory

#### Scenario: Real focus and one activation
- **WHEN** the owner presses j or k outside editable fields and modal contexts
- **THEN** actual DOM focus moves forward or backward through rendered primary row disclosures, entering at the first or last row and clamping at the ends
- **AND** Enter activates the focused disclosure exactly once through its native button behavior
- **AND** traversal does not implicitly fetch another page

#### Scenario: Refetch preserves focus and input ownership
- **WHEN** rows refresh while a disclosure is focused
- **THEN** the same row retains focus if present, otherwise the nearest surviving row at the prior index receives focus
- **AND** typing, IME composition and modal keyboard handling are not intercepted by page traversal

#### Scenario: Existing shortcuts and preset semantics remain intact
- **WHEN** the owner uses slash, Cmd or Ctrl plus K, r, or n
- **THEN** global command search, refresh and conditional Jump to latest retain their existing owners and behavior
- **AND** Escape from global search returns focus under the existing shell modal contract

#### Scenario: Palette-only presets preserve built-in view selection
- **WHEN** the owner selects All, Errors only or Notifications from page actions
- **THEN** the existing built-in view selection path updates the URL and data, with presets available as palette-only commands and actual keybindings discoverable in shortcut help
