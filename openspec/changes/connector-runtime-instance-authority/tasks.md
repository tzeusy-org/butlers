# Tasks

## 1. Registry role model

- [x] 1.1 Add `src/butlers/connectors/registry_roles.py` with the three roles,
      the `unclassified` liveness constant, and `normalize_operational_role`
      (unrecognized or missing → `unknown`, never a healthy default).
- [x] 1.2 Add migration `sw_031`: `operational_role` (NOT NULL, defaulted to
      `unknown`, CHECK-constrained) and `parent_endpoint_identity`.
- [x] 1.3 Backfill from persisted evidence — a process identity or any
      heartbeat means `runtime_instance`; a cursor with neither means
      `checkpoint`; anything else stays `unknown`. Run the backfill before the
      CHECK constraint so the migration cannot fail on pre-existing rows.
- [x] 1.4 Attach each backfilled checkpoint to the longest runtime-instance
      identity of the same `connector_type` that it extends by a `:`-delimited
      suffix, matched with `left(...)` rather than `LIKE` so `%`/`_` in an
      identity cannot act as wildcards.
- [x] 1.5 Re-point `public.v_qa_connector_state` at the persisted role,
      replacing `sw_028`'s nullability inference. Restore the `sw_028`
      definition on downgrade, before the columns are dropped.

## 2. Producers write their own role

- [x] 2.1 `connector.heartbeat` claims its row as `runtime_instance` on both
      INSERT and conflict.
- [x] 2.2 `save_cursor` stamps `checkpoint` on INSERT only, and fills in
      `parent_endpoint_identity` without ever clearing a recorded one.
- [x] 2.3 Google Health passes its canonical heartbeat identity as the parent
      of every per-account/per-resource cursor.

## 3. Read paths count runtime instances only

- [x] 3.1 Partition registry rows by persisted role in
      `/api/ingestion/connectors/summaries`; nest checkpoints under their
      parent, keyed per `(connector_type, parent)`.
- [x] 3.2 Return unresolvable checkpoints in `unparented_checkpoints` instead
      of dropping them.
- [x] 3.3 Report `liveness: "unclassified"` for `unknown` rows and count them
      separately from the fleet rollups.
- [x] 3.4 Apply the same authority to `/cross-summary`, the legacy switchboard
      roster, and the archive-candidate sibling test.

## 4. Dashboard

- [x] 4.1 Add the `unclassified` health state to the shared dispatch deriver,
      checked before every other signal, with its own dot/text/verdict mapping.
- [x] 4.2 Nest checkpoint records under their roster row, labelled and
      status-free.
- [x] 4.3 Add the unresolved-owner checkpoint section.
- [x] 4.4 Count fleet-liveness KPIs from runtime instances only, and name the
      unclassified count beneath the band.

## 5. Tests

- [x] 5.1 API: the Google Health parent-plus-subidentity shape, multi-account
      isolation, unclassified state, rollup exclusion, orphaned checkpoints,
      and source failure — `tests/api/test_connector_operational_role.py`.
- [x] 5.2 Migration: columns, CHECK, evidence-based backfill, parent
      attachment, idempotence, view redefinition, downgrade, and both
      producers' write semantics against a real database —
      `tests/config/test_switchboard_connector_operational_role_migration.py`.
- [x] 5.3 Frontend: roster shape, isolation, unclassified rendering, orphaned
      checkpoints, older-response compatibility, and source failure —
      `frontend/src/components/ingestion/connectors/ConnectorsRosterRuntimeAuthority.test.tsx`.
