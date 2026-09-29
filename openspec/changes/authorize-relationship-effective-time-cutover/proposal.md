## Why

The adopted `relationship-fact-effective-time` contract splits the uniqueness transition into two
schema acts. `rel_035` (merged in PR #4254) added the temporal columns and the occurrence index while
keeping `relationship.uq_ef_spo_active`, and the transition writer refuses temporal intent while that
index exists. The second act, rel036, drops the legacy index and thereby enables temporal and
repeated-period writes. The contract allows it "only after every Relationship instance is proven to
contain the complete compatible/fenced mutator inventory, and every old image is proven absent".

The repository cannot produce that proof today:

- No runtime reports its code identity in a form a migration can trust. The deployments ledger
  records a self-reported `GIT_SHA` once per `butlers up` boot; Dashboard API, the `migrations`
  service, and ad-hoc runs never record one, and a row is history, not liveness.
- Relationship mutator code is not wholly in the image. `roster/` is bind-mounted from the host
  checkout into `butlers-up`, `dashboard-api`, and `migrations`, so an image id does not identify the
  code a process runs.
- Nothing prevents an old container from restarting mid-cutover. Every long-lived service is
  `restart: unless-stopped`, and `scripts/compose.sh` and `butlers deploy` start whatever the
  checkout describes.
- If rel036 joined the ordinary chain, every daemon boot and every deploy would apply it
  automatically, erasing the mixed-writer proof window the adopted design requires.

## What Changes

- Add a new `relationship-effective-time-cutover` capability that defines, source-only, the proof and
  enforcement a future rel036 must require:
  - a canonical, signed, content-blind receipt binding authorization id, target Git SHA, immutable
    image id, `roster/` tree id, complete instance set, mutator-inventory digest, test-receipt digest,
    database target, fence id, and a short expiry;
  - a dedicated root-held Ed25519 signer, used only by a fixed wrapper that collects its own evidence;
  - a complete Docker Compose instance proof covering image and bind-mounted tree identity;
  - an enforced writer lifecycle fence that spans inventory, restart disablement, quiesce, container
    removal, PostgreSQL write exclusion, rel036 DDL, and exact-target release, checked by every
    supported start path;
  - a gated-revision rule that keeps rel036 in the ordinary version directory but out of every
    automatic migration path, runnable only with `-x relationship_temporal_cutover_receipt=<path>`;
  - a failure taxonomy in which every missing, mixed, stale, forged, extra, restart-capable, or
    active-writer condition aborts before DDL; and
  - the rollback boundary at the first temporal write, with no flattening escape.
- Draft the operator packet `docs/operations/relationship-effective-time-cutover.md`.
- Name the exact future source and test files for Docker Compose only.

## Capabilities

### New Capabilities

- `relationship-effective-time-cutover`: receipt, signer custody, instance proof, lifecycle fence,
  gated rel036, rollback boundary, and ownership for the Relationship legacy-index cutover.

### Modified Capabilities

None. The adopted `relationship-facts` bodies in `relationship-fact-effective-time` are referenced,
not restated or overwritten.

## Impact

- Specification and documentation only. No rel036 code, migration execution, deployment, container
  stop or start, database write, key provisioning, wrapper installation, sudoers change, credential
  access, or temporal activation occurs in this change.
- `bu-h3b7t` retains implementation ownership; this change fixes the proof its task 3.3 must satisfy.
- A live cutover additionally depends on owner adoption of the dedicated signer (see `design.md`,
  "Owner decision"), the `relationship-fact-effective-time` task 3.5 real-PostgreSQL scenarios, and
  the owner-gated entity-merge collision wording amendment `bu-ldcp5f`.
- Temporal writes remain refused `temporal_cutover_pending` until a separately authorized cutover.

## Acceptance Status

This is a proposal. Drafting, validation, review, and CI do not constitute owner acceptance,
signer adoption, implementation authority, or approval of any live effect.
