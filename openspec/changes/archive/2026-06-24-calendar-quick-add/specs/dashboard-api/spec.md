## ADDED Requirements

### Requirement: Calendar Quick-Add Parse Endpoint

`src/butlers/api/routers/calendar_workspace.py` SHALL expose a parse-only
endpoint `POST /api/calendar/workspace/parse-quick-add` that turns a
natural-language string into a **draft** calendar event for confirmation. The
endpoint SHALL perform no provider or projection write and SHALL NOT create a
calendar event. Event creation continues to flow exclusively through the existing
`POST /api/calendar/workspace/user-events` create path (the `calendar_create_event`
MCP tool) with a `request_id`; the parse-quick-add response is advisory only.

#### Scenario: Natural-language string parsed into a draft event

- **WHEN** `POST /api/calendar/workspace/parse-quick-add` is called with a
  free-text `text` (e.g. `"lunch with Sarah Fri 1pm at Tartine"`) and an optional
  display `timezone`
- **THEN** the text is parsed by an LLM resolved via `resolve_model(pool,
  butler_name, Complexity.CHEAP)` (the simple/cheap complexity tier — one cheap
  parse per submit)
- **AND** the response has HTTP 200 with `parse_available=true` and a `draft`
  object containing the proposed `title`, `start_at`, `end_at`, and optional
  `location` and `description`
- **AND** no Google event is created and no projection row is written (the parse
  is read-only)

#### Scenario: Draft is confirmed via the existing create path

- **WHEN** the user accepts (and optionally edits) the returned `draft`
- **THEN** confirmation is submitted to the existing
  `POST /api/calendar/workspace/user-events` endpoint with `action="create"` and a
  `request_id`
- **AND** no separate confirm/write endpoint is introduced — the structured create
  path and its `request_id` idempotency are reused unchanged

#### Scenario: LLM unavailable returns a degraded parse with no fabricated event

- **WHEN** `resolve_model(pool, butler_name, Complexity.CHEAP)` returns `None`
  (no enabled model qualifies in any tier) or the LLM parse otherwise cannot be
  produced
- **THEN** the response has HTTP 200 with `parse_available=false` and a
  human-readable `reason`
- **AND** the response contains no `draft` object (the field is absent or null)
- **AND** the endpoint does not fabricate an event or fall back to a heuristic
  guess
- **BECAUSE** silently materializing a guessed event on a single-owner calendar
  would risk writing an unintended event on confirm

#### Scenario: Empty or unparseable input is rejected without a write

- **WHEN** the endpoint is called with empty/blank `text`, or the LLM returns a
  response that cannot be interpreted as a single event draft
- **THEN** the response indicates the input could not be parsed
  (`parse_available=false` with a `reason`, or a 422 validation error for blank
  input) and contains no `draft`
- **AND** no provider or projection write occurs
