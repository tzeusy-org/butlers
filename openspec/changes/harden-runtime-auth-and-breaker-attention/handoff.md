# Runtime Auth and Breaker Attention — Design-Cycle Handoff

## Status

**Gate 6 passed and the refined security-doctrine contract is adopted.** The
owner approved the signed process-bound control design and explicit at-most-once
policy before PR #3705 merged. The subsequent execution-planning review made
the deployment-key representation and rotation contract more precise and
drafted the required doctrine amendment. The owner explicitly adopted that
exact amendment and refined contract on 2026-08-10. Beads exist, but
implementation stays held until the amendment merges and the repaired graph
receives fresh GO verification. No runtime state, data, or code has been
changed by this planning phase.

## Funnel Gate Record

| Gate | Result | Evidence / decision |
|---|---|---|
| 0 — Shape | pass | This is a bounded feature request spanning credentials, routing, Switchboard delivery, model catalog, and two operator surfaces; it is not a broad project-direction exercise. |
| 1 — Current truth | pass | Live diagnosis established a stale schema-local Codex credential overwriting the shared daemon home, dashboard-local probes diverging from routed dispatch, OpenCode canonical-identity versus execution-syntax divergence, a non-atomic breaker debounce, and a post-send ACL failure that reclassified confirmed Telegram delivery. |
| 2 — Desired state | pass | One explicit Codex CLI-auth authority; canonical catalog identity with execution-boundary OpenCode mapping; private runtime-bound probes; serialized breaker-edge episodes; Switchboard-owned at-most-once delivery; explicit uncertain reissue; truthful UI facts. |
| 3 — Specification | pass | `proposal.md`, `design.md`, and nine capability deltas describe the behavior and limits. |
| 4 — Risks and constraints | pass | The design preserves schema isolation, does not expose or copy secret values, does not backfill historical pages, does not automatically resend ambiguous transport, and keeps probes separate from routed success provenance. |
| 5 — Work package | pass | `tasks.md` provides dependency-ordered implementation and verification work below. |
| 6 — Human sign-off | pass | The owner approved the design merged by PR #3705 and requested the `$th-projects` execution-planning phase. |
| Execution release — doctrine adoption | pass | On 2026-08-10 the owner explicitly adopted the exact post-merge security-doctrine amendment and refined key-file/rotation contract. Merge and fresh graph verification remain release conditions. |

## Execution decomposition record

The P0 epic **Harden runtime authentication and breaker attention** is tracked
as `bu-0uqgo`. Its dispatch graph is being reconciled against the approved
package and live ownership before implementation release:

```text
doctrine/source release gate
├── outbox representation → producer activation → Switchboard worker ───────┐
├── explicit Codex authority ────────────────────────────────────────────┐   │
├── canonical OpenCode execution mapping ───────────────────────────────┼───┤
└── probe trust representation → signed coordinator ────────────────────┐   │
    Dashboard runtime-child sandbox ───────────────→ mount/caller cutover ──┘
                                                      Models/Spend truth ←──┘
all ten implementation leaves → gen-1 reconciliation → epic report
```

| Tracked child | Priority | Depends on | Completion evidence |
|---|---:|---|---|
| `bu-0uqgo.9` doctrine/source release gate | P0 | — | merged doctrine classification, valid source metadata, four-pass convergence, fresh graph GO |
| `bu-0uqgo.1` durable attention representation | P0 | gate plus external migration owners | real-Postgres migration, retention, producer ACL, and no-backfill tests |
| `bu-0uqgo.2` breaker/fleet producer activation | P0 | outbox representation | serialized edge tests prove one episode and legacy direct delivery is absent |
| `bu-0uqgo.3` at-most-once Switchboard worker | P0 | producer activation | lease/fence/crash/uncertainty and confirmed-send bookkeeping regressions |
| `bu-ih90b` explicit Codex authority | P0 | gate | dashboard-refresh/next-invocation, shared-home, and unavailable-authority tests |
| `bu-0uqgo.4` OpenCode execution mapper | P1 | gate | canonical identity and provider-qualified CLI argument tests without data migration |
| `bu-0uqgo.5` probe trust representation | P0 | gate, outbox convention, external migration/Secrets owners | inert schema/parser/receipt/grant/redaction evidence with no production key mount |
| `bu-0uqgo.10` signed probe propagation | P0 | trust, Codex authority, OpenCode mapper | fixture-key private endpoint, replay, exact-runtime, and no-breaker-reset tests while production remains unavailable |
| `bu-0uqgo.12` Dashboard CLI-auth child sandbox | P0 | gate, terminal base-image owner, explicit Codex authority, OpenCode mapper | complete runtime-CLI inventory, pinned Bubblewrap user/mount/PID namespaces, exclusive outer identities, exact Compose security policy/preflight, pidfd/namespace-init fencing, same-descriptor staging, adversarial peer/daemon tests, and exact deferred verification allowlist |
| `bu-0uqgo.11` probe mount and caller cutover | P1 | signed propagation, runtime-child sandbox | canonical full-stack readiness gate, Test/verify/scheduler cutover, legacy local-probe absence, and safe rollback |
| `bu-0uqgo.6` Models/Spend truth | P1 | worker, probe cutover, external frontend owners | batched breaker state, truthful degraded state, and one fenced reissue successor |
| `bu-0uqgo.7` gen-1 reconciliation | P0 | all ten implementation leaves | exact implementation/spec/ACL/concurrency and separately authorized runtime evidence |
| `bu-0uqgo.8` epic report | P0 | implementation plus reconciliation | complete evidence matrix, diagrams, and VISION callback |

## Verification Matrix

| Concern | Required automated evidence | Authorized runtime evidence |
|---|---|---|
| Credential authority | unit/integration tests for explicit authority, local conflict, flat topology, shared-home writer order, rotation fencing, and safe logs | inspect provenance/metadata only; prove every daemon restores the same authority without reading values |
| Breaker episode edge | real-Postgres concurrent writer tests with equal timestamps and distinct attempt IDs, including failed half-open races | induce one safe controlled breaker edge only after deployment authorization; observe one durable episode |
| Delivery semantics | producer ACL/forgery tests, two-worker claim, fenced recovery/uncertainty, and post-send ACL route tests | verify sent/uncertain episode state and no automatic duplicate delivery |
| OpenCode and probe | adapter/API and real-Postgres tests for canonical Go identities with provider-qualified execution arguments, fail-closed owner control, fixed-algorithm/time signed control, nonce-race/retention/replay denial, key rotation, generic-Secrets exclusion, and probe-no-reset behavior | use an actual runtime probe plus a separate routed session; compare their independent evidence |
| UI | API contract and frontend interaction tests for state separation, degraded observation, confirmation, fail-closed owner-control key states, and idempotent reissue | confirm Models/Spend surfaces reflect actual API state without a false success toast |

## Validation Record

- Baseline focused regression suite passed before design work: **95 passed**.
- `openspec validate harden-runtime-auth-and-breaker-attention --strict` passes.
- `git diff --check` passes for the tracked change package.
- Repository-wide `spec-trace-check.py --authoring` is not a usable clean gate:
  it reported **2,618 existing errors** across main specifications and unrelated
  active changes, and the tool has no scoped-change mode. This package uses
  unique requirement IDs and complete `ID`/`Source`/`Scope` metadata; its
  implementation tasks require test citations before completion.

## Execution release gate

Original design sign-off and explicit adoption of the exact doctrine amendment
are recorded. `bu-0uqgo.9` keeps every implementation lane held until this
correction is merged and the repaired graph receives fresh GO verification.
Closing that gate releases only dependency-ready Beads;
it does not authorize live key generation, deployment, restart, induced
breaker/fleet failures, ambiguous-send simulation, or external resend.
