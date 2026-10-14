## MODIFIED Requirements

### Requirement: Travel Butler Identity and Runtime
The travel butler SHALL manage trip lifecycle and booking data with structured container models. It SHALL NOT spawn reasoning sessions or hold a runtime adapter or session pool; the model for any goal invocation that uses its capabilities is resolved per invocation by role and tier.

Source: RFC 0038 D2 and D16
Scope: v1-mandatory

#### Scenario: Identity and port
- **WHEN** the travel butler is running
- **THEN** it operates on port 41106 with description "Travel logistics and itinerary intelligence specialist for flights, hotels, car rentals, and trip planning."
- **AND** it runs no runtime adapter and holds no concurrent-session limit, because it spawns no reasoning session
- **AND** its database schema is `travel` within the consolidated `butlers` database

#### Scenario: Switchboard registration
- **WHEN** the travel butler starts
- **THEN** it registers with the switchboard's service listener in `butlers-control`, at the address its deployment configuration names, with `advertise = true`, `liveness_ttl_s = 300`, and registration contract version range `route.v1` to `route.v1`, which governs registration only now that route dispatch is deleted

#### Scenario: Module profile
- **WHEN** the travel butler starts
- **THEN** it loads modules: `email`, `calendar` (Google provider, suggest conflicts policy), and `memory`

### Requirement: Travel Butler Tool Surface
The travel butler SHALL provide booking, itinerary, document management, and narrowly scoped
cross-butler medication-preparation tools. It SHALL register its booking, itinerary, and document
tools as Travel capabilities with the control plane; its medication-preparation consumer SHALL NOT be
LLM-presentable, because a goal reads Health data only through Health's own capability.

Source: RFC 0038 D2 and D7
Scope: v1-mandatory

#### Scenario: Tool inventory
- **WHEN** the travel butler registers its capabilities with the control plane
- **THEN** it registers: `record_booking`, `update_itinerary`,
  `acknowledge_connection_risk`, `list_trips`, `trip_summary`, `upcoming_travel`,
  `add_document`, and calendar tools
- **AND** `health_medication_snapshot` is not registered as an LLM-presentable capability

### Requirement: Travel Health Medication Snapshot MCP Consumer
The Travel butler SHALL keep a parameterless `health_medication_snapshot` consumer for deterministic
Travel code that retrieves the minimum active medication preparation view from Health's
non-LLM-presentable medication snapshot service tool through the Switchboard service-plane relay
(`REQ-butler-switchboard-008`), which authenticates the calling Travel daemon, not through the
deleted generic `route` tool. The service tool returns the same `health.medication-travel.v1`
envelope as Health's LLM-presentable `medication_travel_snapshot` capability, which the relay never
forwards to. The consumer SHALL NOT be LLM-presentable and SHALL NOT accept a
caller-selected target butler, tool name, scope, or raw query. A goal invocation that needs this view
SHALL call Health's `medication_travel_snapshot` capability under its own grant.

Source: RFC 0038 D7
Scope: v1-mandatory

#### Scenario: Authorized request routes through Switchboard and Health MCP
- **WHEN** Travel is allowed the `cross_butler` permission and deterministic Travel code calls
  `health_medication_snapshot`
- **THEN** it SHALL call Health's non-LLM-presentable medication snapshot service tool through the
  Switchboard service-plane relay, which authenticates the calling Travel daemon rather than trusting
  a caller-supplied `source_butler`
- **AND** it SHALL validate the returned `health.medication-travel.v1` envelope strictly before
  returning it
- **AND** it MUST NOT query the Health schema directly

#### Scenario: Permission revocation returns a typed denial
- **WHEN** Travel's `cross_butler` permission is explicitly revoked
- **THEN** `health_medication_snapshot` SHALL return `status = "error"`, an empty medication list,
  and error code `permission_denied` with `retryable = false`
- **AND** it SHALL NOT call the Switchboard

#### Scenario: Provider unavailability is not an empty success
- **WHEN** the Switchboard client is missing, the service-plane call times out, Health is
  unavailable, or the forwarded call fails
- **THEN** `health_medication_snapshot` SHALL return `status = "error"` and an empty medication list
- **AND** the error SHALL distinguish `switchboard_unavailable` from `health_unavailable`
- **AND** the error SHALL have `retryable = true`

#### Scenario: Malformed provider response fails closed
- **WHEN** Health returns an envelope with an unknown version, missing required field, wrong field
  type, or extra field, or the Switchboard service-plane response lacks the serialized
  `CallToolResult` shape
- **THEN** Travel SHALL return `status = "error"`, error code `invalid_health_response`, and an empty
  medication list
- **AND** it SHALL NOT pass the malformed or extra data to its caller

#### Scenario: Successful empty response remains successful
- **WHEN** Health returns a valid `status = "ok"` envelope with `medications = []`
- **THEN** Travel SHALL return that successful empty envelope without converting it to an error

#### Scenario: A goal reads medication preparation through Health
- **WHEN** a goal invocation needs a traveller's active medication preparation view
- **THEN** it calls Health's `medication_travel_snapshot` capability under its own grant
- **AND** Travel's `health_medication_snapshot` is not listed or callable for that invocation
