## 1. Guarded public-state migration

- [ ] 1.1 Add a guarded core migration for the singleton DND generation row and
  replay-safe mutation audit, including durable requested/effective expiry and
  versioned privacy-preserving semantic fingerprint fields, seeded at generation
  `0` without rewriting existing `public.user_context` rows.
- [ ] 1.2 Enforce least privilege so only verified General/Switchboard canonical
  mutations can alter DND through a pinned `SECURITY DEFINER` operation and
  forced DND RLS boundary, while existing authorized non-DND writes retain their
  application-level path, Health/Messenger retain only the public reads required
  for guarded admission, and no peer-schema access is added.
- [ ] 1.3 Add migration and ACL regression coverage for core-only databases,
  actual non-owner role-enforced runtime connections, authorized non-DND writes
  after RLS hardening, direct-DND-DML rejection including row-type crossings,
  denied direct replay-audit reads, development fail-closed DND behavior, and
  no generation wrap/reset path.

## 2. Context-bus mutation and admission helpers

- [ ] 2.1 Route `set_context()` and `clear_context()` DND operations through
  the versioned atomic mutation boundary; preserve current non-DND behavior.
- [ ] 2.2 Implement durable mutation IDs, correlation validation, exact replay
  receipts with requested/effective expiry and versioned semantic fingerprints,
  conflicting-replay rejection for changed expiry/value/confidence/metadata,
  and content-minimizing audit records.
- [ ] 2.3 Implement DND snapshot and transaction-bound admission helpers with
  database-clock state evaluation, `revalidate_at`, and conflicting guard locks.
- [ ] 2.4 Add focused unit coverage for validation, receipt shaping, failure
  closure, canonical fingerprint normalization, changed-expiry and
  changed-semantic-payload replay rejection, uncomparable-replay fail closure,
  expiry revalidation, and generation-exhaustion behavior.

## 3. Canonical-writer and future-consumer integration

- [ ] 3.1 Update the General explicit-context tool path to supply stable
  mutation/correlation identity for DND set and clear operations.
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
- [ ] 4.4 Run strict OpenSpec validation, targeted contract/integration tests,
  migration/ACL checks, and the repository quality gates before enabling any
  wake-recovery consumer.
