## Why

**Adoption state: UNADOPTED.** This is the alternative the owner requested in
`bu-viat6h.4` (choice D). It proposes a Kubernetes CronJob that proves recovery in
throwaway PostgreSQL and publishes a single writer-attested attempt. Adoption
would change three clauses. Drafting this text supplies no implementation or
operational authority.

## What Changes

| Adopted clause | Proposed replacement |
| --- | --- |
| `REQ-database-security-006` requires the dedicated deterministic executor and its purpose-bound live-cluster `LOGIN CREATEDB` role. [Restore-drill security delta](../restore-drill-recovery-truthfulness/specs/database-security/spec.md) | The drill may create scratch data only in per-attempt PostgreSQL inside its own Pod. Retire `restore_drill_executor` at cutover and keep every non-bootstrap login on the live cluster `NOCREATEDB`. Give the workload no live database credential. |
| `REQ-deployment-hardening-007` requires an isolated executor, a named scratch database and protected Compose launch, cleanup and rollback. [Deployment hardening delta](../restore-drill-recovery-truthfulness/specs/deployment-hardening/spec.md) | Mount the backup PVC read-only and verify the exact artifact against a named scratch database inside a disposable Pod. Replace the live-cluster executor, relay and generation fence with a Pod-local database, bounded Job lifecycle and restricted evidence submission. Preserve backup coverage, failure and rollback requirements. |
| `REQ-backup-recovery-truth-003` makes the existing executor-owner restore ledger the sole authority. [Adopted candidate, PR #4170 at f2e59fc6](https://github.com/tzeusy-org/butlers/blob/f2e59fc6e13775d168d7a170a5a65dff2117c5b0/openspec/changes/reconcile-backup-recovery-truth/specs/backup-recovery-truth/spec.md#L144-L220) | One protected writer persists one immutable attested row per attempt. It binds the exact backup, completed scopes and cleanup with the overall verdict. Ordinary audit, logs, and CronJob status remain projections or diagnostics. |

The owner adopted the third clause from `b98c89ee470616e3cc414de0865f67908dd0ec20`
with one permitted ID renumber, which produced `f2e59fc6e13775d168d7a170a5a65dff2117c5b0`.
PR #4170 is closed without merging. The third delta is pending delivery of the
adopted baseline through `bu-h40h2b.3`. Cite and reconcile the actual landing
revision before presenting the amendment for a decision.

## Capabilities

- [Database security](amendments/database-security.md) proposes the scratch and credential boundary.
- [Deployment hardening](amendments/deployment-hardening.md) proposes Job admission, isolation, cleanup and rollback.
- [Backup recovery truth](amendments/backup-recovery-truth.md) proposes attestation and write authority.

The living-specs change keeps the proposed replacement wording in these review
artifacts. Adoption would require a later semantic update to the governing specs.

## Impact

Only this OpenSpec change is in scope. The existing Compose executor continues
under its adopted contract. No chart, RBAC, NetworkPolicy, secret, migration,
role, restore operation, deployment or production evidence is changed.

The other candidate is the exact text of historical draft #4500 at
`9abdad8909f70856585a167b69e91d708140008d`, preserved by its replacement
[#4862](https://github.com/tzeusy-org/butlers/pull/4862). That replacement was
closed as stale. Both exact drafts belong in a single choice on `bu-viat6h.4`
after the prior draft is available for review in its current successor and the
third baseline is identifiable on main. Neither text is adopted by this proposal.

Tests: +0 ~0 -0.
