## ADDED Requirements

### Requirement: Ingestion Event Propagation Through Trigger Pipeline

`Spawner.trigger()` SHALL accept an optional `ingestion_event_id`
parameter and pass it unchanged through `_run()` to
`session_create()`. Callers that originate from a switchboard ingest
(routing handlers in particular) SHALL provide this parameter so the
resulting session row joins back to the
`public.ingestion_events.id` that produced it. Internally-triggered
sessions (tick, scheduler, manual trigger) MAY omit it.

#### Scenario: Route handler propagates the ingestion event UUID

- **WHEN** `route_inbox_processing` invokes `_spawner.trigger(...)`
  for a routed message
- **THEN** the call SHALL pass `ingestion_event_id=<route_request_id>`
  (which is the same UUID7 the switchboard ingest writes into
  `public.ingestion_events.id`)
- **AND** the resulting `{schema}.sessions` row SHALL have
  `ingestion_event_id = <route_request_id>`

#### Scenario: Tick / schedule sessions omit ingestion event id

- **WHEN** a scheduler-fired or tick-fired session calls
  `Spawner.trigger(...)`
- **THEN** the call SHALL leave `ingestion_event_id` at its default
  (`None`)
- **AND** the resulting session row SHALL have
  `ingestion_event_id = NULL`

#### Scenario: Spawner trigger signature stable across runtimes

- **WHEN** any runtime adapter (`opencode`, `codex`, `claude_code`)
  is invoked through the spawner
- **THEN** the `ingestion_event_id` value SHALL remain a property of
  the session row only and SHALL NOT be exposed to the runtime
  process via env, prompt prefix, or MCP context
- **AND** runtime adapters SHALL NOT accept this parameter — the
  propagation chain ends at `session_create`
