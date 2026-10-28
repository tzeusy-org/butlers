## Context

See proposal.md for the concrete ownership escape paths. The source baseline is 0cf196b56833602894f56bb0caad87d09ee374b1. Delivered .2 already makes runtime SET ROLE fail closed and supports real native pools/private Memory schemas. Those behaviors are inherited. Shared login ownership, bootstrap public/default grants, runtime migration execution and dashboard role omission remain separate defects. No catalog or live deployment was inspected in this text increment.

## Goals / Non-Goals

Complete the original .6 model across connecting identities, reachable membership, object ownership, public privileges, purpose functions, actual serving consumers and operator transitions. The full 105-path implementation allocation and original slice/AC matrix are retained as the implementation plan; proposed paths and migration numbers in that earlier plan are not a source lease or slot reservation.

No per-butler process split, new domain grant, runtime owner-auth credential change, Google deployment consolidation, revived Decision Desk/applier, .11 baseline substitution, or runtime-probe key retirement is included. Live role/login creation, secret values and one-time privileged ownership transfer are operator acts. This packet changes no runtime, database or active baseline.

## Decisions

### Separate identity from capability and ownership

The runtime connecting login may SET exactly the derived current roster capabilities, plus connector_writer only where an actual process requires it. This remains cooperative in-process isolation, not cryptographic identity. Recursively inspect every SET-reachable role, including indirect and NOINHERIT memberships, and reject unexpected membership, privileged attributes, database/object ownership or undeclared CREATE. The dashboard login reaches only its fixed dashboard capability. Dedicated dashboard owner-auth stays independent. The migrator owns ordinary application objects; fixed purpose NOLOGIN roles own fenced state and definers. Serving identities cannot reach purpose owners. This is more precise than transferring every object to the ordinary migrator, which would destroy the DND/purpose-owner boundary.

Derive current roster names/schemas once. Preserve historical revision identities and their allowed literal lists; new code and revisions consume derivation. Concierge is included wherever its actual own/common-public consumer requires access. The historical 41/45 claim and current static list counts are not catalog failures or grant authority.

### Use a typed exact manifest, not discovery as authorization

Each row contains a qualified object/signature and kind, expected revision/owner, exact role operations and columns, PUBLIC/default ACL, required RLS, purpose search_path/underlying privileges where relevant, actual producer and selected pool, and the governing exception. Stable identifier quoting and deterministic ordering generate SQL/catalog expectations from one source. Discovery inventories known objects; it cannot infer access. Unknown objects and incomplete classifications block finalization. Sequence access, function overloads, column grants, ownership and recursive membership are included in effective privilege checks, not just has_table_privilege on one table.

Shared writes are narrowed by actual producer/pool provenance, not by copying dated audit rows. The manifest must classify all current tables including formerly insufficient account/entity/secret/spend/deployment/provider/state/event/Insight/backup entries before finalization. Dropped contacts/contact_info/public.facts or retired applier objects are absent entries, not new tables to recreate. Existing append-only evidence and forced RLS remain independent of ACLs. qa_patrols broad-risk wording is intentionally superseded: QA plus the authenticated dashboard synthetic path keep INSERT/UPDATE; no DELETE history authority. This is an original released normative change, not a claim current bootstrap already enforces it.

### One migration owner; runtime admission is read-only

The trusted CLI composition holds one physical session/advisory lock across all declared core, domain and module chains, final-state installation, manifest apply and catalog verification. Per-chain engines must not release this serialization owner. Lock/work/release and cancellation remain finitely owned; a timeout or unknown commit cannot emit success. Serving processes have no migrator credentials. Daemon startup verifies required revisions and interfaces under the serving identity before config seeding/tools/schedules, preserving module dependency cascades and actual private Memory chain/schema selection.

Do not conceal privileged repair in init, startup, a dynamic import or a role-none path. Read-only preflight precedes extension/schema/version writes and protected downgrade/autocommit operations. A current valid database can restart without rerunning init. Missing owner-provisioned roles or ownership is a concrete operator prerequisite; the source implementation remains actionable without inventing a fresh policy gate.

### Replace mechanisms with actual final-state survivors

| Predecessor mechanism | Required executing successor |
| --- | --- |
| Restore admin bunker/singleton | Migration-owned exact restore interface, executor containment, role refusals and independent rollback/data readback |
| DND installer/finalizer bunker | Purpose owner, known-shape exclusive handoff, FORCE RLS, gateway/private active-role checks, current generation/database-time and atomic context/guard/audit |
| Runtime-attention admin/version v2-v5 ladder | One final installer for model breaker, fleet halt, operator and condition-marker interfaces; immutable outbox, idempotence, debounce, leases/ACK/cancel and unknown-outcome semantics |
| Stored-body init parser | Actual catalog ownership/ACL/signature/search_path/revision match, absent/mismatch/unavailable UI states, content-blind diagnostics |
| DashboardProbeRoleView / ambient role-none exception | Fixed dashboard pool and exact reserve/record function identity, preserving epoch/sequence/CAS/nonces and owner stop |
| Dead calendar role | No live role/member/ACL/source dependency; current owning Calendar roles unchanged |
| Broad literal role populations | Derived current roster plus explicit auxiliary capabilities; all real Concierge positive and forbidden-domain controls |

Historical revision IDs, chain edges, evidence and supported old stamps remain. Remove old executable branches only when fresh install and old-stamp upgrades prove the final bodies. No unconditional no-op migration or source-reference grep substitutes for that proof. Independent dashboard attention/stepup and unrelated definer checks survive; original three bunkers do not mean every later security purpose is obsolete.

### Preserve adopted exceptions and active amendment lineage

RFC0010 General filtered briefing and QA fixed no-argument policy projection, RFC0030 Concierge's two filtered telemetry views, and named Relationship/Messenger/Travel message_inbox reads remain exact exceptions. QA's legitimate cross-read is not replaced with a blanket SELECT denial. Underlying domain rows, future Switchboard tables/default privileges and per-domain payload remain denied.

The canonical-dnd-generation-guard active Public Schema Write Authorization Matrix is a predecessor of the same modified requirement here. This successor carries all its scenario names and runtime/privacy/rollback guarantees while expressly replacing only the bootstrap installation model and released broad matrix. ROOT3745 authorizes application of this exact same-matrix successor to the active predecessor; archive order alone must not restore it. Its independent REQ-database-security-012 and every unrelated requirement stay byte-exact. External full replacement copies make that reconciliation reviewable without editing a foreign active change now.

## Migration Plan

1. Ready3743 independently reviewed the corrected full12 contract. ROOT3745 authorized the exact baseline, active-predecessor, RFC0006, vision and QA-risk successors within the original release. Apply and validate them on this same branch, then obtain DIFFERENT applied-source conformance before the runtime groups. No separate governing-only merge is required.
2. At runtime source admission refresh actual migration heads and relevant hunk custody. The previously proposed core281 is NOT RESERVED; pending audit279/QA280 and public evolution require a real current slot/chain review.
3. Implement the full retained allocation: derivation/manifest/catalog, serving profiles, CLI owner, final installers and downgrade preflight, then deployment/observer/diagnostic source and named survivors as one coherent authority transition.
4. Produce source-only operator preflight/transfer/runbook. Operator provisions separate logins/secrets and performs the reviewed one-time owner transfer. Agents neither read values nor execute privileged cutover.
5. Apply migrations through the CLI, converge ACLs and verify catalogs before serving. Repeat init/migrate/restart and concurrent initialization must converge without widening or partial successful admission.
6. Exercise the operator rollback states and ordered procedure in the cutover document: unchanged predecessor catalog, proven reverse transition back to that catalog, and incompatible protected state. Pin the actual predecessor code/chart/init digests and revision vector; no hypothetical executable release is supplied by this draft. Keep the old secret for one release. The positive path explicitly replays the pinned old init and restores the predecessor Helm secret references, after any required proven reverse transition and before independent readiness. Refuse an incompatible path before effects and preserve its exact unmet original outcome; never install runtime credential fallback.
7. Complete all original slices and genuine exact-head proof before task completion. The held signing-key question is revisited only at actual completed closure.

### Concrete rollback disposition and source evidence

The complete operator recipe and acceptance identifiers live in [the cutover document](../../../../docs/identity_and_secrets/database-authority-cutover.md#rollback-and-recovery). The predecessor is the actual release captured at cutover, not an invented version: source baseline 0cf196b56833602894f56bb0caad87d09ee374b1 is evidence for current behavior, not proof of what is deployed. Record code/image digest, chart/values and secret key references, init bytes/digest, exact chain stamps and catalog/owner/ACL/interface facts before applying the rebuild. Record no secret values or private rows.

| State and exact combination | Concrete disposition |
| --- | --- |
| R0: captured predecessor code + unchanged predecessor catalog/stamps + retained predecessor shared-login reference | Quiesce actual writers; validate the recorded combination; operator replays that exact predecessor init; restore recorded predecessor Helm values/image and old secret references; independently verify grants, protected data/stamps and readiness before resuming. This is the original positive rollback result before authority change. |
| R1: rebuilt catalog + a proven reverse path back to the captured predecessor's interfaces/owners/ACLs/stamps + retained old reference | Quiesce writers; preflight and execute the exact reverse path under owned migration/lock authority; re-read the complete predecessor combination; then execute R0's old-init and Helm sequence. No arbitrary downgrade or copied historical installer is that path. R1 is an implementation obligation, not a reverse migration delivered here. |
| P: requested predecessor requires removal of a protected boundary/data, or the old-init preflight/catalog/reverse compatibility proof fails | Refuse before any reversing DDL/DCL/version/autocommit/init effect; retain current data/evidence/stamps and either restore a proven compatible rebuilt artifact with split profiles or remain stopped for forward repair. Do not describe this as successful old-model rollback. |

Existing code makes P concrete: scripts/init-db.sql's DND rollback routine and core_197 refuse durable DND receipts/nonzero generation when removing that boundary; its runtime-attention rollback and core_198 refuse durable outbox/lease evidence. The old init also checks expected ownership before its amendment ownership handoff, so replay after a different owner transfer cannot be assumed valid. These are not a blanket ban on every code rollback after a receipt: a reverse path that retains the same protected state may qualify only through actual compatibility proof. Ordinary bootstrap rewidens grants and is a privileged operator operation, not a serving credential recovery mechanism.

Missing reverse implementation or compatibility proof is unfinished work within the existing105 allocation, not evidence that a preserving path is impossible and not a human prerequisite. Such an attempt is operationally P until the required R1 support is built and tested. Current source proves that specific destructive boundary removals refuse; it does not prove that all predecessor-compatible, boundary-preserving restoration is impossible.

The canonical original Helm/old-init result is therefore retained as R0/R1 positive acceptance, with P recorded as an exact incompatibility rather than hidden behind generic compatibility prose. No authority is claimed to erase protected state to satisfy it. If a state required by original acceptance is P and cannot be implemented by a preserving reverse path, its precise unsatisfied original result needs an explicit disposition through the existing exact-text adoption process; this draft neither supplies that decision nor labels the whole original rollback complete. The remainder of the full released implementation remains actionable.

## Risks / Trade-offs

- A missing manifest producer can starve a real consumer. Mitigate with actual selected-pool traces, all-role positive/refusal matrix including Concierge, and independent committed readback; do not compensate with broad grants.
- A serving login can switch among its declared cooperative roles. The process boundary remains the released model; unreachable ownership and exact capabilities constrain accidental authority, without claiming hostile-process isolation.
- Old migration bodies and protected autocommit downgrades are coupled. Preserve stamps/edges and execute both fresh and supported-old-state transitions before removing branches.
- Catalog inspection can fail. Mark unavailable/unknown rather than matched; never return bodies, row values or credentials.
- Operator rollout can mismatch credentials/code/catalog. Fail closed at admission and use the explicit rollback matrix, not a compatibility bypass.

## Verification Plan

Pure software controls cover deterministic derivation, exact unknown-object refusal, CLI connection/lock ownership, read-only daemon paths, dashboard connect/acquire/release, native pools/partial failure/cancellation, SQL identifier safety, Helm dev/prod/Compose credential references and truthful diagnostics. Genuine PostgreSQL controls exercise all current runtime/connector/dashboard roles, recursive membership/ownership, effective grants and sequences/functions/columns, PUBLIC/default ACLs, forced RLS, shadowing, actual purpose functions and independent committed/rollback observations. Include two concurrent migrators, old/fresh/core-only/private-Memory targets, init replay, unclassified objects and protected downgrade before autocommit. No fixture GRANT may manufacture the runtime proof.

Before any old test/assertion/parameter/fixture loss, map its complete cases to an executing survivor and retain meaningful causal removal/restoration under the current evidence protocol. Keep legacy text counts separate from real invariants. Planner shared-boundary escalation, full tests+roster collection, static/import/SQL checks, actual inventory/budgets and exact-head hosted/protected CI are required by the runtime change. No tests or PG bodies are run in this normative increment.
