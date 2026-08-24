## Tasks

This change restores spec coverage for behaviour that is already in production.
There is no implementation work: the tasks are verification that each written
requirement matches the shipped code, and that archiving lands the baseline.

### 1. connector-lifecycle-ceremony

- [ ] 1.1 Verified against `src/butlers/api/routers/ingestion_connectors.py` (pause, run-now,
      archive/unarchive, disconnect, rotate-token, reauth handlers),
      `src/butlers/modules/approvals/{park,command_contracts,executor,gate}.py`,
      `roster/switchboard/tools/connector/lifecycle.py`, and
      `roster/switchboard/migrations/{002,012,022}_*.py`.

Acceptance:
- Gate matrix, status codes, and audit action strings match the handlers.
- `deleted_at` soft-delete and the `deleted_at IS NULL` read filter match the
  migration and every default-active query.
- `reauth` still returns 503 naming `connector-oauth-scope-surface`, and no
  ratified spec by that name exists yet.
- Covered by `tests/api/test_connector_lifecycle.py` and
  `tests/api/test_ingestion_connector_lifecycle_phase4b.py`.

### 2. connector-replay-idempotency-policy

- [ ] 2.1 Verified against `src/butlers/core/ingestion_events.py` (`ingestion_events_replay_policy`, the
      replay-policy CTEs, the transition SQL), `src/butlers/api/routers/ingestion_events.py` (bulk
      retry handler), `src/butlers/connectors/filtered_event_buffer.py` (drain loop), and
      `roster/switchboard/migrations/{012,013}_*.py`.

Acceptance:
- Batch cap, 400-not-truncate behaviour, and 409 pre-flight atomicity match.
- `replay_safe` DDL and the Gmail seed match the migrations.
- Covered by `tests/api/test_ingestion_bulk_retry.py`,
  `tests/core/test_ingestion_events.py`,
  `tests/integration/test_ingestion_replay_policy_db.py`.

### 3. connector-state-aggregates

- [ ] 3.1 Verified against `src/butlers/api/routers/ingestion_pipeline.py`,
      `src/butlers/modules/metrics/prometheus.py`, and
      `src/butlers/api/routers/ingestion_connectors.py` (`/cross-summary`).

Acceptance:
- `_CACHE_TTL_SECONDS = 60.0`, window-keyed cache, and degraded envelope match.
- No materialized view or rollup table exists for these aggregates.
- Covered by `tests/api/test_ingestion_pipeline.py`.

### 4. ingestion-priority-contacts

- [ ] 4.1 Verified against `src/butlers/api/routers/priority_contacts.py` and
      `alembic/versions/core/core_{101,129,131}_*.py`.

Acceptance:
- Table is butler-agnostic (core_129) and anchored on `public.entities`
  (core_131); the archived `butler` dimension and `public.contacts` FK are gone.
- Cascade-audit trigger target is `contact_id` alone post-core_129.
- Covered by `tests/api/test_priority_contacts.py`,
  `tests/api/test_priority_contacts_entity_facts.py`,
  `tests/migrations/test_priority_contacts_cascade_audit.py`.

### 5. connector-gmail (MODIFIED)

- [ ] 5.1 Verified against `src/butlers/connectors/gmail_policy.py` (`GmailPolicyEvaluator`,
      `_PRIORITY_CONTACTS_TTL = 900`).

Acceptance:
- The added scenarios do not drop any clause of the baseline
  `Policy Tier Assignment` requirement (`scripts/check_spec_overwrites.py`).

### 6. ingestion-policy (MODIFIED)

- [ ] 6.1 Verified against `src/butlers/api/routers/channel_defaults.py` and
      `alembic/versions/core/core_102_channel_defaults.py`.

Acceptance:
- Table DDL, the 400-on-unknown-channel behaviour, and the 405 DELETE surface
  match the router.

### 7. dashboard-ingestion-dispatch-console (MODIFIED)

- [ ] 7.1 Verified against `frontend/src/router-config.tsx`, `frontend/src/router.tsx`
      (`IngestionTabRedirect`, `ConnectorDetailRedirect`),
      `frontend/src/components/ingestion/TimelineTab.tsx`,
      `frontend/src/components/ingestion/timeline/useEventDrawerState.ts`,
      `frontend/src/components/ingestion/connectors/ConnectorsRoster.tsx`, and
      `frontend/src/hooks/use-ingestion.ts`.

Acceptance:
- The added clauses do not drop any clause of the baseline
  `Ingestion Dispatch Route Architecture` requirement
  (`scripts/check_spec_overwrites.py`).
- The URL-backed filter set is exactly `range`, `q`, `channels`,
  `scopedMinute`, `scopedBucketMinutes`, `event` (plus the read-only `trace`
  deep link); status chips and the active saved view are deliberately recorded
  as *not* URL-backed rather than specified as if they were.
- Roster polling is one summaries request per interval with no per-connector
  detail query.

### 8. Gates

- [ ] 8.1 `uv run openspec validate --strict`
- [ ] 8.2 `python3 scripts/check_spec_overwrites.py`
- [ ] 8.3 `python3 scripts/check_archived_requirements_landed.py`
- [ ] 8.4 Archive-rehearsal in a throwaway copy of `openspec/` proves the four new
  capability specs and the modified blocks land in the baseline, and that the
  matching `check_archived_requirements_landed.py` findings for
  `2026-05-19-redesign-ingestion-dispatch-console` clear.
- **Archiving is blocked on `connector-gmail` until its baseline is repaired.**
  `openspec archive` rebuilds and re-validates the whole target spec, and three
  requirements already in `openspec/specs/connector-gmail/spec.md` —
  `ingest.v1 Field Mapping`, `Aggregated Health Status`, and
  `Environment Variables` — contain no SHALL/MUST, which is a hard `✗`. The
  archiver aborts the entire change on that failure and writes nothing. This
  predates this change (`git diff origin/main -- openspec/specs/` is empty).
  Either land the RFC-2119 repair on those three requirements first, or archive
  with the `connector-gmail` delta held back and applied in a follow-up. A
  rehearsal with that one delta removed archives cleanly and applies 23
  requirements across the other five capabilities.
- [ ] 8.5 On archive, remove the healed entries from
  `scripts/archived-requirements-baseline.json` by hand (never
  `--update-baseline`; the script has no such flag by design).
