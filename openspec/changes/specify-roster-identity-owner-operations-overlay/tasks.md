Conversion status: **PENDING** exact-head independent review and hosted gates. The four checked
Section 1 tasks record historical drafting at `63de6168fc852fcbf4e8fc24717585f64df0e2a5`, not
completion of this goal-runtime conversion or any future implementation. All 42 original task IDs
and checkbox states remain. This amendment changes text only and ticks no task.

## 1. Draft the contract

- [x] 1.1 Refresh the prompt-source, API, database-role, direct-SQL, filesystem-helper, and MCP-writer inventory at baseline `63de6168fc852fcbf4e8fc24717585f64df0e2a5`.
- [x] 1.2 Record Travel's merged shared-include repair and QA's approved staffer exception without changing either roster prompt.
- [x] 1.3 Define complete composition, invalid-roster, owner-control, concurrency, privacy, database-isolation, migration, and rollback scenarios across the seven governing capabilities.
- [x] 1.4 Re-scan active same-named requirements and avoid `core-skills / AGENTS.md Read/Write Access`, whose two existing whole-body owners remain separately sequenced.

## 2. Exact-artifact review and adoption gates

- [ ] 2.1 Obtain independent semantic and security review of the exact draft commit; any semantic correction requires a fresh exact-head review.
- [ ] 2.2 Keep separate exact-artifact owner adoption for any future feature beyond adopted RFC0038 conformance; `bu-f5146g` direction approval is not such adoption. No new owner exact-text sign-off gates this authorized spec-only rescope.
- [ ] 2.3 Keep implementation, schema/grant changes, credential provisioning, runtime access, deployment, archive and release behind their separate authority gates. Merging this canonical RFC-conforming text grants none of them and needs no premature owner exact-text gate.

## 3. Future storage and role foundation after adoption

- [ ] 3.1 For separately authorized storage migration, preserve every prompt-history row with explicit `legacy_full_replacement` provenance and truthful historical seeded-mode evidence. Do not seed a live goal compatibility hold, assign one to a new agent or activate operations text.
- [ ] 3.2 Create the non-login table owner and direct-login prompt writer with the exact `NOINHERIT` and `NOBYPASSRLS` attributes from the database-security delta.
- [ ] 3.3 Preserve fixed runtime-role-to-agent RLS upper bounds and writer-only INSERT policy; do not turn domain credentials or row visibility into runner credentials, cross-agent reads or goal grants. Keep backup/migration authority operationally separate.
- [ ] 3.4 Provision the dedicated writer credential and pool plus a separate non-application migration/backup principal; make the generic dashboard login `NOSUPERUSER NOBYPASSRLS` before privilege cutover.
- [ ] 3.5 Transfer both history tables to the non-login owner, revoke historic table and sequence DML from `PUBLIC`, runtime roles, connectors, and the generic API login, then update bootstrap/finalizer reconciliation so reruns preserve the restriction.

## 4. Future roster and composition implementation after adoption

- [ ] 4.1 Make the shared guidance-loading seam resolve recursive roster-confined bare references with fixed missing/escaping/cycle/read-failure bounds and no partial guidance, while retaining distinct non-recursive HTML behavior.
- [ ] 4.2 Reject generated or DB-defined substitutes for required invalid guidance; keep lead policy/owner profile and child briefs as the governing goal inputs without domain-daemon prompt composition.
- [ ] 4.3 Remove the old domain-spawner overlay-input target. Preserve role-mapped per-agent database visibility only as an upper bound; any future read remains separately authorized and grant-bound, without shared-login or cross-domain fallback.
- [ ] 4.4 Implement RFC0038 lead-policy/profile and D15 context or D9 child-brief inputs; expose versioned guidance by authorized stamped retrieval. Preserve declared guidance byte/separator/shared-snippet order and literal operations provenance, not the superseded eight-layer prompt or situational injection.
- [ ] 4.5 Preserve honest source availability and fail-closed blind-spot disclosure in authorized evidence. Unavailable operations/context never substitutes for governing goal inputs, injects a fallback layer or leaks content.

## 5. Future owner-overlay API after adoption

- [ ] 5.1 Keep the prompt GET, PUT and history owner-management/provenance contracts without representing guidance, stored operations or historical modes as a complete goal prompt or runtime activation path.
- [ ] 5.2 Require fail-closed owner control before body, pool, roster, or protected-state access; use `authenticated_principal()` only for post-admission attribution.
- [ ] 5.3 Route all prompt reads and writes through the dedicated direct-login pool and reject unknown roster agents before acquiring that pool.
- [ ] 5.4 Implement bounded literal overlay validation, reserved-delimiter rejection, per-agent compare-and-swap serialization, identical-update no-op behavior, and atomic `butler.prompt_overlay_set` persistence with the exact metadata allowlist.
- [ ] 5.5 Make the dashboard distinguish versioned guidance, mutable operations and the separately governed goal prompt; retain explicit confirmation of the semantic-limit warning without granting activation authority.
- [ ] 5.6 Preserve immutable historical mode provenance and CAS/no-op/concurrency/atomic `butler.prompt_mode_changed` integrity duties for separately authorized state writes; deny obsolete goal selection. This amendment selects no replacement mode wire operation.
- [ ] 5.7 Preserve the old deployment-only fail-closed `BUTLERS_PROMPT_LEGACY_ROLLBACK_UNTIL`, acknowledgement and legacy-head safeguards as historical/operational evidence; no request/DB/MCP/runtime may open authority. Even an open old window cannot authorize a goal legacy mode; safe exit preserves history without hold re-entry or automatic activation.

## 6. Future roster governance after adoption

- [ ] 6.1 Validate domain shared guidance with its first-line include; newly added/substantively changed staffers require explicit infrastructure opt-in/out. Participation governs guidance, not a daemon prompt or a generalized D18 exception.
- [ ] 6.2 Record the existing Concierge, Messenger, and Switchboard shared-instruction opt-ins in their infrastructure contracts and capability projections without changing their prompt bytes.
- [ ] 6.3 Add QA's approved opt-out to its infrastructure-contract MANIFESTO and capability projection while keeping `roster/qa/CLAUDE.md` and `roster/qa/AGENTS.md` byte-identical.
- [ ] 6.4 Preserve Travel's already-merged shared include and all existing staffer opt-ins; do not create a new Travel repair or infer policy from file shape.

## 7. Future verification after adoption

- [ ] 7.1 Extend `tests/features/test_skills.py` for recursive versioned guidance, confined/cycle/missing/read-failed graphs, literal owner text, declared shared snippets and no generated/DB substitute for required guidance.
- [ ] 7.2 Extend the owning runner/guidance contracts and existing `tests/core/test_core_spawner.py` / `tests/core/test_core_spawner_context.py` for lead policy/profile, child briefs, settings via authorized context, exact guidance bytes/separators, source failures, no old eight-layer/situational injection, denial of both historical legacy selectors, expired pre-adapter denial, closed-source rejection and adapter invariance. Preserve the separate D18 executor boundary.
- [ ] 7.3 Extend `tests/api/test_butler_management.py` for 503/401 pre-access denial, central key-or-session plus independent cookie CSRF/exact Origin, unknown-agent pre-pool rejection, strict bounded literal input, owner-only guidance/history projections, CAS/no-op races, atomic allowlisted audit, historical mode integrity and denied obsolete selection including expired/open windows and hold re-entry.
- [ ] 7.4 Extend `tests/config/test_roster_claude_md_include.py` and `tests/config/test_delegation_guidance_reachability.py` to classify domain guidance and staffer declarations, pin Travel conformance and QA explicit opt-out/unchanged prompt bytes, and distinguish exposed QA triage goals from isolated anonymized D18 investigation executors.
- [ ] 7.5 Add `tests/migrations/test_core_prompt_overlay_authority.py` using direct-login writer, actual no-`SET ROLE` generic API, connector, mapped runtime, unmapped runtime, table-owner, sequence, both history tables, and bootstrap-rerun contexts.
- [ ] 7.6 Add privacy absence-sentinel coverage proving roster and overlay bytes never enter denied/conflict/error bodies, audit notes, logs, metric labels, or span attributes, with positive allowlist assertions so empty evidence cannot pass vacuously.
- [ ] 7.7 Run targeted nodes first, topology collection where applicable, migration/contract lanes, `make check-guards`, strict OpenSpec and overwrite checks, diff hygiene, fresh exact-head semantic/security review, and terminal hosted CI. Report the implementation test delta separately.

## 8. Future staged cutover and rollback after adoption

- [ ] 8.1 Before separately authorized storage migration, verify schema-aware code, dedicated-pool/credential handling, parsing and cleanup. Do not ship a second selectable old/new goal runtime or infer migration/activation authority from this amendment.
- [ ] 8.2 Complete ownership transfer, RLS enablement, historic grant revocation, direct-login verification, and bootstrap-rerun verification before any overlay becomes load-bearing.
- [ ] 8.3 Require authenticated owner review before any reuse of each legacy head; never infer, copy or activate identity/operations text during migration. Review does not choose a goal system prompt.
- [ ] 8.4 Verify the single RFC0038 hard cutover and lead/child guidance contracts without per-agent legacy goal modes, retaining per-agent history and specified metadata-only audit for authorized changes.
- [ ] 8.5 Retain separately authorized bounded operational restore with explicit acknowledgement, exact prior-stage evidence, CAS and atomic metadata-only audit for authorized writes; never delete history, restore broad grants or reinstate legacy goal selection.

## 9. Archive only after implementation

- [ ] 9.1 After separately authorized implementation is merged and verified, rebuild any affected open whole-requirement delta against the then-current baseline, sync these requirements, and archive this change.
- [ ] 9.2 Treat archive, deployment, live owner-content use, legacy-machinery retirement, operational restore and implementation release as separate authorized acts; none is performed by this conformance amendment.

Spec-draft test delta: `Tests: +0 ~0 -0`.
