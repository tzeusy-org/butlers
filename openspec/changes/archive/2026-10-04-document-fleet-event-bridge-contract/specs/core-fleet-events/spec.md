## ADDED Requirements

### Requirement: Fleet event notification envelope
Producer processes SHALL publish UTF-8 JSON on `butlers_fleet_events` with only `type` and `data` in the transport envelope. The RFC 0022 producers SHALL use `session`, `spend`, `notification`, `approval`, `ingestion`, `calendar`, or `chronicles` and carry bounded freshness metadata rather than unbounded user content; future content-bearing signals MUST carry references instead of raising the payload cap.

ID: REQ-core-fleet-events-001
Source: RFC 0022 #Decision; [Observed] src/butlers/fleet_events.py
Scope: v1-mandatory

#### Scenario: A producer sends a freshness envelope
- **WHEN** a producer publishes an event with metadata
- **THEN** the notification contains the event `type` and its `data` object on `butlers_fleet_events`
- **AND** the envelope contains no producer-side timestamp

#### Scenario: A producer has no metadata
- **WHEN** publication omits `data`
- **THEN** the transport sends an empty object as `data`

### Requirement: Bounded non-fatal publication
Publication SHALL return `True` after a successful NOTIFY and `False` after serialization or delivery failure, without raising those failures into business work. It MUST reject encoded payloads exceeding 7800 bytes before attempting NOTIFY, warn about serialization or size problems, and log transient delivery failures at debug level; publication MUST NOT raise or block the caller's real work.

ID: REQ-core-fleet-events-002
Source: RFC 0022 #Trade-offs; [Observed] src/butlers/fleet_events.py
Scope: v1-mandatory

#### Scenario: An event exceeds the transport budget
- **WHEN** its UTF-8 JSON envelope exceeds 7800 bytes
- **THEN** publication returns `False` without sending NOTIFY
- **AND** it logs a warning

#### Scenario: Serialization or delivery fails
- **WHEN** metadata serialization or the caller's pool/connection operation raises an exception
- **THEN** publication returns `False` and logs the failure
- **AND** no publication exception reaches the caller's business logic

### Requirement: Shared canonical database target
Publishers, the dashboard listener, and dashboard pools including shared credentials SHALL resolve the same database target: the decoded required database path of a non-empty `DATABASE_URL`, otherwise `POSTGRES_DB`, otherwise the caller's fallback. A supplied pathless URL MUST fail target resolution rather than silently select a different database.

ID: REQ-core-fleet-events-003
Source: RFC 0022 #Decision; [Observed] src/butlers/db.py; src/butlers/api/fleet_events_bridge.py; src/butlers/api/deps.py
Scope: v1-mandatory

#### Scenario: A URL selects the shared database
- **WHEN** `DATABASE_URL` names a database and `POSTGRES_DB` or roster fallback names differ
- **THEN** publishers, the dedicated listener, and dashboard pools use the decoded URL database path

#### Scenario: A URL lacks a database path
- **WHEN** a non-empty `DATABASE_URL` has no database path
- **THEN** participants reject that target before opening connections or pools

#### Scenario: No URL is supplied
- **WHEN** `DATABASE_URL` is absent or empty
- **THEN** participants use `POSTGRES_DB` when supplied and their fallback otherwise

### Requirement: Dashboard event bus compatibility
The dashboard bridge SHALL deliver valid notification metadata unchanged into the existing fleet event bus, which SHALL stamp `ts` at dashboard arrival and retain its ring buffer, subscriber fan-out, snapshot-on-connect, and `WS /api/events/stream` behavior. Native dashboard events, including `header_delta`, `issue`, and `attention_*`, SHALL continue using that bus without needing the cross-process transport.

ID: REQ-core-fleet-events-004
Source: RFC 0022 #Decision; [Observed] src/butlers/api/fleet_events_bridge.py; src/butlers/api/routers/events.py
Scope: v1-mandatory

#### Scenario: A valid notification reaches the dashboard
- **WHEN** a listener receives an object with a string `type` and object `data`
- **THEN** the dashboard emits the same type and metadata through its real fleet event bus
- **AND** the bus assigns its arrival timestamp and supplies existing snapshot and live WebSocket consumers

#### Scenario: Calendar and Chronicler originate in separate processes
- **WHEN** those producer processes publish while the dashboard listener and WebSocket consumer are connected
- **THEN** their `calendar` and `chronicles` frames reach that consumer through the same stream

### Requirement: Defensive notification parsing
The bridge SHALL ignore other channels and drop malformed JSON, non-object envelopes, and missing or non-string `type` values without terminating the listener. Malformed and foreign-channel notification drops SHALL be logged; missing or non-object `data` SHALL normalize to an empty object.

ID: REQ-core-fleet-events-005
Source: RFC 0022 #Trade-offs; [Observed] src/butlers/api/fleet_events_bridge.py
Scope: v1-mandatory

#### Scenario: A notification cannot be accepted
- **WHEN** the channel differs, the JSON is malformed or non-object, or `type` is missing or non-string
- **THEN** no event enters the dashboard bus, the drop is logged, and the listener remains available

#### Scenario: Metadata is not an object
- **WHEN** an otherwise valid envelope has missing or non-object `data`
- **THEN** the bridge emits its type with an empty metadata object

### Requirement: Resilient listener lifecycle
The dashboard SHALL start the listener as an independently guarded lifespan task and hold a dedicated non-pooled connection with `LISTEN butlers_fleet_events` for its listening lifetime. The task SHALL poll connection health, close/discard failed connections, and retry connection or listening failures after fixed backoff until shutdown cancellation; bridge startup failure MUST degrade cross-process freshness without preventing dashboard startup.

ID: REQ-core-fleet-events-006
Source: RFC 0022 #Decision; [Observed] src/butlers/api/fleet_events_bridge.py; src/butlers/api/app.py
Scope: v1-mandatory

#### Scenario: The listener connects
- **WHEN** dashboard lifespan starts the bridge
- **THEN** it registers the fleet channel on a connection held for the listener rather than borrowed and returned between unrelated callers

#### Scenario: Connection establishment or an active connection fails
- **WHEN** connecting fails or a health poll detects a closed connection
- **THEN** the task retries after backoff and resumes delivery after reconnection

#### Scenario: The bridge cannot start or the dashboard shuts down
- **WHEN** bridge startup raises or dashboard shutdown cancels its task
- **THEN** startup remains available with degraded freshness, or shutdown closes the held listener connection respectively

### Requirement: Freshness signals do not replace durable state
The bridge SHALL remain a best-effort freshness transport without a durable queue, replay, or delivery guarantee. Events sent without a listener, including restart/reconnect gaps, MAY be lost; transport loss MUST leave the underlying business records authoritative and poll reconciliation available, while a WebSocket snapshot covers only events that reached the dashboard bus.

ID: REQ-core-fleet-events-007
Source: RFC 0022 #Trade-offs; [Observed] src/butlers/fleet_events.py; frontend/src/hooks/use-bus-aware-poll-interval.ts
Scope: v1-mandatory

#### Scenario: A live signal is missed
- **WHEN** a notification is published while the dashboard is not listening
- **THEN** this transport provides no later replay or guarantee of delivery
- **AND** durable records and dashboard reconciliation remain the correctness backstop

#### Scenario: The bus becomes unhealthy
- **WHEN** shared bus health is late or down
- **THEN** bus-aware queries select their fallback polling interval
- **AND** healthy bus-aware queries still retain reconciliation polling

### Requirement: Daemon producers use the bridge
Session lifecycle, per-call spend, successful `notify()` delivery, and approval creation producers SHALL publish from their owning daemon's database pool through the bridge. They MUST NOT rely on daemon-local dashboard emitters or retired feature streams, and publication failure MUST NOT invalidate session work, notification delivery, or approval gating.

ID: REQ-core-fleet-events-008
Source: RFC 0022 #Decision; [Observed] src/butlers/core/sessions.py; src/butlers/core/spawner.py; src/butlers/core_tools/_notifications.py; src/butlers/modules/approvals/gate.py; src/butlers/modules/approvals/email_guard.py
Scope: v1-mandatory

#### Scenario: A daemon records lifecycle or business activity
- **WHEN** a session starts or ends, a call records spend, a notification delivers successfully, or an approval is created
- **THEN** its producer publishes the matching `session`, `spend`, `notification`, or `approval` freshness event through the bridge

#### Scenario: Publication fails after daemon work
- **WHEN** the producer's freshness publication fails
- **THEN** the failure does not change the outcome of its session, delivery, or gating work

### Requirement: Switchboard ingestion freshness
A newly accepted Switchboard ingest SHALL publish one bridge-only `ingestion` signal after the `public.ingestion_events` transaction commits. Its metadata SHALL contain only `request_id`, `source_channel`, `triage_decision`, and `triage_target`, never the raw ingest payload; duplicate submissions SHALL emit no new-ingest signal and publication failure MUST NOT undo acceptance.

ID: REQ-core-fleet-events-009
Source: RFC 0022 #Decision; [Observed] roster/switchboard/tools/ingestion/ingest.py
Scope: v1-mandatory

#### Scenario: Switchboard accepts a new ingest
- **WHEN** the ingestion transaction commits a new accepted request
- **THEN** Switchboard publishes `ingestion` with the request identifier, channel, and triage fields only

#### Scenario: Switchboard finds a duplicate
- **WHEN** a submission resolves to an already accepted request
- **THEN** it publishes no new-ingest freshness event

### Requirement: Filtered connector batch freshness
A connector filtered-event buffer SHALL clear its buffer and publish one bridge-only `ingestion` signal with empty `data` after a successful `connectors.filtered_events` batch INSERT. Empty or failed writes SHALL publish nothing, and publication failure MUST NOT cause already written rows to be retried.

ID: REQ-core-fleet-events-010
Source: RFC 0022 #Decision; [Observed] src/butlers/connectors/filtered_event_buffer.py
Scope: v1-mandatory

#### Scenario: A filtered batch is written
- **WHEN** the batch INSERT succeeds and its connection is released
- **THEN** the buffer is cleared and one `ingestion` event with empty metadata is published
- **AND** a publication failure does not reinsert the batch

#### Scenario: No batch was written
- **WHEN** the buffer is empty or its INSERT fails
- **THEN** no freshness event is published

### Requirement: Material Calendar projection freshness
Calendar SHALL publish bridge-only `calendar` signals after durable provider projection writes with a non-empty delta or a user-visible internal scheduler projection change. Provider metadata SHALL contain `kind: provider_projection` and updated/cancelled counts; internal metadata SHALL contain `kind: internal_projection`, without event or source content. Bookkeeping-only or empty sweeps SHALL publish nothing.

ID: REQ-core-fleet-events-011
Source: RFC 0022 #Decision; [Observed] src/butlers/modules/calendar.py
Scope: v1-mandatory

#### Scenario: A provider projection changes visible events
- **WHEN** a non-empty provider delta is projected successfully
- **THEN** Calendar publishes `calendar` with its kind and updated/cancelled aggregate counts

#### Scenario: An internal projection changes visible events
- **WHEN** an internal scheduler sweep produces a user-visible projection change
- **THEN** Calendar publishes `calendar` with `kind: internal_projection`

#### Scenario: Only projection bookkeeping changes
- **WHEN** a sweep has no visible material change
- **THEN** Calendar emits no freshness signal

### Requirement: Material Chronicler projection freshness
Scheduled Chronicler adapters SHALL publish bridge-only `chronicles` signals only after successful material projections, including promotion-only outcomes. Metadata SHALL contain `kind: projection` and aggregate counts (`rows_projected`, `point_events`, `episodes_opened`, `episodes_closed`, `episodes_promoted`), never episode or source content; empty, skipped, or failed runs SHALL emit nothing.

ID: REQ-core-fleet-events-012
Source: RFC 0022 #Decision; [Observed] src/butlers/chronicler/jobs.py
Scope: v1-mandatory

#### Scenario: An adapter reports material projection work
- **WHEN** its successful run projects rows or changes point/episode/promotion outcomes
- **THEN** Chronicler publishes `chronicles` with the projection kind and aggregate counts only

#### Scenario: An adapter produces no successful material work
- **WHEN** its run is empty, skipped, or failed
- **THEN** Chronicler emits no projection freshness event

### Requirement: Projection and ingestion cache reconciliation
The dashboard SHALL invalidate ingestion feed caches by event type even with empty metadata, map `calendar` to normalized Calendar workspace and derived-view caches, and map `chronicles` to Chronicler caches. The unified ingestion feed SHALL retain its 30-second primary poll independently of these live signals.

ID: REQ-core-fleet-events-013
Source: RFC 0022 #Decision; [Observed] frontend/src/hooks/event-cache-registry.ts; frontend/src/hooks/use-ingestion-events.ts
Scope: v1-mandatory

#### Scenario: A freshness event reaches the client registry
- **WHEN** it receives `ingestion`, `calendar`, or `chronicles`
- **THEN** it invalidates the corresponding ingestion, projected Calendar, or Chronicler query families
- **AND** empty `ingestion` metadata still invalidates the merged feed

#### Scenario: The ingestion surface receives no bus events
- **WHEN** the unified feed is active without live signals
- **THEN** its head and active detail/aggregate reads continue reconciling every 30 seconds
