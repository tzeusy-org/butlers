## Context

### Who writes, as which database principal

| Context | Principal | SET ROLE | Evidence |
| --- | --- | --- | --- |
| Butler daemon (core, modules, roster tools and jobs) | `butler_<schema>_rw` | yes | `src/butlers/lifecycle.py:120` sets `daemon.db.role`; `src/butlers/db.py:442-446` runs `SET ROLE` on every acquire |
| Connector process | `connector_writer` | yes | `src/butlers/connectors/db_role.py:13` |
| Dashboard API (`src/butlers/api/**`, `roster/*/api/router.py`) | shared migration login, which owns the tables | no | `src/butlers/api/deps.py:518-520` ("SET ROLE is therefore always disabled for API-managed pools"); `qa.py:212-220` uses `db.credential_shared_pool()` |

Consequences that shape every option below:

1. A `REVOKE` or RLS policy aimed at the runtime roles does not affect the dashboard API, because the shared login owns the tables. A writer that is dashboard-only keeps working under any runtime-role narrowing. `ENABLE ROW LEVEL SECURITY` also leaves the owner unaffected; `FORCE ROW LEVEL SECURITY` binds the owner too and therefore needs a policy for the dashboard writer.
2. Some daemon code imports dashboard modules (`core/spawner.py:46` and `core_tools/_routing.py:1202` import `api.conversations`; `core/audit.py:68` and `jobs/decision_review.py:111` import `api.routers.audit`). "Lives under `api/`" therefore does not prove "dashboard-only". The inventory marks such tables as mixed.
3. Runtime roles are created `LOGIN` but are "normally used through SET ROLE rather than direct logins" (init-db L183-188). The shared login is a member of each and can `SET ROLE`; a process that holds the shared credentials can also `RESET ROLE`. Narrowing therefore protects against defects and confined SQL (a module bug, a tool that interpolates SQL, a role-scoped session), not against theft of the shared credentials. That is the boundary behind bu-w24vr3 option B, and this proposal does not move it.

### Why a migration REVOKE alone does not persist

`scripts/init-db.sql` is "safe to re-run" and is rerun on every bootstrap. Each run executes, inside the privileged `DO` block:

- `GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO <role>` for each runtime role (L381) and for `connector_writer` (L598). This re-grants every table that already exists, undoing any earlier `REVOKE`.
- `ALTER DEFAULT PRIVILEGES FOR ROLE <migration user> IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES` (L409, L637), so a table created later starts fully granted.

A narrowing must therefore run after those grants, on every bootstrap, or be expressed as something the grants do not touch (row-level security policies, triggers, ownership).

## Mechanisms already used in the repo

| # | Mechanism | Precedent | Survives rerun because | Cost |
| --- | --- | --- | --- | --- |
| M1 | Inline post-grant `REVOKE` in the bootstrap `DO` block, guarded by `to_regclass`, looped over `_all_runtime_roles` and `connector_writer` | `cost_claims` family: `init-db.sql` L647-659 (`REVOKE DELETE`), with FORCE RLS in `core_239:223-224` | It runs after the L381/L598 grants on every rerun | Smallest change. On a first install the table does not exist at bootstrap time, so the revoke applies only on the next bootstrap run after migrations. Needs a stated operational rule or a post-migration hook |
| M2 | Ownership-transfer finalizer: dedicated `NOLOGIN` owner role, `SECURITY DEFINER` finalizer, `REVOKE ... FROM PUBLIC`, sweep of unlisted grantees, explicit re-grant to the allowed role | `runtime_attention_outbox`: `runtime_attention_admin.finalize_interface()` at L4328, ownership moves at L4406-4412, revokes at L4521-4590. (L2872 is the operator-upgrader that the finalizer consumes, not the ACL itself.) `public.user_context` and the DND tables use the same shape, L1381 onward | Default privileges are registered `FOR ROLE <migration user>`, so a table owned by another role is outside them; the finalizer rewrites the ACL each run | Heaviest. A new owner role, admin schema, versioned interface and rollback function. Also removes the shared login's owner bypass, which the dashboard writer would then need a policy for |
| M3 | Row-level security keyed to `current_user` | `expected_signals` `core_210:92-107` (FORCE, producer-owned rows); `fleet_cases` and `fleet_case_links` `core_217:125-138` (FORCE, switchboard only); `insight_amendments` `core_255:223-235` (ENABLE, switchboard only); `insight_feedback` `core_241:99` (ENABLE) | Policies are not privileges; `init-db.sql` never touches them | Lives in a core migration, needs no `init-db.sql` change. Privileges stay broad, so a missed policy path fails open. `ENABLE` (owner bypass) suits tables the dashboard also writes; `FORCE` suits tables it does not |
| M4 | Trigger guard on a column | `core_256` `trg_entities_posture_writer` on `public.entities` | Triggers are not privileges | Only for column-level rules on a table every role legitimately writes |

Newer per-schema tables avoid the problem by not being public: PR #4355 (open, `core_258_personal_baselines`) creates `metric_baselines` and `metric_deviation_episodes` per schema and grants DML to `butler_<current_schema>_rw` only. That PR is not merged and was read from its diff only; re-check after it lands. `core_257` (`public.provider_allowance_states`, merged) grants `SELECT, INSERT, UPDATE` to roles but does not stop the default privileges from also granting `DELETE`, which is the general gap this audit addresses.

M1 and M3 compose: M3 pins who may write which rows, M1 removes verbs nobody uses (`DELETE`, `UPDATE` on append-only ledgers).

## Constraints any narrowing must meet

- Persist across `init-db.sql` reruns and across `ALTER DEFAULT PRIVILEGES` (above).
- Leave every legitimate writer in `inventory.md` working. For `qa_patrols` that is two writers: the QA daemon (`butler_qa_rw`) and the dashboard synthetic-finding hook.
- Keep dev stacks without runtime roles working: roles are created by bootstrap and `Database.connect()` fails open without them (`spec.md` "Graceful Fallback Policy"). Policies keyed to a role name must not lock out the owner login in that case, which `ENABLE` (not `FORCE`) guarantees.
- Be proven against a real Postgres. `tests/config/test_schema_acl_isolation.py:424-447` asserts today that runtime roles can `INSERT` into `public.qa_patrols` and `public.qa_findings`; any narrowing must update those cases deliberately.
- Be reported honestly: static analysis found the writers listed, but dynamic SQL, ORMs or `_TABLE` constants can hide others. Each follow-up bead must prove its writer set at runtime (for example by running the bootstrap on a dev database and the writer's tests with the revoke applied) before it merges.

## qa_patrols (first candidate)

`public.qa_patrols` is the evidence behind the paging "QA patrol overdue" condition (`core/qa/patrol_provenance.py:48,62`, `core/fleet_conditions.py:102`) and records the handoff mode a patrol ran with (`fleet_condition_handoff`, core_254). A runtime role that can `INSERT` a qualifying row can mask the overdue page; `UPDATE` can forge the handoff mode or resolve a row; `DELETE` can erase patrols. The spec records this as accepted risk (database-security, "qa_patrols write access is an accepted trust boundary").

Writers today:

| Writer | Principal | Evidence |
| --- | --- | --- |
| QA patrol start | `butler_qa_rw` (QA module loads only in `roster/qa/butler.toml`) | `modules/qa/__init__.py:1585` INSERT |
| QA patrol completion | `butler_qa_rw` | `modules/qa/__init__.py:1619` UPDATE |
| QA overlap skip | `butler_qa_rw` | `modules/qa/__init__.py:1650` INSERT |
| QA stale-row recovery on restart | `butler_qa_rw` | `modules/qa/__init__.py:662` UPDATE |
| Dashboard synthetic finding hook (must be preserved) | shared login, no SET ROLE | `api/routers/qa.py:2991` INSERT (origin `operator_synthetic`, status `suppressed`), then `qa.py:3004` INSERT into `qa_findings`, behind `_synthetic_findings_enabled()` |
| Any `DELETE` | none | no `DELETE FROM public.qa_patrols` in `src/` or `roster/` |

Readers (unaffected by any write narrowing): `api/routers/dashboard_briefing.py:557`, `api/routers/qa.py:1484,1505,1672,1730,1874,1879,1910,1969,2720,2750`, `core/qa/dispatch.py:153`, `core/qa/patrol_provenance.py:48,62`, `core/fleet_conditions.py:102`.

Options:

- O0, leave as is. Matches the recorded owner decision. The residual risk stays as written in the spec.
- O1 (recommended): M1, inline post-grant revoke in `init-db.sql`. `REVOKE INSERT, UPDATE, DELETE` on `public.qa_patrols` from every role in `_all_runtime_roles` and `connector_writer` except `butler_qa_rw`, and `REVOKE DELETE` from `butler_qa_rw` too (nothing deletes). The dashboard hook is unaffected because the shared login owns the table. Smallest diff, same shape as `cost_claims`, no new roles. Operational rule needed: bootstrap must rerun after the migration that creates the table (already the case for `cost_claims`).
- O2, M3 `ENABLE ROW LEVEL SECURITY`, policy `SELECT USING (true)` plus `INSERT/UPDATE/DELETE` limited to `current_user = 'butler_qa_rw'`, in a core migration. Survives reruns with no `init-db.sql` change and is idempotent across the per-schema migration replay. Do not use `FORCE`: it would bind the owner login and break `qa.py:2991` unless a policy for the shared login is added. Weaker than O1 if a future table path forgets the policy, because the verbs stay granted.
- O3, M1 plus M3 together. Defense in depth; two places to keep consistent.

Why O1 over O2: the verbs are known and few, the writer set is one role plus the owner, and the repo already trusts this shape. O2 is the fallback if the owner wants the fence in the migration chain rather than the privileged bootstrap.

Preserving `qa.py`: neither option touches the shared login. Verification for the follow-up bead: exercise `POST /api/qa/dev/synthetic-findings` against a database after the revoke or policy, and assert the `butler_qa_rw` start/complete/skip/recover paths still succeed while a different runtime role gets `permission denied` (or an RLS violation).

## Recommendation summary

See `inventory.md` section 1 for the per-table table. In short:

- Narrow with M1, no daemon impact expected, after runtime proof: tables whose only static writer is the dashboard API (`qa_allowed_repositories`, `breaker_resets`, `permissions`, `webhooks`, `approvals_policy`, `channel_defaults`, `model_catalog`, `token_limits`, `butler_model_overrides`, `provider_config`, `spend_ceiling`, `priority_contacts`, `timeline_saved_views`, `dismissed_issues`, `butler_tools`, `system_prompt_history`, `memory_retention_policies`, `butler_secrets`): revoke all DML from runtime roles.
- Narrow with M1, append-only: ledgers with no static UPDATE or DELETE writer: revoke `UPDATE, DELETE`.
- Narrow with M1 or M3 for the QA family, to `butler_qa_rw` plus the dashboard.
- Leave as is, or analyse separately: identity and credential-bearing tables, whose legitimate writers are broad (`entities`, `entity_info`, `google_accounts`, `steam_accounts`), and tables already fenced.
- Insufficient evidence is stated per table, not guessed.

Nothing here is adopted. Each row is an option plus a recommendation for the owner.

## Risks and trade-offs

- A too-eager revoke breaks a daemon write that static scanning missed. Mitigation: runtime proof per table before merge, and narrowing in small batches by group.
- Mechanism sprawl: M1 rows in one `DO` block are easy to read; a table-by-table M2 is costly. The proposal recommends M2 for none of the candidates.
- The `database-security` matrix is stale (inventory section 0). Narrowing should be accompanied by a reconciliation of the matrix, not by silently widening or shrinking it.
