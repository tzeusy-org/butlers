## Why

`2026-05-19-redesign-ingestion-dispatch-console` was archived by hand
(`git mv`, commit `22fcd4e42`) rather than by `openspec archive`, so none of its
44 requirements were ever written into `openspec/specs/`. Nothing noticed for
three months, because every existing check was blind to it:
`openspec validate --changes --strict` validates delta *syntax*, not
application, and `scripts/check_spec_overwrites.py` reads
`openspec/changes/**` only. `scripts/check_archived_requirements_landed.py`
(bu-966by) now sees the gap and has it frozen in the ratchet.

The code shipped. Connector lifecycle actions, per-channel replay safety, the
Prometheus-backed funnel aggregates, the `priority_contacts` table, and
`channel_defaults` are all live and have been in production since May. What is
missing is the baseline coverage, so those five capabilities have no spec a
reviewer can hold an implementation against — and
`add-connector-oauth-scope-surface` already cites
`connector-lifecycle-ceremony` as normative source while pointing at an
archive path.

Restoring the archived deltas wholesale was rejected on bu-1h3hb: it would be a
hand-write into the baseline (the exact edit class no check can see), the text
predates implementation by three months and has never been reconciled, and it
duplicates ground that `dashboard-ingestion-dispatch-console` and
`connector-gmail` already own. This change re-adds only what has no canonical
home, with every requirement rewritten from the shipped code rather than copied
from the stale delta.

## What Changes

### New Capabilities

- `connector-lifecycle-ceremony`: the per-action gate matrix, run-now
  semantics, rotate-token refusal contract, soft-delete semantics, and audit
  emission for `/api/ingestion/connectors/*` lifecycle actions. Converts
  `add-connector-oauth-scope-surface`'s dangling archive citation into a real
  cross-reference.
- `connector-replay-idempotency-policy`: per-channel replay-safety
  classification, the `connector_registry.replay_safe` flag, the bulk-replay
  batch and atomicity contract, and replay audit emission.
- `connector-state-aggregates`: Prometheus as the aggregate source of truth,
  the 60-second TTL cache, the degraded-mode envelope, and the pipeline
  response field shape.
- `ingestion-priority-contacts`: the `public.priority_contacts` data model,
  its REST surface, cascade-delete audit trigger, retention, and
  credential-blindness.

### Modified Capabilities

- `connector-gmail`: `Policy Tier Assignment` gains the `GmailPolicyEvaluator`
  cache contract (15-minute TTL, fail-open on DB error, empty set before first
  successful load) that the archived `ingestion-priority-contacts` delta
  described. That content belongs where the evaluator already lives.
- `dashboard-ingestion-dispatch-console`: `Ingestion Dispatch Route
  Architecture` gains the filter-control URL contract for the sub-routes.
- `ingestion-policy`: gains the `channel_defaults` data model and REST API —
  the one piece of the archived `ingestion-ui-information-architecture` delta
  with no canonical home.

## Out of Scope

- Re-adding `ingestion-ui-information-architecture` as a capability. It is
  largely superseded by `dashboard-ingestion-dispatch-console`, which already
  owns the route hierarchy, roster, detail, and filters ground.
- The archived `GMAIL_KNOWN_CONTACTS_PATH deprecation` requirement. The env
  var, its reader, and the flat-file path are already gone from `src/`
  (`grep -r GMAIL_KNOWN_CONTACTS_PATH src/` → 0 matches); a requirement to
  deprecate something absent is noise.
- The archived `90-day replay history retention` requirement. No 90-day window
  exists anywhere in the shipped path: `public.audit_log`, which backs
  `GET /api/ingestion/events/{event_id}/replays`, is keep-forever with no
  pruner, and the only ingestion retention job is a month-granular partition
  drop over `connectors.filtered_events` with a 12-month default that ships
  disabled and dry-run. Writing the archived text would ratify a window the
  code does not implement; renaming it to a truthful requirement would be a
  content change wearing a rename's clothes, which is exactly the
  check-laundering this change exists to avoid. It stays frozen in the
  ratchet, and a follow-up bead should decide the real retention contract.

- The archived `AttentionStrip dependency declaration` requirement. It requires
  the strip to draw from a shared attention primitive; the shipped
  `frontend/src/components/ingestion/connectors/AttentionStrip.tsx` is
  ingestion-owned and shares nothing with the separate implementation inside
  `SettingsConsolePage.tsx`. Writing it would create fresh drift rather than
  record reality; a follow-up bead should decide extract-or-diverge.
- The archived `MODIFIED` blocks on `connector-base-spec`,
  `connector-replay-queue`, `ingestion-event-registry`, and
  `ingestion-policy`'s `Ingestion rules REST API`, and the remaining
  `ingestion-ui-information-architecture` requirements. They stay frozen in
  `scripts/archived-requirements-baseline.json` under bu-tk618.

## Verification

Every requirement here was verified against shipped code before it was
written; the archived text was treated as a list of topics, not as a source.
Requirement *header names* are preserved verbatim from the archived deltas so
`scripts/check_archived_requirements_landed.py` can match them once this change
is archived — the bodies are rewritten.
