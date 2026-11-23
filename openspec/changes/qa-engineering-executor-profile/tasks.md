## 1. Launch profile

- [x] 1.1 Core-owned executor prompt, single-source provenance, fail-closed when missing.
- [x] 1.2 `Spawner._run` skips persona, memory and preambles for `qa`.

## 2. Fit and tier policy

- [x] 2.1 `ModelFeature.WORKSPACE_AGENT`, declared on the CLI adapters, `false` on the API adapter.
- [x] 2.2 `qa` intent requires it at `reversible`; exact `workhorse` rung; Gate 10 shares launch identity.
- [x] 2.3 `executor_env_unfit` leaves the catalog-entry breaker untouched.

## 3. Deferred

- [ ] 3.1 Action-needed condition for `executor_env_unfit` (needs a producer; follow-up bead).
- [ ] 3.2 Healing shares the profile (separate decision).
- [ ] 3.3 Hosted CI receipt (real-PostgreSQL routing tests run only in CI).
