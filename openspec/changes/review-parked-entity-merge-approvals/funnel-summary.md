## Feature Request: Reviewed approvals for parked entity merges

Size: medium. Several existing components and an owner-visible approval contract
change are involved, but no new subsystem or doctrine exception is needed.

Baseline: `9ffee3f92de8145dfdf88f4dfe50b44a73371432` (`origin/main` at authoring).
Tracking: `bu-jzhy31` owns specification delivery, not application implementation.

### Gate 0: Baseline

- [Observed] Doctrine exists under `about/heart-and-soul/`; owner sovereignty,
  deterministic infrastructure and exact-call approval constrain this repair.
- [Observed] Design contracts exist under `about/legends-and-lore/`; RFC 0021
  governs the decision loop and RFC 0023 notification presentation bindings.
- [Observed] Capability specs exist under `openspec/`; the deltas extend
  `dashboard-approvals`, `module-approvals` and `entity-identity`.
- [Observed] Topology exists under `about/lay-and-land/`; `frontend.md` and
  `integration.md` place the React/API owner surface and existing approval dispatch.
- [Observed] Engineering standards exist under `about/craft-and-care/`; the test
  ladder, same-change docs, independent review and hosted merge-queue gate apply.

### Gate 1: Motif and success

[Observed] A live dedup-curation request proposed a person-to-organization merge
and remained pending without execution. Direct invocation returned no dashboard
preview for either merge tool, while a registered messaging-tool control returned
one. No live action was modified.

The motif is an informed owner decision on a parked identity repair: compare the
records, know which survives, and resolve the original request. Success means all
dashboard entry points show the fixed operation before confirmation, cross-type
merges keep the target type, callbacks cannot bypass review, and both decision
history and merge/rebind outcomes remain truthful.

### Gate 2: Doctrine

Aligned. `vision.md` Rule 1 preserves owner control, Rule 4 keeps the flow
deterministic, and `security.md` Approval Gates requires executing the exact stored
call the owner saw. The feature does not widen grants, add agent approval tools,
relax owner authentication or create a schema-access exception. Relationship's
manifesto supports maintaining accurate identity without a new domain duty.

### Gate 3: Topology

[Observed] The owner SPA calls the dashboard approval API and the existing
Relationship comparison endpoint. Approval dispatch remains the owning daemon's
registered handler and Relationship's single merge authority. Existing references:
`frontend/src/pages/ApprovalsPage.tsx`,
`frontend/src/components/relationship/MergeCompareDialog.tsx`,
`src/butlers/api/routers/approvals.py`,
`src/butlers/modules/approvals/{previews,notifications,executor}.py`, and
`roster/relationship/tools/entity_merge.py`.

### Gate 4: Design

`design.md` records typed review compatibility, comparison reuse without the
standalone mutation, original-call binding, channel eligibility and lifecycle
failure distinctions. The Dispatch design-language contract and th-design
design-bar informed the explicit identity labels, discoverable review entry,
acknowledged loading, retry, keyboard handling and accessible final confirmation.

[Inferred] A typed response with null generic preview is the smallest safe
compatibility contract: adding a shared renderer alone would unlock old generic
clients and notification buttons. This is an engineering choice inside the
approved feature scope, not a new owner permission or authentication policy.

### Gate 5: Specification

The three delta specs define five requirements and nineteen scenarios:

- `REQ-dashboard-approvals-902`: structural comparison, fixed direction, all
  entry points, failed reads, currentness and final confirmation.
- `REQ-dashboard-approvals-903`: Deny/Defer, retained rows, terminal states and
  execution versus rebind completion.
- `REQ-module-approvals-008`: original approval, both decision routes, stored
  argument binding, no edits, malformed commands, legacy alias and failure recovery.
- `REQ-module-approvals-009`: notification/callback refusal, older clients,
  unaffected messaging previews and owner authorization.
- `REQ-entity-identity-009`: target-type preservation in both cross-type directions.

Active deltas were inspected. `decision-loop-one-tap-approvals` governs dossier
and push admission; `generalist-lead-focused-delegation` governs generic gate and
executor changes; `repair-whatsapp-identity-reconciliation` governs its narrower
guarded repair. None owns this parked-merge dashboard review requirement. The new
delta adds disjoint named requirements and retains those existing contracts;
it does not fork or overwrite them. Recheck overlap before implementation.

Semantic completeness sweep:

| Dimension | Contract and verification home |
|---|---|
| Failure/recovery | Comparison retry, pre-decision currentness refusal, post-decision execution failure; module-008 and dashboard-902/903 |
| Authorization/privacy | Existing owner auth, no caller actor authority, no raw preview fallback; module-009 and tasks 2.1/3.1 |
| Concurrency/idempotency | Reviewed-call binding, late-response invalidation, final confirmation and existing action locks; dashboard-902 and module-008 |
| Lifecycle/retention | Original pending ID, legacy name, denial/deferral, terminal controls, retained history/receipts; dashboard-903 and module-008 |
| Limits/performance | Reuse existing compare queries/exclusions; they return all qualifying active facts, so large-payload rendering remains a risk to verify. No model calls/new datasets; design risks and tasks 1.3/2.4 |
| Compatibility/rollout | Null generic preview plus optional typed review, both API paths, existing rows and rollback; module-008/009 and design Migration Plan |
| Human interface | All entry points, names/types/IDs, loading/error/retry, keyboard, focus/cancel; dashboard-902 and tasks 2.2/2.4 |

Non-goals live in `proposal.md`. No mandatory behavior contains an unresolved
product choice. Snapshot-wide freezing of every entity fact is not introduced;
existing merge-time conflict rules remain authoritative.

### Gate 6: Engineering acceptance

`tasks.md` imports the repository standards into named verification seams,
documentation, safe failure classification, real-Postgres transaction evidence,
old/new client compatibility, rollback and independent application review. Tests
must reproduce the defect first; refusal checks need positive controls and
guard-neutralization runs. No requirement-per-test or parallel scan-gate quota
is imposed. Application tests and the mandatory implementation closeout remain
incomplete because this PR specifies the feature only.

### Sign-off and handoff

Human sign-off: the requesting owner, 2026-11-03, approved the repair proposal
in this session and explicitly requested feature-request formalization. The
preceding clarification approved person/organization merges retaining the
target type. This changeset records that approval without treating it as approval
to execute a live merge or replay historical actions.

Open product questions: none. Any scope, grant, schema or conflict-algorithm
discovery during implementation returns to the earliest affected funnel gate.

Recommended handoff: `th-projects` project-direction, tracked by `bu-cw5m2d`,
consuming this approved changeset and current baseline for allocation. It must reconcile ownership with
current beads/PRs before dispatch. Do not split this vertical outcome by file,
language or UI/backend layer; the same review/binding contract crosses them.
No sequencing, implementation schedule or application delivery claim is made by
this feature-request pass.

### Specification verification

- [Observed] Named strict OpenSpec validation passes for this change; all four
  schema artifacts are complete. All 306 canonical specs and active changes pass
  the repository's `make check-openspec-strict` command.
- [Observed] Repository spec-overwrite, requirement-trace and countable-task
  guards pass. There are no modified baseline requirement blocks in this change.
- [Observed] The th-projects affected-authoring check passes against the complete
  native baseline. Its full-tree diagnostic remains NONPASS with 3,150 existing
  errors and 505 warnings; this feature does not repair or suppress that debt.
  The new requirements have no implementation citations yet, as expected in
  authoring mode. Full findings are retained outside the repo with the task's
  validation receipts, not copied into the capability specs.
- [Observed] Independent semantic review covered the approved scope, all seven
  authored artifacts, active contracts and the implementation seams. Its only
  finding corrected an unsupported claim that existing compare queries are
  bounded; they return all qualifying active facts. The correction acknowledges
  the risk without adding a pagination feature.
- No application code, tests, live approvals or entity data changed. Implementation
  tests and all `tasks.md` items remain open for the delivery owner.
