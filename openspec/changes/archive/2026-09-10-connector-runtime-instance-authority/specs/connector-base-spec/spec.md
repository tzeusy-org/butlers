# Connector Base Spec — Operational Role Delta

This delta extends `connector-base-spec` with a persisted operational role on
`connector_registry`. It changes no existing column, and no existing connector
lifecycle behavior: it records which producer owns a registry row so that no
read path has to infer runtime authority from persistence shape.

## ADDED Requirements

### Requirement: Persisted operational role on connector_registry

`connector_registry` SHALL record each row's operational role explicitly, in a
column, rather than leaving it to be inferred by readers.

The role SHALL be one of:

- `runtime_instance` — an executable connector process. The only role that
  carries runtime-health authority.
- `checkpoint` — persisted cursor state for one stream of a parent runtime
  instance. It has no process, therefore no liveness and no health.
- `unknown` — the role has not been established.

A `checkpoint` row SHALL additionally record `parent_endpoint_identity`: the
`endpoint_identity` of the runtime instance it belongs to, within the same
`connector_type`.

#### Scenario: Role column present and constrained

- **WHEN** `connector_registry` is at the most recent migration head
- **THEN** the table SHALL include `operational_role`, `NOT NULL`, defaulting to
  `unknown`
- **AND** the table SHALL include a nullable `parent_endpoint_identity`
- **AND** a value outside `runtime_instance | checkpoint | unknown` SHALL be
  rejected by a CHECK constraint
- **AND** every existing column SHALL be unchanged in type, default, and
  constraint

#### Scenario: A new row is unclassified, not live

- **WHEN** a row is inserted without an explicit `operational_role`
- **THEN** its role SHALL be `unknown`
- **AND** it SHALL NOT be counted as a runtime instance by any read path

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
`parent_endpoint_identity`. A NULL parent on a `checkpoint` row means the row was
orphaned before this rule existed, and never that its writer omitted a value.

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

### Requirement: Backfill classifies existing rows from persisted evidence

The migration that introduces the role SHALL classify pre-existing rows from
evidence already stored on them, deterministically and idempotently.

#### Scenario: Evidence of a process means runtime instance

- **WHEN** an existing row carries a process identity or any heartbeat
  timestamp
- **THEN** the backfill SHALL classify it `runtime_instance`

Both facts can only be written by the heartbeat producer.

#### Scenario: A cursor with no process is storage

- **WHEN** an existing row has no process identity, no heartbeat, and a
  persisted cursor
- **THEN** the backfill SHALL classify it `checkpoint`

#### Scenario: No evidence stays unknown

- **WHEN** an existing row has no process identity, no heartbeat, and no cursor
- **THEN** it SHALL remain `unknown`
- **AND** the backfill SHALL NOT guess a role for it

#### Scenario: Parent attachment reads the registry's own runtime rows

- **WHEN** the backfill attaches a checkpoint to a parent
- **THEN** it SHALL select the longest `runtime_instance` identity of the same
  `connector_type` that the checkpoint's identity extends by a `:`-delimited
  suffix
- **AND** a checkpoint with no such runtime instance SHALL be left with a NULL
  parent rather than attached to an approximate one

This is connector-agnostic: it matches against identities the registry already
holds instead of pattern-matching one connector's key shape.

## Source References

- Non-Negotiable Rule 7 (transport is a connector responsibility) —
  `about/heart-and-soul/vision.md`
- RFC 0003 (Switchboard routing and ingestion)
- Prior inference-based partial fix —
  `roster/switchboard/migrations/028_qa_connector_state_checkpoint_rows.py`
- Tracked implementation bead — `bu-6jv4m.11`
