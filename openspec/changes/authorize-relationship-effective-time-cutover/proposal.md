## Why

The adopted `relationship-fact-effective-time` contract splits the uniqueness transition into two
schema acts. `rel_035` (merged in PR #4254) added the temporal columns and the occurrence index while
keeping `relationship.uq_ef_spo_active`, and the transition writer refuses temporal intent while that
index exists. The future second act, symbolic gate G, drops the legacy index and thereby enables temporal and
repeated-period writes. The contract allows it "only after every Relationship instance is proven to
contain the complete compatible/fenced mutator inventory, and every old image is proven absent".

Actual `rel_036` is the existing meeting-debrief migration, not the cutover. It remains untouched.
G denotes a then-free revision after the actual then-current Relationship head; it reserves no number.

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
- If G joined the ordinary chain, every daemon boot and every deploy would apply it
  automatically, erasing the mixed-writer proof window the adopted design requires.

## What Changes

- Amend the existing `relationship-effective-time-cutover` source proposal to define the complete
  proof and enforcement a future G must require:
  - a canonical, signed, content-blind receipt binding authorization id, target Git SHA, immutable
    image id, `roster/` tree id, complete instance set, mutator-inventory digest, test-receipt digest,
    database target, fence id, and a short expiry, where test evidence comes only from the wrapper
    running the required tests itself;
  - a dedicated root-held Ed25519 signer, used only by a fixed wrapper that collects its own evidence;
  - a complete Docker Compose instance proof covering image and bind-mounted tree identity, bound to
    one named supported Compose invocation, a content-blind configuration digest, and a mechanical
    database-credential rule;
  - an enforced writer lifecycle fence that spans inventory, restart disablement, quiesce, container
    removal, PostgreSQL write exclusion, G DDL, and exact-target release, checked by every
    supported start path;
  - a gated-revision rule that keeps G in the ordinary version directory but out of automatic
    stamped pre-cutover migration paths, with the explicit legacy-receipt path
    `-x relationship_temporal_cutover_receipt=<path>` and the complete owner-A unsigned
    fresh/disposable obligation in REQ-relationship-effective-time-cutover-006;
  - a failure taxonomy in which every missing, mixed, stale, forged, extra, restart-capable, or
    active-writer condition aborts before DDL; and
  - the rollback boundary at the first temporal write, with no flattening escape.
- Draft the operator packet `docs/operations/relationship-effective-time-cutover.md`.
- Name the exact future source and test files for Docker Compose only.

## Capabilities

### New Capabilities

- `relationship-effective-time-cutover`: receipt, signer custody, instance proof, lifecycle fence,
  gated G, rollback boundary, and ownership for the Relationship legacy-index cutover.

### Modified Capabilities

None. The adopted `relationship-facts` bodies in `relationship-fact-effective-time` are referenced,
not restated or overwritten.

## Impact

- Specification and documentation only. No G code, migration execution, deployment, container
  stop or start, database write, key provisioning, wrapper installation, sudoers change, credential
  access, or temporal activation occurs in this change.
- `bu-h3b7t` retains implementation ownership; this change fixes the proof its task 3.3 must satisfy.
- CLOSED `bu-ftd491` answer A allows a non-spoofable unsigned fresh/disposable path while keeping
  receipts mandatory for existing real-data targets. CLOSED `bu-ldcp5f` was applied by PR #4317;
  its no-effective-time merge carve-out is adopted. Neither decision is requested again. The
  September 13 bounded repository implementation release remains in force.
- The partial candidate derives initial Relationship state inside the actual online connection and
  transaction, before branch revisions or its own schema/version preparation. It binds the actual
  database, backend/physical connection, transaction, schema and G invocation; caller flags,
  Config/x-arguments, empty rows, absent indexes and disposable labels cannot create authority.
  Unrelated core/module bootstrap is distinct from Relationship history. Under G's required
  exclusion, all-validity facts must be empty and both original index definitions valid. A
  rel_028 legacy-data import revokes empty admission without deleting its input.
- That witness proves initial state only. Retained REQ003/004/005 require protected complete
  code/test/instance/target/fence proof, and REQ005 compares the quiesced fence to a receipt.
  No equivalent protected non-receipt binding exists. Witness-only unsigned advancement must
  refuse. This prerequisite and genuine disposable interrupted/committed/bootstrap/resume
  provenance remain mandatory and **UNDELIVERED**, rather than permanently receipt-bearing.
  A fully rolled-back traversal may derive a new partial witness; it still cannot cut over without
  complete proof. The temporary fallback is the normal receipt route or separately authorized
  new-target recreation, never automatic destruction or original terminal closure.
- A viable target-bound marker may reuse already-trusted bootstrap/migration/provisioning actors
  under CLOSED A if ordinary-role forgery, trigger, bootstrap-regrant, restore, lifetime and race
  controls are proved. No marker or unsigned binding is selected or implemented here. Only an
  actual new actor, privilege, custody or history guarantee outside existing security doctrine
  needs its precise new decision; current catalogs do not prove past against trusted administrative
  removal of every historical footprint.
- A live legacy cutover additionally depends on adoption of the exact dedicated signer/proof
  artifact (design "Owner decisions"), merged `relationship-fact-effective-time` task 3.5
  real-PostgreSQL scenarios in wrapper-produced evidence, repaired inventoried discrepancies,
  and separate exact-environment authorization. Source review or hosted CI supplies none of
  those live acts. The current Compose proof is unimplemented, with no Kubernetes equivalent.
- Temporal writes remain refused `temporal_cutover_pending` until a separately authorized cutover.

## Acceptance Status

This remains an unimplemented source proposal. This clarification consumes the already closed
owner choices and bounded repository release; drafting, parser validation, review, and CI do not
implement admission, supply runtime proof, adopt a signer/topology, or authorize any live effect.
The original `bu-2z6jyb` outcome stays open until its whole unsigned fresh/disposable/resume and
legacy-receipt acceptance is delivered with the cohesive `bu-h3b7t` implementation. Socket isolation
`bu-jnnxtq` joins the same test-runtime unit; `bu-0kf2fd` remains a serialized, unadopted Kubernetes
source proposal. No foreign source ownership is transferred.
