# Database authority rebuild: controlled cutover

Status: ROOT3745-authorized governing source for bu-dss7he.6 at 0cf196b56833602894f56bb0caad87d09ee374b1. No runtime implementation, migration slot, live catalog result, login/secret action or deployment is delivered by this document. The canonical database-security/core-daemon/dashboard-api specifications and the dated db-authority-rebuild archive contain the exact target contract. DIFFERENT applied-source review remains pending. Existing operational procedures remain current until the complete implementation and reviewed transition are delivered.

## Profiles and scope

| Profile | Where it is usable | Required boundary |
| --- | --- | --- |
| Migrator | Explicit CLI/init migration action only | Own ordinary objects, maintain controlled purpose installers, serialize and verify the whole migration; never injected into serving processes |
| Runtime | Actual daemon, control, connector and scheduled-job processes | Exact derived cooperative roster capabilities; connector capability only for its actual process; no reachable object/database/purpose-owner ownership |
| Dashboard | API per-butler/shared selected credential pools and observer | Fixed exact dashboard capability; no generic cross-domain/migration authority |
| Owner auth | Existing independent owner-auth service | Keep its current dedicated login/profile and authentication ceremony |
| Purpose owner | Catalog-owned NOLOGIN/nonprivileged roles | Exact fenced state/definers, unreachable from runtime/dashboard; never credentials to deploy |

Secret names/references are source configuration; secret values are never report material. Source deployment must bind each profile explicitly in Helm dev/prod, Compose, CLI and real cron/control/connector consumers. Preserve current Google deployment topology. No missing-key fallback to a shared owner or migration secret is supported. Do not revive the retired Decision Desk export/apply deployment or grant an absent object.

## Before any live action

Ready3743 independently found the corrected full12 normative packet conforming. ROOT3745 authorized its exact baseline/RFC0006/vision/QA-risk successors within the original release; this source step applies them without performing runtime or operator work. Continue the runtime implementation on this same branch after DIFFERENT applied-source review; no governing-only merge is a prerequisite. The runtime implementation must cover the full retained 105-path plan and all original acceptance rows; .2 enforcement alone is insufficient. Reserve a migration slot from actual then-current heads, not from this document's earlier suggested number. Current foreign/pending audit, QA, COPY and public evolution are integrated through their actual reviewed objects, never WIP copies.

Read-only preflight must identify the database/profile, current chain stamps, every managed object/signature, owner, effective role membership/privileges, PUBLIC/default ACL, forced RLS, definer search_path/ACL and exact manifest comparison. Emit closed identifiers, counts/categories and digests only. Unavailable catalog evidence is unknown, not a clean report. No preflight marker or historical script hash confers authority.

## Operator-owned preparation

The operator provisions the distinct login/secret references and reviews one explicit transfer set. Existing objects transfer only through that reviewed one-time procedure: ordinary objects to migrator, purpose state/functions to their fixed NOLOGIN owners. Shape/policy/trigger checks and exclusive-lock handoff preserve canonical DND. Unexpected owners, extra membership, privileged attributes, shadow objects and partial predecessor catalogs refuse. Application startup never runs privileged repair. This document grants no live operation to an agent and contains no password or runnable privileged command.

The held RUNTIME_PROBE_CONTROL_SIGNING_KEY decision remains unchanged. Source preparation does not move it into a runtime credential store. ROOT revisits it only when the complete original rebuild actually closes.

## Apply and admission sequence

1. Verify the reviewed operator prerequisites and exact code/manifest target before schema/version/autocommit effects.
2. Run only the explicit migrator CLI. Retain one owned advisory-lock session across every declared core/domain/module chain, final-state installer, ACL convergence and catalog verification.
3. Preserve historical stamps/edges, old supported states, core-only targets and real private Memory schemas. Failed apply/commit/release is incomplete, never success by caller acknowledgment.
4. Revoke managed unlisted privileges/defaults, apply only exact manifest rows and independently verify effective privilege reach. Discovery cannot infer a grant. An unclassified current object fails finalization.
5. Serve only after read-only runtime/dashboard admission proves required revisions, exact role profile and no reachable ownership. Repeat acquires restore role after reset and continue to refuse revoked membership.
6. Repeat init/migrate/restart and two concurrent initializers in genuine disposable proof. No successful serving admission may expose an intermediate privilege state.

## Protected survivor checklist

- Canonical DND gateway/private writer, only General/Switchboard role authority, enabled/forced RLS, ordinary non-DND access, current generation/database time, atomic context/guard/audit and private replay receipts.
- Final attention model breaker/fleet halt/operator/condition marker and immutable outbox, lease/CAS/ACK/cancel/Stop/unknown-once semantics.
- Restore executor and target containment, immutable evidence and protected pre-consumer downgrade.
- RFC0010 briefing and QA policy projection, RFC0030 Concierge views, exact named message_inbox readers; no generic cross-schema SELECT.
- Dedicated owner-auth and independent later dashboard attention/stepup interfaces; only the original three bunkers are retired.
- Truthful catalog match/missing/mismatch/unavailable diagnostics replacing source-body drift, with no bodies, credentials or private row contents exposed.

Each removed test assertion, case or fixture must map to an executing survivor before deletion. Count or filename similarity alone is insufficient. Source-text absence supplements real effective-privilege and behavior proof.

## Rollback and recovery

This is a required operator procedure and future proof plan, not an executable reverse migration already shipped. The current source at 0cf196b56833602894f56bb0caad87d09ee374b1 establishes the existing init/Helm/protected-boundary semantics; it is not an observed deployment identity. Before cutover, capture the actual running code/image digest, chart revision and rendered profile references, exact init bytes/digest, every chain stamp, and a closed catalog/owner/ACL/interface report. Retain the old shared-login secret in its existing secret manager for one release; record key names/references only. Dedicated owner-auth and the held runtime-probe signing key are not part of that credential change.

### Supported combinations

| State | Code, catalog and credentials | Required result |
| --- | --- | --- |
| R0, no authority transition applied | Captured predecessor artifact/chart/init + independently unchanged predecessor catalog/stamp vector + retained old shared-login reference | Positive original rollback: operator replays the captured init to restore its grants and restores predecessor Helm env references/image; independent checks precede traffic. |
| R1, authority transition applied and reversibly compatible | Exact rebuild artifact/revision vector + a reviewed, actually tested reverse transition to the captured predecessor catalog/interfaces/stamps + retained old reference | Execute that reverse transition while quiesced, verify it, then perform the same old-init and Helm restoration. No new reverse revision or compatible image is identified by this draft; implementation must supply real artifacts and proof before advertising R1 as supported. |
| P, incompatible or unproven | Requested old artifact/init needs a protected boundary/data removed, owner shape differs from old-init admission, reverse effects/stamps are unknown, or evidence is unavailable | Refuse before init/reverse effects. Preserve current data, receipts, generations and stamps. Use only an independently verified compatible rebuilt artifact with split profiles for code recovery, or remain stopped for forward repair. This is not successful predecessor restoration. |

R0/R1 preserve the explicit original retained-old-secret/revert-Helm/old-init grant-restoration result. An explicit operator return to the recorded predecessor release is distinct from a rebuilt serving process falling back to a shared owner on missing keys. The latter remains forbidden. A protected receipt does not itself ban every R1 path: a path that retains the same boundary and data must still prove actual predecessor code/init compatibility. No rule permits destroying receipts or resetting generation to make a path eligible.

### Ordered operator recipe

1. Stop or fence actual serving writers, schedules, connectors and control actions using their existing owned shutdown/Stop mechanisms. Retain incomplete native pool/task ownership and do not call a timed-out shutdown success. Verify quiescence before authority or deployment rollback effects.
2. Load the captured artifact identities and key references without printing values. Read the actual chain stamps and effective catalog under the authorized observer. Select R0 only when the original combination is unchanged; select R1 only when its real reverse path and predecessor compatibility receipts exist. Mismatch, unreadability, unknown commit or unsupported target selects P; never infer that `alembic downgrade` to a convenient number is safe.
3. For R1 only, run the exact reviewed reverse target/owner transfer through the delivered single-owner migration/lock path. Its preflight runs before newer autocommit work and checks protected state before each irreversible boundary. Reverse only effects belonging to this rebuild, preserve the already-protected predecessor interfaces/data, and let the actual migration path manage its revision changes. Never set a stamp by hand or issue ad hoc GRANT/ALTER OWNER to imitate a successful transition. Independently read back predecessor owners, memberships, object/function signatures/ACLs and the expected stamp vector. A partial/unknown result remains stopped for readback/reconciliation, not a blind retry.
4. As the privileged operator, replay the exact captured predecessor scripts/init-db.sql using its documented `psql -f` entry with ON_ERROR_STOP and the captured configured connecting-user name. This is the actual original grant-restoration action. The old script's ownership/admission checks must succeed against the proven predecessor catalog; do not modify the script, skip preflight or substitute a fixture grant. Run no schema downgrade merely to make this init succeed. Stop on any rejection and record incomplete effects truthfully.
5. Restore the captured predecessor chart/image and its recorded Helm POSTGRES_USER/POSTGRES_PASSWORD key references to the retained old shared login. The repository currently maps these through deploy/helm/butlers/values*.yaml and shared helper wiring; use the recorded release values, not assumed dev/prod key names or `commonEnv` fallback. A Helm history rollback alone is insufficient because it does not restore database authority. Perform no automatic migrations as an unreviewed deployment side effect.
6. Before traffic, use independent acquisitions and the actual selected old serving profile to prove the original expected grants and current protected refusals, unchanged application/evidence data and correct chain stamps. Confirm actual predecessor startup/readiness and dedicated owner-auth before resuming. If the chart applies but readiness or catalog proof fails, retain stopped/incomplete state; no acknowledgment, successful Helm command or old secret possession proves rollback completed. Remove the retained old secret only after the promised release window and the normal operator recovery decision.

### Concrete protected-state limit

The current DND admin rollback in scripts/init-db.sql and core_197 refuses removal of its boundary once a replay receipt or nonzero generation exists. The current attention admin rollback/core_198 refuses removal with durable outbox/lease evidence; later revisions may refuse earlier still. The old init's amendment-owner preflight also rejects an unexpected owner. These constraints survive the rebuild as actual final-state equivalents. They prohibit a destructive or mismatched R1, not just automatic credential fallback. Preserve user_context, all evidence and chain history; do not restore an earlier backup over newer effects to manufacture the old outcome.

An unimplemented or unproven reverse path is unfinished preserving-rollback work required in the existing105 plan, not a permanent exception and not a new human prerequisite. Refuse that unsafe attempt now, then implement and prove the real R1 support; do not ask the owner merely because it is not shipped. Current source proves the named destructive paths unsafe, not the impossibility of every preserving predecessor restoration.

If original acceptance requires old-init/old-Helm restoration for a P state and no preserving reverse path is possible, that exact original outcome remains unmet. Record the incompatible requested predecessor, protected boundary, disallowed effect and required preserving alternative/disposition under the existing exact-text adoption process. Do not call P a successful rollback, invent an executable compatibility release, silently narrow original acceptance or add a blanket new permission gate for the rest of the engineering implementation. No such additional disposition is recorded by this draft.

### Required genuine rollback proof

These are named future acceptance controls, not existing passing tests. Use real managed bootstrap/migrations and actual runtime/observer roles; fixtures may not manufacture the grants or change the protected production policy.

| Control | Actual producer/action and independent acceptance |
| --- | --- |
| RB-01 predecessor restoration | Capture a genuine predecessor install and representative data/stamps; take R0, revoke a normally restored grant as the causal fault, replay the captured init and restore actual rendered Helm references. Observe the grant restored through the actual old profile, preserved protected refusals/data/stamps and successful serving readiness. Init omission must leave the intended permission denied; old-reference omission must fail the actual profile/readiness control. |
| RB-02 preserving reverse transition | Apply the genuine rebuild from a supported old stamp with representative protected state; exercise the actual R1 reverse path, pinned old init and chart/profile restoration. Independently prove old-profile positive operations and protected negative operations, full relevant owner/ACL/recursive-membership/signature/stamp equality and data/evidence preservation. Include a predecessor whose existing protected receipts remain intact; do not substitute generation reset. |
| RB-03 destructive return refusal | Seed a real DND receipt/nonzero generation and real attention outbox/lease evidence through owning operations; request a path that would remove them. Both migration entry and direct supported rollback interface must refuse before DDL/DCL/stamp/autocommit effects. Independent observers prove data, owner/ACL/interface and version state unchanged. |
| RB-04 wrong combination refusal | Supply the wrong recorded owner/init digest, mismatched stamp/interface, missing old credential reference, or unavailable catalog read. Prove refusal before reverse/init effects, no automatic shared-owner fallback and no falsely healthy daemon/dashboard admission; cancellation and unexpected errors retain their existing semantics. |
| RB-05 interruption and concurrency | Interrupt/cancel before and after an actual reverse commit/old-init step, including lost acknowledgment and native release resistance; reconcile durable state under the retained owner/lock. A second initializer cannot cross the owner. Independent readback chooses the true next step without duplicate destructive effects, premature pool close or invented success. |
| RB-06 release boundary and audit | Render actual predecessor and rebuild Helm/Compose/cron/control/connector/dashboard profile bindings with synthetic references, proving only the explicit operator transition activates the retained old reference, owner-auth stays separate and no secret value enters receipts. Record P states and their unresolved original outcomes; require every necessary state to have RB-01/RB-02 success or an exact adopted disposition before original rollback acceptance closes. |

## Evidence and completion

Required implementation proof includes actual positive/refusal runtime and dashboard identities, recursive role/ownership reach, every manifest object/operation including Concierge, PUBLIC/default/column/sequence/function ACL, actual purpose operations/shadow refusal, DND concurrent rollback, fresh and old stamps, repeated bootstrap, two migrators, finite native cancellation/release, real startup/CLI/dashboard/observer and rendered credential wiring. Use genuine migrated PostgreSQL roles and independent committed readback, not setup GRANTs or privileged test callers. Follow the dirty planner, affected API/roster scopes, full topology, inventory/budgets, static/import/SQL/frontend gates and exact-head hosted/protected CI.

This text increment earns authoring/preservation checks only: Tests: +0 ~0 -0; no feature software tests, PG bodies, live secrets or cutover. Completion of the original task requires every original slice with executing security survivors or an explicit owner-approved disposition, not another generic runner/live-deployment gate.
