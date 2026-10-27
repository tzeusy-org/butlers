## Context

See proposal.md for the source-transfer motivation. The audit at main `5e611d88c57463af9c75e7b2a90cc7507b94d4c7` found a partial implementation broader than RFC0031's stale status and narrower than the complete original S1-7 outcome. This change transfers the contract; it does not repair or certify that implementation.

`core_215_entity_graph_edges.py` supplies the table, keys, checks, indexes and twelve runtime-role grants. `core_222_entity_graph_edges_concierge_grant.py` adds the omitted Concierge grant. The public projection helpers, Memory edge-fact storage, Relationship assertion writer and commitment `post_write` hook implement selected source effects. Recursive walk/path and graph-group registration exist. Catalog search attaches coverage from the public count helper. No original entity dossier API or EntityDetailPage graph dossier consumer was found.

## Goals / Non-Goals

The design goal is one canonical WHAT home with a traceable source transfer and a concise RFC WHY/HOW record. Every existing baseline and active delta remains byte-for-byte unchanged. Archival applies the new capability only; it does not complete the existing graph feature.

No runtime, migration, grant, credential, provider, temporal cutover, private-source read, background loop, foreign proposal adoption or feature ownership change is included. Test edits are comments only. There is no new ontology, belief revision, contradiction docket or writes from readers.

## Decisions

### Transfer source obligations rather than soften missing implementation

Requirements 001-012 remain `v1-mandatory`. Requirements010/011 use target-state titles because their API/UI are absent, and 012 retains mandatory migrated substrate verification without a fake test citation. A cited requirement is not thereby fully implemented: partial source, fixture and assertion limits below remain part of the feature handoff.

The canonical th-projects spec-format uses authoring integrity before source signoff and full strict test tracing at implementation/milestone closeout. This source transfer therefore checks the exact new home with the official authoring validator routines and retains the full repository strict findings separately. It does not relabel gaps post-v1 or strip foreign findings to claim strict green.

### Keep existing behavior homes and authority

- Entity-identity owns identity/channel resolution and the distinct legacy `entity_neighbors` Google-account exclusion. Neither is rewritten as a public graph traversal exclusion.
- Core-daemon/RFC0002 and the active runtime-tool-surface-discovery source own group-gated registration. Requirement007 records the graph group's existing seam without adopting a broader discovery proposal.
- Relationship facts and the protected effective-time source own assertion validity. Graph projection does not introduce an effective-now cutoff or authorize a temporal cutover.
- Existing catalog read authority remains server-held; attaching counts does not grant private source reread authority.
- Dashboard-relationship's existing entity-activity aggregator requirement owns all response shapes' fixed content-blind degradation reason. It is not duplicated as a graph requirement.
- Database-security owns the catalog/central-GC write matrix. Its DELETE/bootstrap contradiction remains explicit rather than being overwritten by graph DML needs. The active public-write audit is a proposal, adopts no per-table posture and must not be applied by this change.
- The active Chronicles/commitment-door proposal explicitly excludes graph consumption. Existing activity/neighbor/commitment panels are not the graph dossier.

### Preserve historical assertion semantics

Commitment creation projects a directed `committed-to` assertion. `commitments.py` explicitly retains it on resolution because resolution is not source deletion or retraction. Current commitment backfill selects only `open`/`aging` rows. That recovery mismatch remains a mandatory finding, not a reason to delete live historical assertions or redefine eligible recovery during documentation work.

Memory rule eligibility must be derived from its owning model. No two-anchor rule producer was discovered; unary rules cannot receive invented anchors. The original memory facts/rules source-family obligation remains, including the audit/implementation path for genuinely eligible assertions. A backfill helper without a discovered operator/job invocation is not a delivered historical recovery outcome.

### Use truthful adjacent citations

Comments identify only existing executing assertions. Exact function/class targets were AST-checked before editing. Comparison of executable ASTs, function counts and assertions proves that comments do not change tests. The seven mapped files contain 247 functions and 882 assertions before the change; collected nodes are measured separately.

| ID | Observed source and existing test seam | Remaining verification or implementation limit |
| --- | --- | --- |
| 001 | Natural-key helpers; Relationship, migrated Memory and migrated commitment rerun tests | Rerun assertions do not prove every cross-source provenance/anchor or collision clause; migrated enforcement remains012 debt. |
| 002 | Memory create/supersede/forget; Relationship assert/supersede; commitment creation hook rollback tests | Complete eligible rule, merge/delete and lifecycle inventory remains incomplete. Commitment closure retains historical edges; resolved backfill parity is unverified. |
| 003 | Migrated Memory normal/pii/confidential edge creation; stand-in traversal excludes stubs | Producer positives do not certify migrated malformed XOR/sensitivity negatives. |
| 004 | Three bounded backfill helpers and repeat-call tests | Historical runner/invocation, inactive/stub/concurrency coverage and resolved-commitment recovery parity are incomplete. |
| 005 | Real-PG stand-in nearest-hop/cycle/truncation tests; wrapper forwards filters | Hard500 boundary and all negative/no-effect clauses are observed source, not new executed proof. |
| 006 | Stand-in shortest ordered path, missing/zero-hop controls; wrapper receipt mapping | Not every provenance field or boundary combination is separately covered. |
| 007 | Existing daemon graph allowlist on/off/null test with mocked lifecycle | Not full discovery advertisement, all-butler integration or adoption of an active discovery proposal. |
| 008 | Stand-in incident/withheld count tests; mocked catalog HTTP response mapping | No migrated actual catalog-handler/source-count equivalence or dossier behavior is credited. |
| 009 | Walk executes with only the public edge stand-in; wrapper read receipt mapping | No absent dossier handler, cryptographic source ownership or whole privacy boundary is certified. |
| 010 | No original entity dossier route/model found | Mandatory API outcome and actual source/receipt/count proof remain absent; no citation. |
| 011 | EntityDetailPage has existing activity/neighbor surfaces, not graph dossier | Mandatory dossier consumer and user flow remain absent; no citation. |
| 012 | Core215 substrate and core222 Concierge grants are present | Dedicated migrated FK/cascade/UNIQUE/XOR/roles/bootstrap/core-only/shared replay proof is not supplied by traversal's no-FK stand-in; no citation. |

Existing relevant tests are `tests/integration/test_entity_graph_walk.py`, `tests/integration/test_commitments_entity_graph_edges.py`, `tests/core_tools/test_graph.py`, `tests/daemon/test_daemon.py`, `tests/modules/memory/test_memory_migration_integration.py`, `roster/relationship/tests/test_relationship_assert_fact.py` and `tests/api/test_memory.py`. Their citations credit their own assertions, not all clauses in a requirement. No local real-Postgres PASS is inferred from source inspection or collection.

## Risks / Trade-offs

- **Citation presence can look like complete coverage.** Retain the partial-clause ledger above and the exact010/011/012 strict findings. Feature closeout requires meaningful behavior evidence, not just an ID match.
- **Projection status can be confused with source authority.** Ordinary shared grants are cooperative application ownership; no new RLS, peer SELECT or security-definer boundary is claimed.
- **Source condensation can discard original WHAT.** Preserve provenance/schema, atomic lifecycle, withheld accounting, historical backfill, traversal, registration, catalog coverage, original dossier URI/UI, substrate and S7 riders through canonical requirements or their existing homes. RFC retains decision/alternatives and explicit residual status.
- **S7 catalog grants have an active unresolved posture conflict.** Bootstrap/default privileges broadly grant DELETE. Runtime catalog purge marks stale; observed catalog DELETE statements are privileged installer/seed migrations (`core_183`, `core_234`, `concierge_002`), not an observed runtime DELETE caller. The audit's shared-writer recommendation is not an adopted resolution. No grant change or S7 completion is inferred.
- **Activity source implementation is broader than its verified discriminator evidence.** Current Relationship models/router and EntityDetailPage handle `chronicler_activity_unavailable`; existing UI fixtures do not prove the actual API's fixed reason/empty-success distinction across all shapes.

## Migration Plan

Apply the ADDED entity-graph capability through normal OpenSpec sync/archive; preserve every existing spec and active source. The CLI's new-capability Purpose transfers into the new baseline. Only source-transfer tasks are completed. Reverting these document/comment changes has no runtime or data effect; it must not be represented as rollback of the graph feature.

## Residual Work Handoff

`bu-8cdl1.8` remains OPEN under its existing foreign coordinator; related graph source reviews are closed and no open children were found in the shaping census. Do not claim, mutate, transfer or close it here. Its original outcomes remain mandatory. The coordinator retains complete bounded field proposals and these cohesive work allocations:

1. **Projection foundation and recovery:** audit/repair exact eligible lifecycle effects and historical invocation, preserve resolved commitment assertions, and prove migrated keys/FKs/checks/role-bootstrap/shared replay. Extend existing consolidated owning tests with actual source failures and separate-acquisition readback; no stand-in FK certification or new loop/cutover authority. Counts-match-source evidence is a prerequisite for feature completion.
2. **Original dossier API and EntityDetailPage flow:** deliver S5/S6 together using actual public provenance/counts, existing dashboard authority, truthful empty/unavailable and privacy controls. Preserve the original `/api/entities/{id}/dossier` outcome; never silently substitute the existing Relationship activity URI or mock catalog counts. Re-census actual API/UI owners before allocation and preserve Chronicles/commitment-door source.
3. **Original S7 riders:** verify the actual activity handler's unavailable fixed reason plus successful-zero/source-selection controls in its existing owning tests. Keep catalog no-runtime-DELETE intent and broad-bootstrap incompatibility explicitly held behind exact existing posture/adoption and installer/writer compatibility. A migration-only revoke cannot certify convergence. No new owner-policy adoption is inferred.

No duplicate feature terminal, test-per-clause species or future-source adoption is created. Actual family signoffs and exclusive hunk allocations remain protected. The final graph feature/milestone must retain full strict and partial-clause findings until actual behavior proof resolves them.

## Source Verification

At source handoff, official focused authoring routines accept the exact canonical home (12 requirements/IDs, zero authoring errors), and explicit strict OpenSpec validation plus all guards accept the tree (262 items). Delta-to-canonical requirement blocks are identical, all 392 prior baseline/active spec files are unchanged, local document links resolve, and README changes only the RFC0031 row.

The full repository strict trace is retained as FAIL: 2828 findings, including exactly the three new mandatory uncited IDs 010/011/012 and 2825 existing-home/test findings. Partial 001-009 limits above remain even where a test ID resolves. This is not a strict implementation closeout claim.

Named non-Postgres wrapper/registration/catalog mapping selections pass 17 tests. Full tests/roster collection passes (22443 selected, 22480 before 37 default deselections). Shards select unit 17485 and integration 4866 nodes exactly once; both lanes are within budget. The seven comment files preserve their executing ASTs, 247 functions and 882 assertions: Tests:+0 ~0 -0. No structural fixture repair or test weakening was needed.

Real migrated-Postgres behavior and exact-head hosted/merge-queue evidence remain pending at author handoff. Collection, source inspection, mocked HTTP controls and no-FK traversal fixtures are not substituted for those proofs. The existing feature and its original outcomes remain incomplete.
