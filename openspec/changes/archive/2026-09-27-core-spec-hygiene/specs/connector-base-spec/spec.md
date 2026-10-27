## MODIFIED Requirements

### Requirement: Triage Integration

Connector-side and server-side ingestion rules SHALL gate ingestion and early routing decisions before LLM classification. Connector-scoped rules (`block` action) SHALL be evaluated at the connector. Global rules (all other actions) SHALL be evaluated post-ingest by the Switchboard.

An envelope's `control.pinned_target`, when present, SHALL take precedence over thread-affinity lookup and global ingestion-rule evaluation: the Switchboard SHALL produce a deterministic `route_to` triage decision to that butler without evaluating rules or invoking LLM classification. The pinned target SHALL be validated against the live, routable butler registry (the same candidate set used for LLM-classification routing: registered, `butler`-typed, `eligibility_state = 'active'`). An envelope naming an unknown, non-butler, or non-routable target SHALL be rejected at the ingest boundary (the envelope is not accepted) rather than silently falling through to classification or being misrouted.

#### Scenario: Pinned target routes deterministically
- **WHEN** an envelope is ingested with `control.pinned_target` set to a registered, routable butler name
- **THEN** the Switchboard produces a `route_to` triage decision targeting that butler
- **AND** thread-affinity lookup and global ingestion-rule evaluation are not performed
- **AND** the message is routed without LLM classification

#### Scenario: Unknown pinned target is rejected
- **WHEN** an envelope is ingested with `control.pinned_target` set to a name that is not a registered, routable butler (including a staffer such as the Switchboard itself)
- **THEN** the ingest submission is rejected with a validation error
- **AND** no `message_inbox` or `public.ingestion_events` row is created for the submission

#### Scenario: Absent pinned target preserves existing behavior
- **WHEN** an envelope is ingested without `control.pinned_target` (or with it unset)
- **THEN** routing proceeds through the unpinned pipeline: thread-affinity lookup (email only), then global ingestion-rule evaluation, then LLM classification fallback

#### Scenario: Thread affinity lookup (email only)
- **WHEN** an email message is ingested with a thread_id and no `pinned_target`
- **THEN** Switchboard checks thread affinity BEFORE evaluating global ingestion rules

#### Scenario: Deterministic rule evaluation
- **WHEN** a message passes connector-scoped evaluation and is accepted by the Switchboard, and no `pinned_target` was set
- **THEN** global ingestion rules are evaluated in priority order; the first match determines routing/action

#### Scenario: Ingestion tier classification
- **WHEN** no global ingestion rule matches (pass_through) and no `pinned_target` was set
- **THEN** the message proceeds to LLM classification

### Requirement: Producers write their own operational role

The role SHALL be written from the provenance of the write — which producer
created or claimed the row — and SHALL NOT be derived from the content or shape
of the opaque `endpoint_identity` string.

#### Scenario: A heartbeat claims the row

- **WHEN** the `connector.heartbeat` tool persists a heartbeat for
  `(connector_type, endpoint_identity)`
- **THEN** that row's `operational_role` SHALL be set to `runtime_instance`,
  whether the row is newly registered or already existed

#### Scenario: A checkpoint save declares what kind of row it is creating

`save_cursor` SHALL require its caller to declare the cursor's ownership; the
declaration has no default, so a connector cannot create a row without saying
which kind it is.

- **WHEN** `save_cursor` inserts a row that did not exist, and the caller names
  a `parent_endpoint_identity`
- **THEN** that row's `operational_role` SHALL be `checkpoint`
- **AND** its `parent_endpoint_identity` SHALL be that identity

- **WHEN** `save_cursor` inserts a row that did not exist, and the caller
  declares that the cursor key IS the connector's own runtime identity
- **THEN** that row's `operational_role` SHALL be `unknown` — unclaimed until a
  heartbeat proves a process owns it
- **AND** it SHALL NOT be `checkpoint`, because it is not storage state
  belonging to a parent

- **AND** `save_cursor` SHALL NOT write `runtime_instance` in either case:
  persisting a cursor is not evidence that a process is running

A row created by `save_cursor` therefore SHALL NOT be a `checkpoint` with a NULL
`parent_endpoint_identity`. A NULL parent on a `checkpoint` row means the row is a
legacy orphan, and never that its writer omitted a value.

#### Scenario: A checkpoint save never demotes a runtime instance

- **WHEN** `save_cursor` writes to a row that already exists
- **THEN** the row's `operational_role` SHALL be left unchanged
- **AND** a previously recorded `parent_endpoint_identity` SHALL NOT be cleared

Most connectors checkpoint under the same identity they heartbeat with. Were the
conflict branch to re-stamp the role, a live connector would demote itself out of
the fleet roster on its next cursor save. Role ownership is therefore one-way: a
heartbeat promotes, and nothing demotes.

#### Scenario: A connector with multi-dimensional cursor keys names its parent

- **WHEN** a connector persists cursors under a key that carries dimensions
  beyond its heartbeat identity — for example one cursor per account and per
  resource
- **THEN** it SHALL pass its canonical heartbeat identity as the cursor's
  `parent_endpoint_identity`

#### Scenario: A heartbeat written by SQL claims the role like any other

- **WHEN** a connector keeps a registry row's `last_heartbeat_at` current by
  writing the column directly rather than through the `connector.heartbeat` tool
- **THEN** that write SHALL also set `operational_role` to `runtime_instance`

Writing a heartbeat is what claims a row, regardless of which code path writes
it. A producer that refreshes liveness but leaves the role alone strands its own
identities in whatever state something else happened to create them in.
