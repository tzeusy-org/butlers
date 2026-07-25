## 1. Shared reconciliation engine

- [x] 1.1 Extract `infra_conditions`' reconciliation logic into `butlers.core.condition_ledger`, parametrized by `table`.
- [x] 1.2 Re-point `infra_conditions.py` at the shared engine as a thin facade; verify every existing caller and test is unaffected.

## 2. Owner condition ledger

- [x] 2.1 Add `public.owner_conditions` (core_184), identical shape to `infra_conditions`.
- [x] 2.2 Add `butlers.core.owner_conditions` as a facade over `condition_ledger`.
- [x] 2.3 Add the Switchboard `reconcile_owner_condition` MCP tool (`OwnerConditionsBrokerModule`).
- [x] 2.4 Unit, integration (real Postgres), and migration regression tests.

## 3. Finance producer

- [x] 3.1 Reconcile overdue bills (`finance:bill-overdue`) into the owner condition ledger on every `run_insight_scan`.
- [x] 3.2 Reconcile monthly spending anomalies (`finance:spending-anomaly`) into the owner condition ledger.
- [x] 3.3 Best-effort, non-fatal to the existing insight-candidate delivery path; tests for open/resolve/degrade.

## 4. Dashboard surface

- [x] 4.1 Extend `GET /api/system/conditions` with a `ledger` query param (`infra` default, `owner`).
- [x] 4.2 Extend `StandingConditionsTile` to fetch and merge both ledgers, tagged per row, with independent degraded-source notes.
- [x] 4.3 Frontend unit tests, full `tsc -b` build, full `eslint .`, full `vitest run`.

## 5. Contract and verification

- [x] 5.1 Add the `owner-condition-ledger` capability delta and extend `system-overview-page` / `proactive-insight-engine` deltas.
- [ ] 5.2 Run `openspec validate --strict` on the changed specs.
- [ ] 5.3 Run backend lint/format/targeted tests and a full non-e2e pytest pass.

## 6. Deferred (reported as follow-up, not implemented here)

- [ ] 6.1 Calendar overcommitment radar emitting an owner condition N days out.
- [ ] 6.2 Ingestion-time threshold watchers (HA sensors, health vitals, spend running total) materializing conditions at the crossing.
