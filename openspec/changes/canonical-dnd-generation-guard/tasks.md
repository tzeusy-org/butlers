## 1. Guarded public-state migration

- [x] 1.1 Add the trusted cluster-superuser bootstrap source in
  `scripts/init-db.sql`: fixed no-argument installer/finalizer operations own
  the creation and final handoff of the singleton DND guard, replay audit,
  private definer, forced RLS policies, and dedicated NOLOGIN owner. A separate
  privileged rollback may remove only an unused generation-0 boundary while
  preserving `public.user_context` rows and restoring the known pre-guard
  handoff; it must reject untrusted pre-existing authority objects rather than
  adopting them.
- [x] 1.2 Add a guarded core migration that only catalog-validates the trusted
  finalized interface, invokes the fixed installer, or delegates a bounded
  superuser rollback to the trusted bootstrap routine. It must not create,
  re-own, repair, or otherwise acquire authority over DND boundary objects;
  source it with the durable requested/effective expiry and versioned
  privacy-preserving semantic fingerprint fields, seeded at generation `0`
  without rewriting existing `public.user_context` rows.
- [x] 1.3 Enforce least privilege with a dedicated NOLOGIN owner for
  `public.user_context`, the guard, audit, policies, and private definer;
  `ENABLE` plus `FORCE ROW LEVEL SECURITY`; a `SECURITY INVOKER` active-role
  gateway before the pinned private definer; `PUBLIC`/migration/direct DML and
  cross-role revokes; and a private-definer active-`SET ROLE` recheck. Existing
  authorized non-DND writes retain their application-level path, and no
  peer-schema access is added.
- [ ] 1.4 Add source/static regression coverage plus an explicitly required,
  separately executable real-PostgreSQL role/catalog suite for core-only
  databases, actual non-owner runtime roles, authorized non-DND writes after
  RLS hardening, direct/cross-role DND-DML rejection, denied audit reads,
  development fail-closed DND behavior, trusted-bootstrap provenance, and no
  generation wrap/reset path.

## 2. Context-bus mutation and admission helpers

- [x] 2.1 Route `set_context()` and `clear_context()` DND operations through
  the versioned atomic mutation boundary; preserve current non-DND behavior.
- [x] 2.2 Implement a stable per-action mutation ID and opaque correlation
  validation, exact durable replay receipts with requested/effective expiry and
  versioned semantic fingerprints, conflicting-replay rejection for changed
  expiry/value/confidence/metadata, and content-minimizing audit records that
  never retain raw DND payload.
- [ ] 2.3 Implement DND snapshot and transaction-bound admission helpers with
  database-clock state evaluation, `revalidate_at`, and conflicting guard locks.
- [ ] 2.4 Add focused unit coverage for validation, receipt shaping, failure
  closure, canonical fingerprint normalization, changed-expiry and
  changed-semantic-payload replay rejection, uncomparable-replay fail closure,
  expiry revalidation, and generation-exhaustion behavior.

## 3. Canonical-writer and future-consumer integration

- [x] 3.1 Update the General explicit-context tool path to supply stable
  per-action mutation/correlation identity for DND set and clear operations;
  retries of the same routed action must reuse the same identity rather than
  generating one from wall-clock time or raw DND content.
- [ ] 3.2 Require any Switchboard DND path to originate from accepted-event
  evidence and use the canonical mutation operation before it is enabled.
- [ ] 3.3 Integrate Health/Messenger guarded admission only in the dependent
  wake-recovery implementation; carry the captured generation through
  authenticated Switchboard MCP packets without adding direct peer SQL.

## 4. Behavior-executing verification and rollout

- [ ] 4.1 Add PostgreSQL integration tests for General/Switchboard concurrent
  mutations, exact/conflicting replay, changed-expiry/semantic-payload replay,
  DND refresh/reactivation generation advancement, and committed atomic
  guard/context/audit visibility.
- [ ] 4.2 Add concurrency tests proving stale snapshots reject before a local
  durable admission, admission-vs-writer lock ordering, and DND-before-egress
  prevents future Messenger intent admission.
- [ ] 4.3 Add restart and database-time TTL tests proving durable reconstruction
  from persisted semantic identity/effective expiry and expiry revalidation
  without an inferred client clock or background expiry writer.
- [x] 4.4 Run strict OpenSpec validation and permitted source/unit checks in
  this source-only PR.
- [ ] 4.5 Before enabling any wake-recovery consumer, execute the
  real-PostgreSQL migration/role/catalog/concurrency suite (including actual
  `SET ROLE`, `FORCE RLS`, owner/catalog, direct/cross-role DML, replay, and
  lock-order proofs) and the repository quality gates in an authorized database
  environment.
