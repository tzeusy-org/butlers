# Runtime Auth and Breaker Attention — Design-Cycle Handoff

## Status

**Gate 6: awaiting human sign-off.** This package is design-complete and has
not created Beads, changed runtime state, migrated data, or implemented code.

## Funnel Gate Record

| Gate | Result | Evidence / decision |
|---|---|---|
| 0 — Shape | pass | This is a bounded feature request spanning credentials, routing, Switchboard delivery, model catalog, and two operator surfaces; it is not a broad project-direction exercise. |
| 1 — Current truth | pass | Live diagnosis established a stale schema-local Codex credential overwriting the shared daemon home, dashboard-local probes diverging from routed dispatch, OpenCode canonical-identity versus execution-syntax divergence, a non-atomic breaker debounce, and a post-send ACL failure that reclassified confirmed Telegram delivery. |
| 2 — Desired state | pass | One explicit Codex CLI-auth authority; canonical catalog identity with execution-boundary OpenCode mapping; private runtime-bound probes; serialized breaker-edge episodes; Switchboard-owned at-most-once delivery; explicit uncertain reissue; truthful UI facts. |
| 3 — Specification | pass | `proposal.md`, `design.md`, and nine capability deltas describe the behavior and limits. |
| 4 — Risks and constraints | pass | The design preserves schema isolation, does not expose or copy secret values, does not backfill historical pages, does not automatically resend ambiguous transport, and keeps probes separate from routed success provenance. |
| 5 — Work package | pass | `tasks.md` provides dependency-ordered implementation and verification work below. |
| 6 — Human sign-off | pending | Required before Bead creation or implementation. |

## Proposed Beads (draft only)

Create one P0 epic, **Harden runtime authentication and breaker attention**,
with the following dependency graph after approval:

```text
outbox schema + atomic outcome recorder ──┬── Switchboard outbox worker + route result
                                          ├── fleet-halt producer migration
explicit Codex CLI-auth authority ────────┼── private runtime-bound model probe
                                          │     └── Models API/UI truth surface
canonical-to-execution OpenCode mapping ──┘
all implementation leaves ─────────────────── final ACL/concurrency/e2e evidence
```

| Draft child | Priority | Depends on | Completion evidence |
|---|---:|---|---|
| Durable outbox schema and serialized dispatch recorder | P0 | — | real-Postgres migration, authorized producer, retention, and concurrent edge tests prove one episode |
| Explicit CLI-auth authority | P0 | — | multi-daemon/shared-home and unavailable-authority regressions pass without values in logs |
| Switchboard at-most-once worker and terminal route semantics | P0 | outbox schema/recorder | role-isolation, fenced claim/recovery, crash/uncertainty, and post-send ACL regressions pass |
| Fleet-halt outbox migration | P1 | outbox schema/recorder | one calendar-month episode, no direct ledger/audit debounce path |
| OpenCode execution mapping and private runtime-probe coordinator | P1 | Codex CLI-auth authority | canonical pricing identity, native invocation, private control, and no-breaker-reset probe tests pass |
| Models/Spend API and UI truth surfaces | P1 | worker and runtime probe | API/frontend tests cover independent states, unavailable state, and one reissue successor |
| Cross-boundary verification and deployment evidence | P0 | all above | exact-head test matrix, ACL proof, and authorized live runtime validation complete |

## Verification Matrix

| Concern | Required automated evidence | Authorized runtime evidence |
|---|---|---|
| Credential authority | unit/integration tests for explicit authority, local conflict, flat topology, shared-home writer order, rotation fencing, and safe logs | inspect provenance/metadata only; prove every daemon restores the same authority without reading values |
| Breaker episode edge | real-Postgres concurrent writer tests with equal timestamps and distinct attempt IDs, including failed half-open races | induce one safe controlled breaker edge only after deployment authorization; observe one durable episode |
| Delivery semantics | producer ACL/forgery tests, two-worker claim, fenced recovery/uncertainty, and post-send ACL route tests | verify sent/uncertain episode state and no automatic duplicate delivery |
| OpenCode and probe | adapter/API and real-Postgres tests for canonical Go identities with native execution arguments, fail-closed owner control, fixed-algorithm/time signed control, nonce-race/retention/replay denial, key rotation, generic-Secrets exclusion, and probe-no-reset behavior | use an actual runtime probe plus a separate routed session; compare their independent evidence |
| UI | API contract and frontend interaction tests for state separation, degraded observation, confirmation, fail-closed owner-control key states, and idempotent reissue | confirm Models/Spend surfaces reflect actual API state without a false success toast |

## Validation Record

- Baseline focused regression suite passed before design work: **95 passed**.
- `openspec validate harden-runtime-auth-and-breaker-attention --strict` passes.
- `git diff --check` passes for the tracked change package.
- Repository-wide `spec-trace-check.py --authoring` is not a usable clean gate:
  it reported **2,608 existing errors** across main specifications and unrelated
  active changes, and the tool has no scoped-change mode. This package uses
  unique requirement IDs and complete `ID`/`Source`/`Scope` metadata; its
  implementation tasks require test citations before completion.

## Sign-off Requested

Approve this package to create the proposed Beads and begin implementation in
the isolated worktree. The approval commits to the explicit at-most-once policy:
after ambiguous external transport, the system records `uncertain` and only a
confirmed operator action may create one new child episode.
