## MODIFIED Requirements

### Requirement: Find Free Slots Tool

The module SHALL register an MCP tool `calendar_find_free_slots` that turns free/busy data into ranked open time slots. It is a read-only availability tool: it proposes slots and never creates, updates, or deletes an event.

#### Scenario: Rank open slots over a search window

- **WHEN** `calendar_find_free_slots` is called with a `duration_minutes`, a `search_start`/`search_end` window, optional `calendar_ids`, and optional structured `constraints`
- **THEN** it queries `get_free_busy` over the window, subtracts the busy windows to obtain free gaps, splits each gap into `duration_minutes`-sized candidate slots, and returns them ranked earliest-first (constraint-matching slots preferred)
- **AND** at most `limit` slots are returned
- **AND** no event is created, updated, or deleted as a side effect

#### Scenario: Slots respect owner scheduling preferences

- **WHEN** `calendar_find_free_slots` runs and owner scheduling-availability preferences are configured
- **THEN** returned slots lie within the owner's allowed meeting hours and days and do not overlap any no-meeting block
- **AND** when no owner preferences row exists, only busy-window subtraction and the search window constrain the results

#### Scenario: Natural-language constraints are pre-parsed into structured form

- **WHEN** a caller wants constraints like "mornings only" or "avoid Fridays"
- **THEN** the caller passes them as structured `constraints` (e.g. part-of-day, avoided weekdays) and the deterministic finder applies them
- **AND** the finder itself performs no LLM call

#### Scenario: Fully busy window returns no slots

- **WHEN** `calendar_find_free_slots` is called and the search window contains no gap long enough for `duration_minutes`
- **THEN** an empty slots list is returned (fail-open, not an error)

#### Scenario: Provider failure returns a structured module error

- **WHEN** the provider free/busy lookup fails with an authentication or request error
- **THEN** the tool returns its structured error dictionary with `status="error"`, an empty `slots` list, the requested `duration_minutes`, and the resolved `calendar_ids`
- **AND** the tool does not create, update, or delete a calendar event
- **AND** any diagnostic error text remains subject to the module's existing credential-redaction contract
