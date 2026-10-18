## MODIFIED Requirements

### Requirement: Seasonal Context Injection
The scheduler SHALL query `get_active_seasons()` during task dispatch and inject active seasonal context into the job metadata or, for a prompt-mode task, into the goal input of the obligation admission it submits instead of spawning a session. Seasonal periods are written by model-reachable tools, so each injected period SHALL carry its stamp, and a period written under an exposed assertion SHALL expose the admitted goal. This allows butlers to adjust behavior based on what periods are currently active.

Source: RFC 0038 D2, D5, D6
Scope: v1-mandatory

#### Scenario: Active season injected into prompt dispatch
- **WHEN** `tick()` submits an obligation admission for a due prompt-mode task
- **AND** `get_active_seasons()` returns `[{name: "tax-season", metadata: {context_hint: "Prioritize financial tasks"}}]`
- **THEN** the goal input includes `active_seasons` with the period names, metadata, and each period's stamp
- **AND** a period written under an exposed assertion makes the admitted goal start exposed

#### Scenario: No active seasons
- **WHEN** `tick()` dispatches a task and `get_active_seasons()` returns an empty list
- **THEN** no seasonal context is injected into the dispatch
