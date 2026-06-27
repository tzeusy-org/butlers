# Butlers Review-Remediation Improvement Cycles — Program Report

**Program epic:** `bu-dl98i` — *Review-remediation improvement cycles*
**Status:** All cycle epics closed; all child work shipped to `main`.
**Report date:** 2026-06-19

## Overview

This program tracked the remediation of an external code-review/audit of the
Butlers system, re-verified against `HEAD`. The audit's findings were grouped
into six improvement cycles (Cycle 0 through Cycle 5), mirrored as child epics
`bu-dl98i.1`–`bu-dl98i.7`. Four cycles were backed by OpenSpec changes (now
landed and archived — see [Spec application](#spec-application)); the remainder
were beads-only impl/doc reconciliation against existing doctrine and specs.

Every cycle epic is **closed** and every child bead is **closed/shipped**. The
sections below record each cycle's outcome, the PRs that delivered it, and the
findings-resolution status.

## Per-cycle outcomes

### Cycle 0 — Harden secrets + dashboard honesty (`bu-dl98i.1`, closed)

Spec source: `harden-secrets-and-dashboard-honesty` (spec `dashboard-admin-gateway`).

| Bead | Outcome | PR |
| --- | --- | --- |
| `bu-dl98i.1.1` | Remove legacy raw-secret reveal endpoint + router/frontend callers | #2417 |
| `bu-dl98i.1.2` | Route-introspection contract test forbidding raw-secret reveal routes | #2424 |
| `bu-dl98i.1.3` | Keep opt-in defense-in-depth API-key auth + tests | #2426 |
| `bu-dl98i.1.4` | Honest auth-status health indicator | #2432 |
| `bu-dl98i.1.5` | Export signer refuses dev-secret default outside dev | #2423 |
| `bu-dl98i.1.6` | Gen-1 spec-to-code reconciliation (read-only audit) | — (zero gaps) |

### Cycle 1 — Reproducible builds + pinned runtime (`bu-dl98i.2`, closed)

Spec source: `reproducible-builds-and-pinned-runtime` (spec `build-reproducibility`).

| Bead | Outcome | PR |
| --- | --- | --- |
| `bu-dl98i.2.1` | Commit `uv.lock` + fix stale pyproject comment | #2415 |
| `bu-dl98i.2.2` | Frozen/locked sync in CI and Docker + stale-lock check | #2416 |
| `bu-dl98i.2.3` | Pin LLM CLI / Node supply chain + version manifest | #2419 |
| `bu-dl98i.2.4` | Pin floating service images + bump procedure | #2447 |
| `bu-dl98i.2.5` | Gen-1 spec-to-code reconciliation (read-only audit) | — (satisfied) |

### Cycle — Wire proactive insight delivery (`bu-dl98i.3`, closed)

Beads-only (the `insight-delivery` spec was already complete; impl-only drift).

| Bead | Outcome | PR |
| --- | --- | --- |
| `bu-dl98i.3.1` | Wire durable notify path into switchboard insight-delivery cycle | #2420 |
| `bu-dl98i.3.2` | Spec-coverage tests for insight-delivery paths | #2427 |
| `bu-dl98i.3.3` | Surface insight-delivery state on the dashboard | #2435 |
| `bu-dl98i.3.4` | Gen-1 spec-to-code reconciliation (read-only audit) | — (satisfied) |

### Cycle 2 — Truth & docs cleanup (`bu-dl98i.4`, closed)

Beads-only (doctrine `about/craft-and-care/review-and-documentation`).

| Bead | Outcome | PR |
| --- | --- | --- |
| `bu-dl98i.4.1` | Reword pyproject description away from "AI agent framework" | #2446 |
| `bu-dl98i.4.2` | Add `about/heart-and-soul/v1-status.md` evidence matrix | #2441 |
| `bu-dl98i.4.3` | Reconcile relationship interaction-sync docs vs jobs | #2444 |
| `bu-dl98i.4.4` | Single authoritative credential-fallback explanation | #2443 |
| `bu-dl98i.4.5` | Gen-1 spec-to-code reconciliation (read-only audit) | — (satisfied) |
| `bu-dl98i.4.6` | Doc-code drift CI guards (3 invariants) | #2448 |

### Cycle 3 — Operational smoke tests (`bu-dl98i.5`, closed)

Spec source: `operational-smoke-tests` (spec `testing` delta).

| Bead | Outcome | PR |
| --- | --- | --- |
| `bu-dl98i.5.1` | Smoke test tier scaffolding/marker | #2418 |
| `bu-dl98i.5.2` | Clean-start + dashboard-health smoke tests | #2429 |
| `bu-dl98i.5.3` | Migration smoke test (empty→head + round-trip) | #2428 |
| `bu-dl98i.5.4` | Daemon lifecycle + route-inbox recovery smoke tests | #2431 |
| `bu-dl98i.5.5` | CI fast smoke gate + release evidence | #2439 |
| `bu-dl98i.5.6` | Gen-1 spec-to-code reconciliation (read-only audit) | — (satisfied) |

### Cycle 4 — Hardened deployment defaults (`bu-dl98i.6`, closed)

Spec source: `hardened-deployment-defaults` (spec `deployment-hardening`).

| Bead | Outcome | PR |
| --- | --- | --- |
| `bu-dl98i.6.1` | Deployment posture profile + Grafana anon-viewer gating | #2461 |
| `bu-dl98i.6.2` | Infra creds via secret indirection + known-default detection | #2472 |
| `bu-dl98i.6.3` | Degraded-safety indicator for insecure defaults | #2475 |
| `bu-dl98i.6.4` | Gen-1 spec-to-code reconciliation (gap closed by 6.5) | — |
| `bu-dl98i.6.5` | Strict DB-role + permission-gate fail-closed enforcement | #2470 |
| `bu-dl98i.6.6` | Backup/restore verification drill for PostgreSQL data plane | #2468 |

### Cycle 5 — Maintainability decomposition (`bu-dl98i.7`, closed)

| Bead | Outcome | PR |
| --- | --- | --- |
| `bu-dl98i.7.1` | Split `spawner.py` into internal seams | #2483 |
| `bu-dl98i.7.2` | Dependency-direction contract tests | #2454 |
| `bu-dl98i.7.3` | Module-boundary contract tests | #2455 |
| `bu-dl98i.7.4` | Gen-1 reconciliation (contract-test vacuity fix) | #2493 |
| `bu-dl98i.7.5` | Versioned read-models/DTOs for dashboard direct-DB fan-out | #2484 |
| `bu-dl98i.7.6` | Retention/index/query-budget discipline for long-lived data | #2490 |

## Spec application

The four spec-backed cycles cited OpenSpec changes that originally lived on the
`review-remediation-specs` branch (**PR #2412**). That branch PR was **closed as
superseded**. The spec deltas were instead landed on `main` via **PR #2507**
(*docs(openspec): land review-remediation cycle specs + archive completed
changes*, merged 2026-06-19), which synced the capability specs and archived the
completed changes.

**Capability specs synced to `openspec/specs/` (verified present):**

- `build-reproducibility` — Cycle 1
- `deployment-hardening` — Cycle 4
- `dashboard-admin-gateway` — Cycle 0
- `testing` (smoke-tier delta) — Cycle 3

**Changes archived under `openspec/changes/archive/` (verified present):**

- `2026-06-19-harden-secrets-and-dashboard-honesty`
- `2026-06-19-reproducible-builds-and-pinned-runtime`
- `2026-06-19-operational-smoke-tests`
- `2026-06-19-hardened-deployment-defaults`

This confirms the PR #2412 spec content was applied to `main` (via #2507) rather
than abandoned.

## Findings-resolution table

Each cycle carried a gen-1 spec-to-code reconciliation child (`*.N` beads above)
whose job was to falsify the cycle's claims against `HEAD`. All reconciliation
children are **closed** with their findings either confirmed satisfied or
remediated by a follow-up bead within the same cycle.

| Finding area | Cycle | Resolution | Evidence |
| --- | --- | --- | --- |
| Plaintext secret reveal endpoint | 0 | RESOLVED | #2417, contract test #2424 |
| Weak/absent dashboard auth honesty | 0 | RESOLVED | #2432, #2426, #2423 |
| Non-reproducible builds / unpinned runtime | 1 | RESOLVED | #2415, #2416, #2419, #2447 |
| Proactive-insight promise not delivered | — | RESOLVED | #2420, #2427, #2435 |
| "AI agent framework" mis-description + doc drift | 2 | RESOLVED | #2446, #2441, #2444, #2443, #2448 |
| No operational smoke-test tier | 3 | RESOLVED | #2418, #2429, #2428, #2431, #2439 |
| Insecure deployment defaults | 4 | RESOLVED | #2461, #2472, #2475, #2470, #2468 |
| Maintainability / boundary erosion | 5 | RESOLVED | #2483, #2454, #2455, #2493, #2484, #2490 |

**All verified review findings are resolved.** Every cycle epic and child bead
is closed, and the spec deltas backing the spec-bearing cycles are live on
`main`.

## Conscious deferrals / out-of-scope

- **`public.contacts` retirement (`bu-oluyt`)** — the owner-gated retirement of
  the vestigial `public.contacts` / contact-store split is tracked **separately**
  and is **out of scope** for this review-remediation program. It is a distinct
  tracked effort, not a deferral of any cycle finding here.

## Program closure

The only open item under `bu-dl98i` is this program-report bead (`bu-dl98i.8`).
With the report landed, `bu-dl98i` itself is closeable. Per program protocol,
the epic close is left to the coordinator rather than performed by this report.
