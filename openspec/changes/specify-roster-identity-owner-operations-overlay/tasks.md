## 1. Draft the contract

- [x] 1.1 Refresh the prompt-source, API, database-role, direct-SQL, filesystem-helper, and MCP-writer inventory at baseline `63de6168fc852fcbf4e8fc24717585f64df0e2a5`.
- [x] 1.2 Record Travel's merged shared-include repair and QA's approved staffer exception without changing either roster prompt.
- [x] 1.3 Define complete composition, invalid-roster, owner-control, concurrency, privacy, database-isolation, migration, and rollback scenarios across the seven governing capabilities.
- [x] 1.4 Re-scan active same-named requirements and avoid `core-skills / AGENTS.md Read/Write Access`, whose two existing whole-body owners remain separately sequenced.

## 2. Exact-artifact review and adoption gates

- [ ] 2.1 Obtain independent semantic and security review of the exact draft commit; any semantic correction requires a fresh exact-head review.
- [ ] 2.2 Obtain separate owner adoption naming the exact independently reviewed commit. The direction approval in `bu-f5146g` is not exact-artifact adoption.
- [ ] 2.3 Keep implementation, schema/grant changes, credential provisioning, runtime access, deployment, archive, merge, and release blocked until their own authority gates pass.

## 3. Future storage and role foundation after adoption

- [ ] 3.1 Add a forward core migration that preserves every prompt-history row, backfills explicit `legacy_full_replacement` provenance, seeds existing agents into one-way `precutover_legacy_hold`, and starts later agents in `roster_overlay`.
- [ ] 3.2 Create the non-login table owner and direct-login prompt writer with the exact `NOINHERIT` and `NOBYPASSRLS` attributes from the database-security delta.
- [ ] 3.3 Add fixed runtime-role-to-agent RLS read policies plus writer-only INSERT policy; leave backup/migration authority on its separately governed operational path.
- [ ] 3.4 Provision the dedicated writer credential and pool plus a separate non-application migration/backup principal; make the generic dashboard login `NOSUPERUSER NOBYPASSRLS` before privilege cutover.
- [ ] 3.5 Transfer both history tables to the non-login owner, revoke historic table and sequence DML from `PUBLIC`, runtime roles, connectors, and the generic API login, then update bootstrap/finalizer reconciliation so reruns preserve the restriction.

## 4. Future roster and composition implementation after adoption

- [ ] 4.1 Make the shared core-skills seam resolve recursive roster-confined bare references and report missing, escaping, cyclic, or unreadable required identity graphs as fixed invalid-roster failures.
- [ ] 4.2 Retire the generated generic prompt fallback for admitted roster agents and prove a database row cannot substitute for invalid identity.
- [ ] 4.3 Read overlays through the agent-local runtime-role pool rather than the generic shared pool, yielding roster-only composition when role enforcement is unavailable.
- [ ] 4.4 Compose the eight authorized layers in the specified order with exact separators, literal delimited overlay handling, and no adapter-owned reference expansion or prompt additions.
- [ ] 4.5 Preserve each dynamic layer's specified availability posture and emit content-blind evidence when the overlay store is unavailable.

## 5. Future owner-overlay API after adoption

- [ ] 5.1 Convert the current prompt GET, PUT, and history routes to the field-by-field identity/overlay/provenance representations in the dashboard capability delta.
- [ ] 5.2 Require fail-closed owner control before body, pool, roster, or protected-state access; use `authenticated_principal()` only for post-admission attribution.
- [ ] 5.3 Route all prompt reads and writes through the dedicated direct-login pool and reject unknown roster agents before acquiring that pool.
- [ ] 5.4 Implement bounded literal overlay validation, reserved-delimiter rejection, per-agent compare-and-swap serialization, identical-update no-op behavior, and atomic `butler.prompt_overlay_set` persistence with the exact metadata allowlist.
- [ ] 5.5 Update the dashboard editor to distinguish roster identity from the mutable overlay and require explicit confirmation of the natural-language semantic-limit warning.
- [ ] 5.6 Add the owner-only composition-mode route over append-only mode history with compare-and-swap, identity-replacement acknowledgement, approved-window enforcement, and atomic `butler.prompt_mode_changed` evidence.
- [ ] 5.7 Read the rollback deadline only from fail-closed `BUTLERS_PROMPT_LEGACY_ROLLBACK_UNTIL`; reject request/DB/MCP/runtime authority to open it, reject `precutover_legacy_hold` as an API target, and keep the target-mode exit available after expiry.

## 6. Future roster governance after adoption

- [ ] 6.1 Extend roster validation so domain butlers require the first-line shared include and newly added or substantively changed staffers require an explicit infrastructure-contract opt-in or opt-out.
- [ ] 6.2 Record the existing Concierge, Messenger, and Switchboard shared-instruction opt-ins in their infrastructure contracts and capability projections without changing their prompt bytes.
- [ ] 6.3 Add QA's approved opt-out to its infrastructure-contract MANIFESTO and capability projection while keeping `roster/qa/CLAUDE.md` and `roster/qa/AGENTS.md` byte-identical.
- [ ] 6.4 Preserve Travel's already-merged shared include and all existing staffer opt-ins; do not create a new Travel repair or infer policy from file shape.

## 7. Future verification after adoption

- [ ] 7.1 Extend `tests/features/test_skills.py` for recursive bare references, invalid identity graphs, literal overlay include-like text, shared snippets, and generated-fallback retirement.
- [ ] 7.2 Extend `tests/core/test_core_spawner.py` and `tests/core/test_core_spawner_context.py` for target-mode layer order including currency and measurement system, exact separators, unavailable-overlay behavior, both bounded legacy modes, expired-rollback pre-adapter denial, closed-source rejection, and byte-identical adapter handoff.
- [ ] 7.3 Extend `tests/api/test_butler_management.py` for 503/401 pre-access denial, unknown-agent pre-pool rejection, bounded validation, owner-only projections, overlay/mode compare-and-swap races, fail-closed rollback-window parsing, one-way pre-cutover hold, no-op retries, exact audit actions, and content-blind evidence.
- [ ] 7.4 Extend `tests/config/test_roster_claude_md_include.py` and `tests/config/test_delegation_guidance_reachability.py` to classify domain butlers and staffers from roster configuration, positively pin Travel conformance, and pin QA's explicit governance opt-out without asserting changed QA prompt bytes.
- [ ] 7.5 Add `tests/migrations/test_core_prompt_overlay_authority.py` using direct-login writer, actual no-`SET ROLE` generic API, connector, mapped runtime, unmapped runtime, table-owner, sequence, both history tables, and bootstrap-rerun contexts.
- [ ] 7.6 Add privacy absence-sentinel coverage proving roster and overlay bytes never enter denied/conflict/error bodies, audit notes, logs, metric labels, or span attributes, with positive allowlist assertions so empty evidence cannot pass vacuously.
- [ ] 7.7 Run targeted nodes first, topology collection where applicable, migration/contract lanes, `make check-guards`, strict OpenSpec and overwrite checks, diff hygiene, fresh exact-head semantic/security review, and terminal hosted CI. Report the implementation test delta separately.

## 8. Future staged cutover and rollback after adoption

- [ ] 8.1 Ship and verify the compatibility release before the additive migration, credential provisioning, behavior cutover, or privilege cutover.
- [ ] 8.2 Complete ownership transfer, RLS enablement, historic grant revocation, direct-login verification, and bootstrap-rerun verification before any overlay becomes load-bearing.
- [ ] 8.3 Require explicit owner review of each legacy head before creating or activating an operations overlay; never infer or copy identity/operations text during migration.
- [ ] 8.4 Cut over prepared agents individually through the owner-only mode route, recording only the specified audit metadata.
- [ ] 8.5 Retain the owner-gated compare-and-swap legacy selector during the approved observation window without deleting history or restoring broad DML grants.

## 9. Archive only after implementation

- [ ] 9.1 After separately authorized implementation is merged and verified, rebuild any affected open whole-requirement delta against the then-current baseline, sync these requirements, and archive this change.
- [ ] 9.2 Treat archive, deployment, live overlay activation, legacy-mode retirement, and implementation release as separate authorized acts.

Spec-draft test delta: `Tests: +0 ~0 -0`.
