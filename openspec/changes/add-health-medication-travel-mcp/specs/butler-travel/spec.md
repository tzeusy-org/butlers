## MODIFIED Requirements

### Requirement: Travel Butler Tool Surface
The travel butler SHALL provide booking, itinerary, document management, and narrowly scoped
cross-butler medication-preparation tools.

#### Scenario: Tool inventory
- **WHEN** a runtime instance is spawned for the travel butler
- **THEN** it has access to: `record_booking`, `update_itinerary`, `list_trips`, `trip_summary`,
  `upcoming_travel`, `add_document`, `health_medication_snapshot`, and calendar tools

### Requirement: Travel Insight Scan Job
The travel butler's `insight-scan` job SHALL evaluate travel domain data and produce insight candidates covering pre-trip preparation, document expiry warnings, and cross-domain coordination hints. All candidates are submitted via the Switchboard's `propose_insight_candidate()` MCP tool; the butler does not write to `public.insight_candidates` directly.

#### Scenario: Insight-scan job handler registration
- **WHEN** the travel butler starts
- **THEN** it SHALL register an `insight-scan` job handler that is invokable by the scheduler's `job` dispatch mode

#### Scenario: Candidate submission via Switchboard MCP
- **WHEN** the `insight-scan` job generates a candidate
- **THEN** it SHALL submit the candidate by calling the Switchboard's `propose_insight_candidate()` MCP tool
- **AND** if the tool returns `{"status": "filtered"}`, the butler SHALL skip remaining candidates (verbosity is off)
- **AND** if the tool returns `{"status": "error"}`, the butler SHALL log the error and continue with remaining candidates

#### Scenario: Pre-trip preparation insights
- **WHEN** the insight-scan job evaluates upcoming trips with status `planned`
- **THEN** it SHALL generate candidates for trips departing within 7 days
- **AND** trips departing within 1 day SHALL have priority 92 (time-critical)
- **AND** trips departing within 3 days SHALL have priority 78
- **AND** trips departing within 7 days SHALL have priority 65
- **AND** the `dedup_key` SHALL be `travel:pre-trip:{trip-id}:{departure-date}`
- **AND** `expires_at` SHALL be the departure date
- **AND** the message SHALL reference the destination and suggest reviewing the pre-trip checklist

#### Scenario: Document expiry insights
- **WHEN** the insight-scan job evaluates travel documents (passports, visas, travel insurance)
- **THEN** it SHALL generate candidates for documents expiring within 90 days
- **AND** documents expiring within 30 days SHALL have priority 85
- **AND** documents expiring within 60 days SHALL have priority 65
- **AND** documents expiring within 90 days SHALL have priority 45
- **AND** the `dedup_key` SHALL be `travel:document-expiry:{document-type}:{expiry-date}`
- **AND** `expires_at` SHALL be 14 days from generation (re-check periodically)
- **AND** `cooldown_days` SHALL be 14 for 90-day warnings, 7 for 60-day, 3 for 30-day

#### Scenario: Medication prep for travel insights
- **WHEN** the insight-scan job evaluates upcoming trips
- **AND** the user has active medications owned by the Health butler
- **THEN** Travel SHALL obtain the active medication snapshot through its
  `health_medication_snapshot` MCP tool, which routes through the Switchboard to Health's
  `medication_travel_snapshot` MCP tool
- **AND** Travel MUST NOT query the Health schema or import Health implementation code
- **AND** it SHALL generate candidates reminding the user to ensure adequate medication supply for the trip duration
- **AND** priority SHALL be 75 for trips within 7 days, 55 for trips within 14 days
- **AND** the `dedup_key` SHALL be `travel:medication-prep:{trip-id}`
- **AND** `expires_at` SHALL be the departure date
- **AND** this insight SHALL only be generated if the trip duration exceeds 3 days

#### Scenario: No insights for past or completed trips
- **WHEN** the insight-scan job evaluates trips
- **THEN** it SHALL exclude trips with status `completed` or `cancelled`
- **AND** it SHALL exclude trips whose departure date is in the past

## ADDED Requirements

### Requirement: Travel Health Medication Snapshot MCP Consumer
The Travel butler SHALL expose a parameterless `health_medication_snapshot` MCP tool that retrieves
the minimum active medication preparation view through the Switchboard. The tool SHALL NOT accept a
caller-selected target butler, tool name, scope, or raw query.

#### Scenario: Authorized request routes through Switchboard and Health MCP
- **WHEN** Travel is allowed the `cross_butler` permission and calls
  `health_medication_snapshot`
- **THEN** it SHALL call the connected Switchboard client's `route` MCP tool with
  `target_butler = "health"`, `tool_name = "medication_travel_snapshot"`, and
  `source_butler = "travel"`
- **AND** it SHALL validate the returned `health.medication-travel.v1` envelope strictly before
  returning it
- **AND** it MUST NOT query the Health schema directly

#### Scenario: Permission revocation returns a typed denial
- **WHEN** Travel's `cross_butler` permission is explicitly revoked
- **THEN** `health_medication_snapshot` SHALL return `status = "error"`, an empty medication list,
  and error code `permission_denied` with `retryable = false`
- **AND** it SHALL NOT call the Switchboard

#### Scenario: Provider unavailability is not an empty success
- **WHEN** the Switchboard client is missing, the route times out, Health is unavailable, or the
  routed MCP call fails
- **THEN** `health_medication_snapshot` SHALL return `status = "error"` and an empty medication list
- **AND** the error SHALL distinguish `switchboard_unavailable` from `health_unavailable`
- **AND** the error SHALL have `retryable = true`

#### Scenario: Malformed provider response fails closed
- **WHEN** Health returns an envelope with an unknown version, missing required field, wrong field
  type, or extra field, or the Switchboard response lacks the serialized `result.data`
  `CallToolResult` shape
- **THEN** Travel SHALL return `status = "error"`, error code `invalid_health_response`, and an empty
  medication list
- **AND** it SHALL NOT pass the malformed or extra data to its caller

#### Scenario: Successful empty response remains successful
- **WHEN** Health returns a valid `status = "ok"` envelope with `medications = []`
- **THEN** Travel SHALL return that successful empty envelope without converting it to an error
