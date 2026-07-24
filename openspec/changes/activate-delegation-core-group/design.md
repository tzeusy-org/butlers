## Context

`activate-delegation-wake-loop` (bu-27dxl.5.1, merged) reserved the
`delegation` core-tool group at the daemon level (`core-daemon: Delegation
Core Tool Inventory And Admission Boundary`) and the four delegation tools
(`delegate_ask`/`delegate_receive`/`delegate_answer`/`delegate_wake`) are
already registered in code for every non-staffer butler
(`src/butlers/core_tools/_delegation.py`), gated behind that group.

That reservation was deliberately contract-only: the runtime-config API's
`KNOWN_CORE_GROUPS` allowlist (`src/butlers/api/routers/runtime_config.py`)
still rejects `delegation` with HTTP 422, so no butler can actually have it
enabled through the supported config surface. This change closes that last
gap so the already-implemented tools become reachable.

`core_groups` is DB-backed runtime config (`{schema}.runtime_config`),
seeded from `[butler.runtime_seed]` in `butler.toml` only on first boot. A
running deployment's effective config does not change when the toml seed
changes — it changes only via `PATCH /api/butlers/{name}/runtime-config`
followed by a daemon restart (`core_groups` is a cold field).

## Goals / Non-Goals

Goals:
- Accept `delegation` as a known `core_groups` value in the runtime-config
  API so it can be PATCHed onto any butler.
- Seed Finance and Relationship's `butler.toml` with `delegation` in
  `runtime_seed.core_groups` (additive — existing groups preserved) so
  freshly-provisioned instances of these two butlers pick it up automatically.
- Document, but not execute, the operator release path for the two
  butlers' already-running deployments.

Non-Goals:
- Changing delegation ledger/callback semantics (bu-27dxl.5.2, merged).
- Mutating the live production `runtime_config` rows — this change ships the
  validation + seed + docs; enabling it in the running system is a separate,
  explicit operator action.
- Activating `delegation` for any butler other than Finance and Relationship.

## Decisions

- **Additive seed, not seed replacement.** Finance's and Relationship's
  `runtime_seed.core_groups` gain `delegation` alongside their existing
  groups, matching how every other core group has been added to those seeds
  historically. Alternative considered: replacing the whole list — rejected,
  it would silently drop groups those butlers already depend on.
  [decision] additive seed edit over full-list replacement: preserves
  existing behavior for unrelated groups, smaller diff, reversible.
- **Validation-only code change, no live PATCH.** The worker cannot safely
  mutate the production `runtime_config` table from a worktree. The release
  path (PATCH via the dashboard API, then restart) is documented in this
  change's `tasks.md` and the worker's handoff report for the operator to
  execute deliberately.

## Risks / Trade-offs

- [Risk] Seeding `delegation` in `butler.toml` has no effect on an
  already-provisioned Finance/Relationship deployment (seed-vs-DB divergence)
  → Mitigation: documented PATCH-plus-restart release path; this is expected
  seed/DB behavior per `runtime_config.py`'s docstring, not a defect.
- [Risk] A stale contract test asserting the exact `KNOWN_CORE_GROUPS` set (or
  the runtime-config-api spec's known-groups scenario) breaks when
  `delegation` is added → Mitigation: update the affected test(s) as part of
  this change; run the full suite before handoff.

## Migration Plan

1. Land `delegation` in `KNOWN_CORE_GROUPS` and this spec delta.
2. Land the additive `butler.toml` seed change for Finance and Relationship.
3. Operator (post-merge): for each of Finance and Relationship,
   `PATCH /api/butlers/{name}/runtime-config` with `core_groups` set to the
   butler's existing groups plus `delegation`, then restart that butler's
   daemon.
4. Rollback: PATCH `core_groups` back to the prior list (without
   `delegation`) and restart; `delegation` remaining in `KNOWN_CORE_GROUPS`
   after rollback is harmless (an unused, but valid, group name).

## Open Questions

None.
