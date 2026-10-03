## 1. Audit and proposal (this change)

- [x] 1.1 Derive the public table x runtime-role writer inventory with file:line evidence (`inventory.md`).
- [x] 1.2 Analyse `qa_patrols` first, naming the QA daemon writer and the `api/routers/qa.py` synthetic-finding writer.
- [x] 1.3 Document why a migration `REVOKE` alone does not persist (`design.md`).
- [x] 1.4 Present options and a recommendation per table; adopt none.
- [x] 1.5 State where evidence is insufficient (`inventory.md` section 2).

## 2. Owner decision (not started)

- [ ] 2.1 Owner chooses a posture per group in `inventory.md` section 1: leave, M1, M2 or M3.
- [ ] 2.2 Owner decides whether to reconcile the stale `database-security` write matrix and its "Read-only public tables" scenario.

## 3. Proposed follow-up beads (text only; file after 2.1, not before)

Each narrowing bead must: change only its group's tables; prove the writer set at runtime on a dev database (bootstrap rerun, then the writer's tests and an attempted write from a non-writer role); update `tests/config/test_schema_acl_isolation.py` deliberately; keep `openspec validate --strict` green; and use ADDED or complete requirement text, never a partial MODIFIED block.

- [ ] 3.1 Narrow `qa_patrols` (recommended M1). Preserve `modules/qa/__init__.py` writer paths and `api/routers/qa.py:2991`. Verify `POST /api/qa/dev/synthetic-findings` still succeeds.
- [ ] 3.2 Narrow the rest of the QA family: `qa_findings`, `qa_dismissals`, `qa_repo_config`, `qa_investigation_events`, `qa_allowed_repositories`, and `breaker_resets`.
- [ ] 3.3 Revoke runtime-role DML on dashboard-only configuration and policy tables (`inventory.md` 1.3), in batches: policy and routing (`permissions`, `approvals_policy`, `webhooks`, `token_limits`, `butler_model_overrides`, `provider_config`, `spend_ceiling`, `priority_contacts`), then the rest. Tables with insufficient evidence (`model_catalog`, `butler_secrets`, `spend_rules`, `secret_probe_log`) are excluded; see 3.6.
- [ ] 3.4 Revoke `UPDATE, DELETE` on append-only ledgers (`inventory.md` 1.4), starting with `audit_log`, `token_usage_ledger`, `model_dispatch_attempts`, `attention_ledger`.
- [ ] 3.5 Fence `fleet_case_evidence`, `insight_cooldowns`, `insight_engagement` and `insight_settings` with RLS matching their siblings. `insight_candidates` is excluded until its retention principal is known (3.6).
- [ ] 3.6 Resolve the insufficient-evidence tables (`inventory.md` section 2), one analysis bead per cluster: identity and credential tables (`entity_info`, `google_accounts`, `steam_accounts`); dashboard config tables awaiting a principal (`model_catalog`, `butler_secrets`, `spend_rules`, `secret_probe_log`); backup tables (`contacts_dropbak`, `priority_contacts_dedup_bak_core_133`); `state`, `provider_feature_catalogue`, `insight_candidates`; condition ledgers (`infra_conditions`, `owner_conditions`, `butler_reachability_conditions`); domain events; deployments.
- [ ] 3.7 Reconcile `database-security`: drop `contacts`, `contact_info`, `facts` from the matrix and correct "Read-only public tables" to the effective ACL.
- [ ] 3.8 Add a guard test that fails when a new public table is created without an inventory entry or an explicit "broad" classification.
