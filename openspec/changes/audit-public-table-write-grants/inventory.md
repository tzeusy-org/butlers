# Public table x runtime-role writer inventory

Method: public tables enumerated from `alembic/versions/core/*` (`CREATE TABLE` and `ALTER TABLE public.*`) and `scripts/init-db.sql`. Writers found by a static scan of non-test `src/` and `roster/` Python for `INSERT INTO`, `UPDATE`, `DELETE FROM` against each table, then classified by execution context. `ON CONFLICT ... DO UPDATE` was checked on every `INSERT` site so that "append-only" claims do not hide an upsert. No database was available, so ACLs are read from the bootstrap and migrations, not from `pg_class.relacl`.

Principals: **D(role)** daemon under SET ROLE; **A** dashboard API, shared login, table owner, no SET ROLE; **C** `connector_writer`; **J** scheduled job (principal is the daemon that hosts it unless noted). Line numbers are in this branch's base (`origin/main`, 2026-10-03).

Effective ACL for every table below that has no row in "already fenced" (section 1.7): `SELECT, INSERT, UPDATE, DELETE` for all `butler_*_rw` roles and `connector_writer`, from `init-db.sql` L381, L409, L598, L637. Where a table is narrower, the row says how.

## 0. Findings about the existing spec and ACL model

1. The `database-security` matrix ("Public Schema Write Authorization Matrix") lists `public.contacts`, `public.contact_info` and `public.facts`. `contacts` was dropped by `core_134` (`DROP TABLE public.contacts`, L155) and `contact_info` by `core_115`; the core chain has no `CREATE TABLE public.facts`, only comments mention it (`roster/finance/tools/transactions.py:913`). The matrix lists them, so it is stale.
2. The scenario "Read-only public tables" says a role can only `SELECT` from tables outside the matrix. The bootstrap grants `INSERT, UPDATE, DELETE` on every public table to every runtime role (L381, L409). The scenario does not describe the effective ACL. Treat the matrix as documentation of intended writers, not as a description of enforced access.
3. `tests/config/test_schema_acl_isolation.py:424-447` asserts runtime-role `INSERT` on `qa_findings` and `qa_patrols`. A narrowing must change these cases on purpose.
4. The core chain replays once per butler schema, so migrations that add RLS or grants must be idempotent (as `core_255`, `core_257` are).

## 1. Summary

Legend for "Proposed": **M1** inline post-grant REVOKE in `init-db.sql`; **M2** ownership-transfer finalizer; **M3** RLS keyed to `current_user`; **leave** no change; **INSUFFICIENT** evidence does not support a proposal. All are options with a recommendation, not adopted.

### 1.1 QA family (analysed first)

| Table | Writers today (evidence) | Should write | Risk if a runtime role is compromised | Proposed (recommendation) |
| --- | --- | --- | --- | --- |
| `qa_patrols` | D(qa): `modules/qa/__init__.py:1585` I, `:1619` U, `:1650` I, `:662` U. A: `api/routers/qa.py:2991` I (synthetic patrol). No DELETE anywhere | `butler_qa_rw` (no DELETE) plus dashboard | Forge or mask a patrol row to hide the paging QA-overdue condition; forge `fleet_condition_handoff`; delete patrol history | **M1 recommended** (revoke I/U/D from all but `butler_qa_rw`, D from it too); M3 as alternative; leave is the current accepted risk. See `design.md` |
| `qa_findings` | D(qa): `core/qa/findings.py:80` I, `:130,165,196,252` U; `core/qa/dispatch.py:1049,1223` U; `modules/qa/__init__.py:1104` U. A: `api/routers/qa.py:3004` I (synthetic) | `butler_qa_rw` plus dashboard | Poison the dedup/dispatch queue, mark findings resolved or dispatched | Same as `qa_patrols`: M1 revoke I/U/D from non-QA roles. Verify `core/qa/*` is never run by another daemon before proposing |
| `qa_dismissals` | D(qa): `core/qa/dismissals.py:65` I, `:172` D. A: `api/routers/qa.py:2627` I, `:2667,3115` D | `butler_qa_rw` plus dashboard | Suppress findings by inserting dismissals | M1, same group |
| `qa_repo_config` | D(qa): `core/qa/repo_clone.py:86,95,104,219,232` U. A: `api/routers/qa.py:3469` U | `butler_qa_rw` plus dashboard | Redirect the clone path or repo URL | M1, same group |
| `qa_allowed_repositories` | A only: `api/routers/qa.py:3220` I, `:3264` U, `:3303` D | dashboard only | Widen the allowlist of repositories the QA butler may act on | **M1: revoke all DML from every runtime role.** No daemon writer found |
| `qa_investigation_events` | D(qa): `core/qa/journal.py:68,341` I (append-only) | `butler_qa_rw`, insert only | Forge or erase investigation history | M1: revoke U/D from all, I from non-QA roles |

### 1.2 Healing and fleet

| Table | Writers today | Should write | Risk | Proposed |
| --- | --- | --- | --- | --- |
| `healing_attempts` | D(any butler via spawner): `core/healing/tracking.py:211` I, `:377,715,735,756,995` U, `:1102` D; D(qa): `core/qa/dispatch.py:2430,2622,2989,3111,3143,3163,3189,3720` U; A: `api/routers/healing.py:539` I | every daemon (self-healing) plus dashboard | Open or close a healing attempt to trip or reset the circuit breaker | leave; legitimate writers span all roles. INSUFFICIENT to say which verbs each role needs without a runtime trace |
| `healing_attempt_sessions` | D(any): `core/healing/tracking.py:982` I, `:1051` U | every daemon | Falsify session linkage | leave |
| `healing_dispatch_events` | D(any): `core/healing/tracking.py:860` I only | every daemon, insert only | Erase dispatch history | M1: revoke U/D |
| `breaker_resets` | A only: `api/routers/healing.py:696` I, `api/routers/qa.py:3404` I | dashboard only (operator action) | Forge an operator breaker reset, re-enabling a tripped breaker | **M1: revoke all DML from runtime roles** (operator control; no daemon writer found) |
| `fleet_cases` | switchboard: `core/fleet_cases.py:151,631` I, `:343,365,543` U | switchboard | n/a | already fenced (FORCE RLS, `core_217:125-138`) |
| `fleet_case_links` | `core/fleet_cases.py:266` I | switchboard | n/a | already fenced (FORCE RLS, `core_217:209-220`) |
| `fleet_case_evidence` | `core/fleet_cases.py:191` I (DO NOTHING) | switchboard (siblings are fenced to it) | Attach forged evidence to a case | **M3** mirroring the sibling policy; confirm only switchboard runs `core/fleet_cases.py` |

### 1.3 Dashboard-only configuration and policy (no daemon writer found)

Static evidence shows the writers are in `api/` only, so a runtime-role revoke should not change behavior. Because some daemon code imports dashboard modules (design.md "Consequences" 2), each row needs a runtime proof before merge.

| Table | Writers today | Risk if a runtime role writes it | Proposed |
| --- | --- | --- | --- |
| `permissions` | A: `api/routers/permissions.py:206` I | Grant itself permissions | M1: revoke I/U/D from runtime roles |
| `approvals_policy` | A: `api/routers/approvals.py:2472` I. (`testing/approval_delivery_schema.py:19` is a test helper) | Weaken approval policy | M1 |
| `webhooks` | A: `api/routers/webhooks.py:383,598,729` U, `:486` I, `:655` D | Register or redirect a webhook | M1 |
| `channel_defaults` | A: `api/routers/channel_defaults.py:213` I | Redirect default notification channels | M1 |
| `model_catalog` | A: `api/routers/model_settings.py:728,814,897,941`; verify sweep uses the shared pool (`jobs/model_verify.py`, `api/routers/model_settings.py` `run_verify_all_models`) | Reroute model dispatch, tamper with verification state | M1. INSUFFICIENT on whether any daemon-side verify run exists; confirm `run_model_verify_sweep` runs on the shared login |
| `token_limits` | A: `api/routers/model_settings.py:1355,1370,1420,1428,1436` | Raise own limits | M1 |
| `butler_model_overrides` | A: `api/routers/model_settings.py:1611,1663` | Pin a butler to a chosen model | M1 |
| `provider_config` | A: `api/routers/provider_settings.py:209,269,302` | Alter provider config | M1 |
| `spend_ceiling` | A: `api/routers/spend.py:2277` | Raise own spend ceiling | M1 |
| `spend_rules` | A: `api/routers/spend.py:2033-2222`; J: `jobs/spend.py:353` U | Alter spend rules | INSUFFICIENT: the principal of `jobs/spend.py:353` is not established. Propose M1 only after confirming it |
| `priority_contacts` | A: `api/routers/priority_contacts.py:294,361` | Change who bypasses filtering | M1 |
| `timeline_saved_views` | A: `api/routers/timeline_saved_views.py:155,229,246` | Low (UI state) | leave (low value); M1 optional |
| `dismissed_issues` | A: `api/routers/issues.py:598,627` | Hide an issue from the owner | M1 |
| `butler_tools` | A: `api/routers/butler_management.py:575` I | Alter the tool registry view | M1 |
| `system_prompt_history` | A: `api/routers/butler_management.py:419` I | Forge prompt history | M1 |
| `memory_retention_policies` | A: `api/routers/memory.py:2877` I | Change retention | M1 |
| `butler_secrets` | A (shared pool): `api/routers/secrets_v2.py:6266,6784,7109,7231`, `api/routers/telegram_auth.py:222` (unqualified SQL) | Overwrite or delete secret metadata | M1. INSUFFICIENT: writers use unqualified names; confirm the table is the public one |
| `secret_probe_log` | A: `api/routers/secrets_v2.py:5714,5736,6590`, `api/routers/cli_auth.py:488,557`; J: `jobs/retention.py:480` D | Forge or erase probe evidence | M1 revoke I/U from runtime roles; D stays only if the retention job runs under a runtime role (INSUFFICIENT) |

### 1.4 Append-only evidence ledgers (no static UPDATE or DELETE writer)

Legitimate writers insert. The baseline also grants `UPDATE` and `DELETE`, which nothing uses. Proposal for all: **M1 revoke UPDATE, DELETE** (same shape as the `cost_claims` precedent, init-db L647-659). Preconditions per row: no `ON CONFLICT DO UPDATE` (checked) and no retention `DELETE` under a runtime role (checked for Python literals only; INSUFFICIENT for any SQL held in alembic jobs or constants).

| Table | Insert writers | Notes |
| --- | --- | --- |
| `audit_log` | A: `api/routers/audit.py:337`; D(any): `core/runtime_config.py:292`, `core/audit.py:68` (imports the API appender) | Mixed principals, insert only; forging or erasing audit rows is the main risk |
| `token_usage_ledger` | D(any): `core/dispatch_outcomes.py:385`, `core/model_routing.py:1459` | Spec already says INSERT only |
| `model_dispatch_attempts` | D(any): `core/dispatch_outcomes.py:374` | Spec already says SELECT, INSERT |
| `attention_ledger` | D/C/A: `core/attention_ledger.py:268`; callers in `core/scheduler.py`, `connectors/discretion.py`, `api/routers/notifications.py` | Three principal classes insert |
| `dispatch_failures` | D(any): `core/spawner.py:3665` | |
| `consolidation_runs` | D: `modules/memory/consolidation.py:567` | |
| `memory_compaction_log` | J: `scheduled_jobs.py:525` | |
| `delegation_wake_attempts` | D: `core/delegation_ledger.py:473` | |
| `deployments` | `core/deployments.py:327` I; callers `core/deploy.py`, `cli.py` | INSUFFICIENT on principal (CLI or deploy job); do not narrow before it is known |
| `domain_events` | D: `core/domain_events.py:104` | Retention path not found; confirm |

### 1.5 Mixed or broad legitimate writers (leave, or analyse separately)

| Table | Writers | Why not narrowed here |
| --- | --- | --- |
| `entities` | many: `identity.py:1207`, `owner_bootstrap.py:57`, `modules/memory/tools/entities.py:88,106,242`, `modules/contacts/backfill.py`, `roster/relationship/**`, `roster/education/tools/mind_map_nodes.py:107`, `google_account_registry.py:215`, `steam_account_registry.py:155`, `api/routers/memory.py` | Every butler legitimately creates entities. The `posture` column is already trigger-fenced (`core_256`); further column-level rules, if wanted, use M4 |
| `entity_info` | `credential_store.py:1344,1392`, `google_credentials.py:337,353,670`, `google_account_registry.py:393,746`, `steam_account_registry.py:366,415`, `api/routers/oauth.py:1731`, `api/routers/secrets_v2.py:5227,5375,5752`, `api/routers/spotify.py:863`, `roster/relationship/api/router.py:2698,2804,2855` | Holds credential-adjacent facts, so the poisoning risk is high, but the daemon-side writers (credential store, Google credentials) are not mapped to roles. INSUFFICIENT; needs its own bead |
| `google_accounts`, `steam_accounts` | registries, `modules/calendar.py`, `modules/google_drive`, `connectors/google_health.py:1520` (C), `api/routers/oauth.py`, `api/routers/steam.py` | Writers across D, C and A. Leave pending the entity_info analysis |
| `entity_graph_edges` | `core/entity_graph_edges.py` (D), `roster/relationship/tools/entity_merge.py:432,445` | Shared graph; leave |
| `ingestion_events` | D(switchboard): `roster/switchboard/tools/ingestion/ingest.py:1070`, `core/ingestion_events.py` U; C: `connectors/owntracks.py:408` D | Spec already lists it; leave |
| `memory_catalog` | `modules/memory/storage.py` many, `entity_rebind.py:122`, `roster/health/tools/conditions.py:410` | Every memory-bearing butler; leave |
| `model_round_robin_counters` | `core/model_routing.py:1347` | Every daemon; leave |
| `provider_allowance_states` | `core/model_routing.py:510,538` (merged PR #4351) | Every daemon needs I/U; **M1 revoke DELETE** is a cheap tightening |
| `domain_event_contracts`, `domain_event_reactions`, `domain_event_deliveries`, `butler_subscriptions` | `core/domain_event_contracts.py:466,475`, `core/domain_event_reactions.py:169`, `core/domain_events.py:185,210,291,351,387,419,469` | Daemon registration and delivery; principal per path unmapped. INSUFFICIENT |
| `delegation_ledger` | `core/delegation_ledger.py:129-445` | Daemon-side lifecycle; leave |
| `dashboard_conversations`, `dashboard_messages` | `api/conversations.py` (A) and daemon imports of it (`core/spawner.py:46`, `core_tools/_routing.py:1202`) | Mixed; leave |
| `dashboard_conversation_turns`, `dashboard_conversation_turn_sessions` | no static writer found | INSUFFICIENT |
| `attention_daily_rollup` | `core/attention_ledger.py:334`, `roster/switchboard/tools/insight/broker.py:869` (upsert) | Upsert from several callers; leave |
| `atmosphere_readings`, `atmosphere_feed_status`, `flight_status_feed_status` | `jobs/atmosphere.py`, `jobs/flight_status.py` (upserts) | Low value; leave |
| `provider_feature_catalogue` | `catalogue_bootstrap.py:208` (upsert) | INSUFFICIENT: bootstrap principal not established |
| `infra_conditions`, `owner_conditions`, `butler_reachability_conditions` | generic `core/condition_ledger.py:598,650,733,788` (`{table}` is a parameter); callers `core/infra_conditions.py`, `core/fleet_conditions.py`, `core/commitments.py`, `jobs/commitment_escalation.py` | These drive paging and owner attention, so they matter, but the table is a variable and the calling principal is not mapped. INSUFFICIENT; recommend its own audit |
| `state` | `core/state.py:76,176,193`, `identity.py:1098` (unqualified; per-schema `state` shadows `public.state`) | INSUFFICIENT: cannot tell which table the unqualified SQL targets |
| `contacts_dropbak`, `priority_contacts_dedup_bak_core_133` | no writer | Backup snapshots of dropped data. Confirm they still exist; if so, M1 revoke all DML |

### 1.6 Switchboard-owned insight tables

| Table | Writers | Proposed |
| --- | --- | --- |
| `insight_candidates` | D(switchboard): `roster/switchboard/tools/insight/broker.py:337` I, `:848` D; J: `jobs/retention.py:380` D | M3 switchboard policy like `insight_amendments`, after confirming the principal of `jobs/retention.py:380` (INSUFFICIENT) |
| `insight_cooldowns` | `broker.py:423,855` D, `:434,732` I | M3, same |
| `insight_engagement` | `broker.py:766` I, `:893` D | M3, same |
| `insight_settings` | `broker.py:198,1002` I | M3, same. Spec says "a butler" may write it; the only non-test writer is the switchboard broker |
| `insight_feedback` | `broker.py:406` I | already fenced (`core_241:99`) |
| `insight_amendments` | `broker.py:1843,1851,2476` | already fenced (RLS to `butler_switchboard_rw`, `core_255:223-235`, PR #4347) |

### 1.7 Already narrowed (newer pattern; no action)

| Table | Mechanism |
| --- | --- |
| `runtime_attention_outbox`, `runtime_attention_delivery_lease`, `runtime_attention_*control`, `runtime_attention_condition_episodes` | M2 finalizer, `init-db.sql` L4328 onward |
| `user_context`, `dnd_generation_guard`, `dnd_generation_mutations` | M2 plus FORCE RLS (`init-db.sql` L1381-2170) |
| `expected_signals` | M3 FORCE RLS keyed to producer (`core_210:92-107`) |
| `cost_claims`, `cost_claim_resolutions`, `cost_claim_events` | FORCE RLS (`core_239:223-224`) plus M1 `REVOKE DELETE` (`init-db.sql` L647-659) |
| `fleet_cases`, `fleet_case_links` | FORCE RLS to switchboard (`core_217`) |
| `task_continuity` | FORCE RLS (`core_229:108-123`) |
| `insight_feedback`, `insight_amendments` | RLS (above) |
| `entity_rebind_log` | RLS (`core_243:119`) |
| `entities.posture` (column) | trigger `core_256` |
| `metric_baselines`, `metric_deviation_episodes` (per-schema, PR #4355 open) | DML granted to the schema's own role only |

## 2. Tables with insufficient evidence (do not narrow until resolved)

`entity_info`, `google_accounts`, `steam_accounts`, `spend_rules`, `secret_probe_log` (retention delete), `butler_secrets` (unqualified SQL), `model_catalog` (verify sweep principal), `deployments`, `domain_event_*` and `butler_subscriptions`, `dashboard_conversation_turns`, `dashboard_conversation_turn_sessions`, `infra_conditions`, `owner_conditions`, `butler_reachability_conditions`, `state`, `provider_feature_catalogue`, `insight_candidates` (retention principal), `contacts_dropbak`, `priority_contacts_dedup_bak_core_133`.

The static scan cannot see: SQL built from f-string table variables other than the condition ledger, SQL in non-Python files, stored procedures, or triggers that write on a caller's behalf. Each follow-up bead must establish its writer set at runtime.
