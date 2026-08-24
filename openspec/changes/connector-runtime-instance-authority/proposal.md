## Why

`public.connector_registry` (in the switchboard schema) has two independent
producers:

- the `connector.heartbeat` MCP tool, which registers an **executable connector
  process** — `roster/switchboard/tools/connector/heartbeat.py`;
- `cursor_store.save_cursor`, which persists a **restart-safe checkpoint
  cursor** — `src/butlers/connectors/cursor_store.py`.

Both upsert on `(connector_type, endpoint_identity)`, and nothing in the schema
recorded which producer a row came from. A connector whose streams advance
independently therefore accumulated registry rows that never heartbeat. Google
Health is the live case: it heartbeats as `google_health:user:<email>` but keys
its cursors as `google_health:user:<email>:<account_uuid>:<resource>`, one per
account **and** per resource.

Because every read path had to infer the distinction, and the ingestion roster
inferred nothing at all, `GET /api/ingestion/connectors/summaries` returned each
cursor as its own connector. With no heartbeat, `derive_liveness(None)` returned
`offline`, so the live console showed `activity`, `sleep_sessions`, `hrv` and
their siblings as separate OFFLINE listening connectors beside the single
genuinely online account — each one dragging fleet KPIs down and taking a slot
in the attention strip.

Migration `sw_028` had already patched the symptom for the QA liveness view by
inferring storage rows from column nullability (`instance_id IS NULL AND
last_heartbeat_at IS NULL AND checkpoint_cursor IS NOT NULL`). That inference
was per-query, unpersisted, and applied to exactly one surface: persistence
shape was still wearing runtime-health authority everywhere else.

## What Changes

- **MODIFIED capability** `connector-base-spec`:
  - `connector_registry` gains a persisted `operational_role` column
    (`runtime_instance | checkpoint | unknown`, `NOT NULL DEFAULT 'unknown'`,
    CHECK-constrained) and a `parent_endpoint_identity` column recording the
    runtime instance a checkpoint belongs to.
  - Each producer writes its own role: the heartbeat tool claims a row as
    `runtime_instance` (a heartbeat is proof an executable process owns the
    identity), `save_cursor` stamps `checkpoint` on INSERT only. Role ownership
    is one-way — a heartbeat promotes, nothing demotes — so a connector that
    checkpoints under its own heartbeat identity is never dropped from the fleet.
  - The role is never inferred from the opaque `endpoint_identity` string.
  - Migration `sw_031` backfills from persisted evidence only, attaches each
    existing cursor to the longest runtime-instance identity it extends by a
    `:`-delimited suffix, and re-points `public.v_qa_connector_state` at the
    persisted column in place of `sw_028`'s inference.

- **MODIFIED capability** `dashboard-ingestion-dispatch-console`:
  - The roster, attention strip, KPI band, and both fleet-liveness rollups
    count executable runtime instances only.
  - Checkpoint records are nested under their parent (`checkpoints[]` on each
    connector), labelled by the stream they track, with no liveness, state, or
    health of their own. Grouping is keyed on
    `(connector_type, parent_endpoint_identity)`, so two accounts of one
    connector type never collect each other's cursors.
  - A checkpoint whose parent cannot be resolved is returned in
    `unparented_checkpoints` rather than dropped.
  - A row whose role is `unknown` reports `liveness: "unclassified"` — a named
    unavailable state. It is never read as active or healthy, and never
    inferred into `offline` from a heartbeat contract that does not exist.

- **NO new tables.** Two additive columns, one CHECK constraint, one view
  redefinition.

## Capabilities

### Modified Capabilities

- `connector-base-spec` — the registry's operational-role model and the
  producer-side write rules.
- `dashboard-ingestion-dispatch-console` — runtime-instance authority for the
  roster, checkpoint nesting, and the unclassified state.

## Impact

- **Code**:
  - `src/butlers/connectors/registry_roles.py` (new) — the shared role
    vocabulary, importable from both `src/butlers/**` and `roster/**`.
  - `roster/switchboard/migrations/031_connector_operational_role.py` (new) —
    columns, CHECK, evidence-based backfill, view redefinition.
  - `roster/switchboard/tools/connector/heartbeat.py` — claims its row as
    `runtime_instance`.
  - `src/butlers/connectors/cursor_store.py` — stamps `checkpoint` on INSERT,
    records the parent, never demotes.
  - `src/butlers/connectors/google_health.py` — passes its canonical heartbeat
    identity as the cursor's parent.
  - `src/butlers/api/routers/ingestion_connectors.py` — partitions registry rows
    by role, nests checkpoints, and counts runtime instances only.
  - `roster/switchboard/api/router.py`, `roster/switchboard/api/models.py` —
    the same authority on the legacy roster and summary endpoints.
  - `frontend/src/api/types.ts`,
    `frontend/src/components/ingestion/connectors/` — the `unclassified` health
    state, nested checkpoints, and the unresolved-owner section.

- **APIs** (all additive):
  - `GET /api/ingestion/connectors/summaries` — each connector gains
    `operational_role` and `checkpoints[]`; the envelope gains
    `unparented_checkpoints[]` and `unclassified_count`. `liveness` gains the
    `unclassified` value.
  - `GET /api/ingestion/connectors/cross-summary` — gains
    `connectors_unclassified`; `total_connectors` now counts runtime instances.
  - `GET /api/switchboard/connectors` and `/connectors/summary` — the same
    role fields and the same runtime-instance-only totals.

- **Database**: `operational_role TEXT NOT NULL DEFAULT 'unknown'` +
  `parent_endpoint_identity TEXT NULL` on `connector_registry`, with a backfill.
  `public.v_qa_connector_state` reads the persisted role.

- **Doctrine alignment**:
  - **Non-Negotiable Rule 7 (transport is connector responsibility)** — the
    role is written by the connector-side producers; the dashboard reads it and
    infers nothing.

## Source References

- Non-Negotiable Rule 7 (transport is a connector responsibility) —
  `about/heart-and-soul/vision.md`
- RFC 0003 (Switchboard routing and ingestion)
- Connector registry contract — `openspec/specs/connector-base-spec/spec.md`
- Roster and fleet-health contract —
  `openspec/specs/dashboard-ingestion-dispatch-console/spec.md`
- Prior partial fix (QA view only, inference-based) —
  `roster/switchboard/migrations/028_qa_connector_state_checkpoint_rows.py`
- Degraded-envelope conventions —
  `docs/api_and_protocols/response-conventions.md`
- Tracked implementation bead — `bu-6jv4m.11`
