Reviewed commit: 2c4c54c685265e126f299b0295dff35cc89024e8

# Investigator evidence

These scoped reports retain each investigator's original limits. The lead's packet supersedes provisional tracker/hosted evidence statements. Local temporary paths are audit provenance; durable reproduction bodies are in the companion reproduction document.

# Calendar, approvals, notification audit

Reviewed pinned source 2c4c54c685265e126f299b0295dff35cc89024e8; window 2026-10-09T01:42Z to 2026-10-13T01:42Z. Read-only audit; no repository or tracker changes.

## Findings

### C1 — P1, high confidence: unresolved Calendar attempts do not fence another enabled owner

Introduced by PR #4464 / closed bu-q7vx1q.18. `src/butlers/modules/calendar_response.py:500-508` checks unresolved `calendar_action_log` rows only through the current module's owning pool. Messenger and Relationship both enable responses against the default Google account (`roster/messenger/butler.toml:32`, `roster/relationship/butler.toml:163`). Calendar discovery mirrors accessible calendars into each owning schema, with `butler_name=None` for shared calendars (`src/butlers/modules/calendar.py:5997-6019`). `_target` accepts that null owner. The response API selects the owner of the submitted schema-local instance UUID (`src/butlers/api/routers/calendar_workspace.py:2660-2673`); it does not establish unique ownership of the physical provider target.

Trigger: an approved Messenger command times out after its committed start marker. A second approved command targets Relationship's independently projected instance of the same calendar/event/self. Relationship's local fence query sees no unresolved command, so it sends another PATCH while Messenger's first outcome remains uncertain. A normal refresh can change which physical instance survives cross-butler dedup, so the cross-owner route does not require corrupt IDs.

Reproduction: `PYTHONPATH=.:src .venv/bin/python /home/orca/.tmp/butlers-audit-20261013/calendar-fence-repro.py`. Runs the production preparation/execution coordinator, task-bound approval execution context, and existing test suite's explicitly software-only SQL carrier with two isolated pools. Output: `messenger uncertain provider writes=1`; `relationship applied provider writes=1`; `Same physical target key: True`; `First fence still unresolved: True`. This is control-flow evidence, not PostgreSQL concurrency proof.

Contradictory evidence considered: the transaction-scoped advisory lock uses the same physical target key across schemas, but it only serializes claims; the second transaction still cannot see the other schema's command after the first releases its lock. `If-Match` prevents overwriting a changed resource but does not prevent a second attempt when the first timeout had no provider effect, or when the second preparation observes its new version. Existing migrated tests prove same-schema replacement/source-deletion protection, not two enabled owners. Each action having a human decision does not waive REQ-module-calendar-029's explicit ban on replacement requests bypassing an unresolved target.

Fix: establish an authoritative physical-target owner/fence, respecting the existing single-owner data boundary, and make every enabled source route through it; alternatively refuse nonauthoritative owners before admission. Add a real migrated two-owner scenario proving zero second provider call while the first owner retains unresolved evidence. This is a correctness gap in the completed outcome; any shared authority/data design needs the corresponding contract decision, rather than a cross-schema shortcut.

### C2 — P2, high confidence: invitation response capability and receipt owner can describe a different physical entry

Introduced by #4455 and made actionable by #4464; closed bu-q7vx1q.17 / .18. `src/butlers/api/routers/calendar_workspace.py:1629` emits the deduplicated row's physical instance ID, while lines 1636-1638 emit owner/capability from a separately canonicalized source. `_dedup_workspace_rows` chooses the freshest instance (`src/butlers/api/read_models/calendar_workspace_v1.py:179-196,266`); source canonicalization instead ranks enabled/core source health and source cursor freshness (`src/butlers/api/routers/calendar_workspace.py:438-465`). Those selectors are independent.

Trigger: Finance or General holds the surviving freshest invitation instance while Messenger wins the logical source's canonical owner. The DTO advertises a Messenger response-enabled entry, but POST /respond looks up the UUID and dispatches to Finance/General, where calendar_respond is unregistered. Even when both physical and advertised owners support response, a Messenger/Relationship disagreement makes the returned receipt fail the UI's stored-owner dossier-link check (`frontend/src/components/calendar/CalendarResponsePanel.tsx:161-166`). The reverse mismatch can suppress available response controls.

Fix: bind response capability and displayed owner to the same physical entry used by the write, or deliberately select/rebind the canonical owner's corresponding instance and validate its freshness. Cover a freshest event copy whose schema differs from the healthiest source owner. Production-selector reproduction: `PYTHONPATH=.:src .venv/bin/python /home/orca/.tmp/butlers-audit-20261013/calendar-owner-repro.py` prints `Actual instance owner: finance`, `Advertised source owner: messenger`, `Response enabled according to API: True`. Both `_dedup_workspace_rows` and `_select_canonical_source_rows` are the actual functions; no mocked selector. No live provider interaction or mounted mismatch reproduction was performed.

### C3 — P2, high confidence: pending inverse outcome checks replay the original response

Introduced by #4464 / bu-q7vx1q.18. `frontend/src/components/calendar/CalendarResponsePanel.tsx:96-103` stores the inverse receipt but never replaces the entry's request identity. Lines 150-155 then implement 'Check response outcome' using `requests.current`, which still holds the original response request. `respond` resubmits that original and overwrites the inverse receipt with the original result.

Trigger: an original response is applied; Undo successfully reserves and approves an inverse but returns `approved` because dispatch is temporarily unavailable. If the invitation row remains visible (for example, original projection failed), clicking Check response outcome checks the original action, changes the panel back to Response applied, and loses the inverse's mounted receipt. If projection already removed the invitation, the control is absent entirely because it requires membership in `controls`. Neither path recovers the pending inverse correctly. The generic approval dossier may still offer recovery, so this is not permanent loss of the durable command.

Backend reachability: `_dispatch_approved_action_outcome` catches an unavailable/timeout second MCP dispatch and returns `unreachable` (approvals.py:1400-1415); `approve_owning_action` retains approved and sets `dispatched=False` (3190-3198). `_respond_through_approval` therefore returns approved (calendar_workspace.py:2598-2601,2628-2630). The undo branch wraps that response at 3768-3771 without changing its status. This occurs if the daemon becomes unreachable after successful command preparation but before execution; no invented backend state is required.

Fix: retain the inverse's exact request/command identity and implement outcome recovery for that identity independent of the invitation row. Never replay the original request as an inverse status check. Add a mounted test for applied original -> approved inverse -> check and for row removal. Existing panel tests cover rejected undo transport and forward pending replay, not pending inverse recovery. Static traced finding; not a newly executed browser reproduction.

## Coverage and verification

Read engineering-bar canonical skill, repo craft-and-care skill and engineering bar, CLAUDE.md, relevant Calendar normative requirement and archived response design, prepared-origin normative UI contract, and reviewed the assigned implementation diffs/current paths:

- #4439: one explicit SDK RuntimeError cause hop for approval connection timeout; no new finding.
- #4455: explicit provider status provenance, related-copy admission/dedup, API window/cursor/availability, invitation hook/page wiring.
- #4457/#4459/#4461: prepared origin contract, API list/detail/summary and independent delivery truth, rail/dossier presentation, subsequent baseline adoption.
- #4458/#4460/#4463: parked refusal typed reference, switchboard internal writer and migration, notification projection, complete configured approval census, approval detail discriminator and frontend link; no additional concrete defect found.
- #4462/#4464: published response contract, canonical gate registration, command preparation, task/actor/digest admission, dedicated conditional provider call, durable start/terminal receipts, approval route composition, inverse reservation and frontend receipt/recovery wiring.

Correlated closed issue identities/closure reasons from the supplied authoritative closed.json inventory: bu-2fosay, bu-q7vx1q.17, bu-q7vx1q.18, bu-h40h2b.5.11/.13/.14/.19/.23. Their closure reasons distinguish source delivery/native administration and state historical independent review/hosted gates. This audit did not independently reauthenticate historical hosted artifacts, and those recorded historical claims are not used as fresh pass evidence.

Executed on unchanged pinned tree:

- `uv run --no-sync pytest tests/modules/test_calendar_response.py -q --tb=short -n 0`: PASS, 5 tests, 2.49s, two existing FastMCP deprecation warnings.
- `uv run --no-sync pytest tests/api/test_calendar_response.py tests/api/test_notifications.py -q --tb=short -n 0`: PASS, 80 tests, 1.41s.
- Two-owner control-flow reproduction described in C1: confirms second provider invocation with first fence retained.

No broad suites, live Kubernetes/provider writes, database mutations, or tracker mutations. No newly executed real-Postgres or browser evidence. Native archive moves were checked at diff/contract level, not through independently replayed full archive generation. Existing untracked root .beads.gate.lock and .claude/settings.local.json were left untouched.


---

# CI / condensation / pre-push audit

Reviewed repository head `2c4c54c685265e126f299b0295dff35cc89024e8`; interval 2026-10-09T01:42Z through 2026-10-13T01:42Z. Read-only source review following th-engineering engineering-bar and Butlers craft-and-care standards. No project/tracker edits or hook installation.

## Confirmed finding

**P2, high confidence: test-loss admission misses executable additions that suppress unchanged tests.**

- Introduced by PR #4466, retained at current head and through #4467/#4475/#4476/#4480/#4481.
- Primary location: `scripts/check_condensation_ledger.py:889` (module/class context admission only asks whether old statements remain an ordered subsequence). Secondary same-boundary location: `scripts/check_condensation_ledger.py:891` (ancestor conftest scan iterates baseline-only `paths`, populated at line 832).
- Trigger A: append `test_a.pytestmark = pytest.mark.skip(reason="unavailable")` to an existing test module. The existing function, assertion, argument and decorator AST remain identical. The executable attribute mutation is a new context statement, so old context remains a subsequence and `markers` only recognizes module-level `pytestmark = ...`, not the function attribute.
- Trigger B: commit a previously absent `tests/conftest.py` containing an autouse fixture that calls `pytest.skip`. No existing file or test body need change. The new ancestor is absent from the baseline-only conftest scan.
- Effect: both changes suppress the existing assertion while `verify(root, base, [])` reports `PASS`, `losses: 0`, `ledger_clusters: 0`. This means no survivor account or fresh preservation run is requested. The reproducible disposable repository starts with a real failing `assert False` test (pytest exit 1), then each committed variant yields pytest exit 0 with `1 skipped`, while the guard remains PASS. Both variant commits have clean tracked source.
- Governing contract: `openspec/specs/testing/spec.md:1384`, REQ-testing-053 scenario “Assertion and parameter loss inside a retained name refuses”; also “One guard species and complete survivor accounting.” `docs/testing/condensation-evidence.md:103` expressly says additive changes and unchanged names do not establish preservation; lines 121–125 say additive structural conditions grant no fixture/runtime/zero-ledger exemption.
- Contradictory checks considered: the simple `test_a = None` repro would be caught by the separate duplicate-top-level-name scanner, so it is **not** the reported trigger. Attribute assignment and a new fixture do not duplicate a top-level binding. Fresh CI inventory and shard reconciliation describe current collected identities and accept legitimate skipped phases (`ci_partition.py:431` onward); they do not compare old test execution or restore this preservation guarantee. The budget guard is an upper bound, not survivor proof. Existing no-ledger producer requests are triggered only by detected losses, so fresh-plan execution cannot cover a missed loss.
- Repair: compare the union of old/current ancestor conftest paths and require accounts for additions as well as modifications/removals. Treat executable module/class context additions conservatively unless an explicit binding/control analysis proves they cannot alter existing test execution; otherwise qualify them through the same complete fresh account. Extend the existing scanner test species with committed new-ancestor and function-marker mutation cases, retaining the useful harmless-addition controls.
- Classification: correctness for already accepted REQ-testing-053, not a new feature. Independent review of the correction is appropriate. No evidence this exact bypass occurred in the production test changes during the interval.

Reproduction: `/home/orca/.tmp/butlers-audit-20261013/ci-loss-repro.py`; results: `/home/orca/.tmp/butlers-audit-20261013/ci-loss-repro.json`. Run with `python3` (host `python` is Python 2). Script imports the current guard, creates only disposable Git repositories, commits each variant, runs real installed pytest, and records actual guard verdicts.

## Assigned coverage

| PR | Scope reviewed | Audit result |
| --- | --- | --- |
| 4415 | Actual inventory/partition, scoped route/verdict, complete phase/identity reconciliation, frontend build binding and Vitest execution/receipt flow, workflow and advisory weights interfaces; sampled large supporting test changes | No additional confirmed defect. Exact current membership is materially stronger than static manifests; it does not constitute before/after condensation preservation. Hosted receipts delegated to parent. |
| 4435 | Full NOTIFY latency test diff and fallback/ordering assertions | Governing 500ms bound retained with explicit listener wake and persisted recheck; no finding. |
| 4437 | Full inventory test watchdog diff | Changes outer hang watchdog only; source budget degradation assertions retained; no finding. |
| 4453 | Image acquisition helper, actual Testcontainers callsite, compatibility/digest checks, timeout cleanup and browser consumer diff | No confirmed defect. No network pull or real Docker launch performed. |
| 4465 | Full pre-push driver/sandbox, installation/rollback/config binding, exact head updates, guard plan, retained delegate, process cleanup and operating docs | No confirmed defect. No installation performed. Hard elapsed target remains explicitly unearned. |
| 4466 | Loss scanner, test shape/import/fixture context accounting, ordinary verify, executable proof admission, spec/docs and test controls | Confirmed P2 above. |
| 4467 | Changed-source lineage alignment, coverage residue, mutant pairing, fresh CI binding/requests and proof phase/source admission; supporting execution/readback paths | Same finding persists; no separate confirmed defect. Broader original cluster/timing completion not claimed by finite prerequisite. |
| 4475 | Full complete-batch mutation production and consumer reconstruction diff | Batch completeness is independently regenerated, including sources and target IDs; no separate confirmed defect. |
| 4476 | Full callback/closed-capture lineage correction and surrounding lexical normalization | Unsupported capture/binding classes remain conservative; no separate confirmed defect. |
| 4480 | Full request-observation source/consumer protocol, strace stream parser, actors, descriptor/path matching and finalization | No separate confirmed defect. Whole-copy resource support is explicitly unfinished under 4483; do not interpret request observation as completed resource exclusion. |
| 4481 | Full mutation target identity diff and surrounding pairing | Literal AST target selector is retained separately from cross-source semantic point; no separate confirmed defect. |
| 4483 | Proposal/design/spec/task additions and declared release limits | Correctly presented as preparation only, no runtime resource admission or full original completion. |

Closed-bead excerpts checked for the owned scope include bu-ly3lv5.11.1, bu-ly3lv5.11.2, bu-ly3lv5.10.1, bu-nqvq3x, bu-bhndnq and associated source/review entries. They generally distinguish finite prerequisite closure from unfinished real clusters, daemon/runtime, resource, timing and deployment outcomes. Parent owns full tracker reconciliation and hosted exact-head evidence.

## Verification and limits

The retained decisive verification is the standalone committed-repository behavioral reproduction, exit 0, with actual before/after pytest outcomes and admission JSON. No broad suites, Docker lanes, provider operations or current hosted CI were run here.

A targeted command was launched for `tests/scripts/test_pre_push.py`, `tests/scripts/test_ci_partition.py`, and `tests/scripts/test_check_condensation_ledger.py` with `-n 0`; its initial output was empty and the tool session identifier was not retained. The processes subsequently exited. Its final result is **UNKNOWN**, and it is not used as evidence or called a pass. It was not repeated for reassurance.

This is a risk-focused source audit, not a claim that every line of the large test and workflow diffs was independently re-executed or exhaustively verified. Source review concentrated on fail-closed admission boundaries, matching consumers, scoped acceptance and honest unfinished outcomes. No P1 issue is asserted. Observed root untracked `.beads.gate.lock` and `.claude/settings.local.json` were left untouched.


---

# Runtime, data, connector and supporting-change audit

Audit target: `2c4c54c685265e126f299b0295dff35cc89024e8`; interval 2026-10-09T01:42Z through 2026-10-13T01:42Z. Read-only source/tracker audit; no repository or tracker mutations.

## Result

No confirmed newly introduced correctness/security defect in the assigned changes. This is bounded review evidence, not certification of the full runtime or deployment.

## Reviewed coverage

| PRs | Scope and review outcome |
| --- | --- |
| 4434 | Bootstrap amendment-table ownership convergence: reviewed exact SQL change, owner/type/persistence/configured-login preflight, later identity revalidation and NOWAIT table lock, atomic owner block and declared non-atomic earlier bootstrap effects. Acceptance explicitly acknowledges migration-owner RLS bypass and ACL grantor substitution. No claim of full-script atomicity. Did not rerun its real-PG owning node. |
| 4436,4438,4440 | Mocked pipeline source-registry isolation: reviewed scoped context restoration, nested/exception controls, explicit fixture selection rather than global production weakening. These tests intentionally model downstream routing, with SQL source-admission controls separate. |
| 4441 | Ingestion spec reconciliation/archive: reviewed file inventory, added meaningful URL/history assertions and historical cascade-audit survival assertion, spec adoption/retirement claims. Did not independently reconstruct all seven native-spec before/after populations or ratchet inverse. |
| 4442 | Metrics/document-renderer retirement: reviewed relocated query helper/callers/registry/source-test mapping, removed package/test/dependency inventory, and closed bead's explicit approved retirement scope. No surviving legacy import found during review. Existing Prometheus behavior was preserved; no new malformed-response behavior attributed to this move. |
| 4448 | Owner singleton entity merge: inspected original-role union before source release, same-transaction source tombstone and target assignment, pair-lock context and subsequent rebind/review writes. Real migrated test adds old-order unique-constraint failure, separate-reader visibility, later failure rollback and ordinary-General denial. Moving tombstone earlier does not alter later query predicates to depend on source eligibility. No new defect found. |
| 4450 | `sw_043`: finite extension of existing correct_route/failure audit vocabularies; prior vocabulary preserved, downgrade locks first and refuses incompatible rows. Not rerun against PostgreSQL. |
| 4452,4454 | Cancellation fixture now waits on adapter entry, retaining original unwind/drain assertions; accessibility helper wraps actual axe call with React act, retaining six callers. No production change or weakened assertion found. |
| 4468 | Legacy liveness startup is skipped only for exact receiver flag `1`; flag-off keeps legacy task and shutdown tolerates absent task. Narrow owning file passed. |
| 4469–4472 | Gmail source classification now uses the actual httpx response's exact token POST request, not exception-level apparent origin. Query/fragment/wrong-origin/method/missing-request controls retain diagnostics and failure identity. Subsequent native application/archive chain matches a source-delivery then administration sequence. Focused new regressions passed. |
| 4473 | Contacts owner routing: reviewed client response validation, fixed target/tool boundary, dynamic startup client/source identity, server-reset third-party provenance, source-link-first resolver, exhaustive distinct eligible channel lookup, failure/ambiguity handling and bounded channel diagnostics. Checked actual Switchboard route wrapper and FastMCP return shape against adapter assumptions. Candidate/pending channel outcomes and best-effort local-contact processing are explicitly documented, not falsely sold as active channel delivery. Local owner tests passed; real-role integration source reviewed but not executed here. |
| 4477 | Relationship registered inventory is preserved as legacy names plus two Contacts tools; meaningful complete set assertion and production TOML wiring checked. Active proposal inventory correction does not claim temporal feature adoption. |
| 4479 | Kubernetes cutover proposal inspected at its stated source epoch: proposal, spec, allocation/failure matrix and source inventory explicitly distinguish proposal delivery, later contract/custody adoption, implementation, provisioning and operations. Not an implemented cutover and not labelled one. Full future controller/RBAC/SQL properties remain untested proposal obligations. |
| 4482 | Two-line plans-index correction points to real archived approval recovery and labels superseded core208 rollout honestly. |

## Bead completion correlation

Inspected available closed-export acceptance and closure fields for bu-d55reo, bu-pmsa6e, bu-h6tssj, bu-a9sd9, bu-lsxqb0.24, bu-2cpmi6, bu-ezo76k, bu-rrmnp4, bu-quektp, bu-ly3lv5.14.1, bu-ly3lv5.14.1.4, bu-14acw7 and bu-60pwv6.44. Their stated narrow result matches the changed surface. Historical independent review/control/native inversion/CI claims remain claims unless independently checked by the parent audit; this review does not restamp those claims as new verification. bu-ss4o2u, bu-yq0rz9 and bu-0kf2fd were not present as matching closed records in the supplied export, so commit/spec scope was used without inventing a closed-bead status.

Deployment-only closures bu-psarp and bu-7exe4.2: export records explicit October 8 owner authorization superseding production prerequisite deployment with a sole-dev-fleet stop-the-world rollout. Notes preserve rollback ordering (stop writers, downgrade eligible schemas, then old image). Both closure reasons assert October 11 Helm revision 9/image c966a1cdde9e, writers stopped, core265/sw042 applied, 8→4 Telegram anchors with four messages retained and backup/snapshot presence. The supplied export contains no per-writer immutable deployed-version/process-replacement/health receipt linked to that assertion. bu-psarp has two comments not included in the export; requested parent retrieval before treating evidence as missing. This is an independently-unverified operational claim, not proof that rollout failed or authorization was absent.

## Fresh verification

- `uv run --no-sync pytest tests/modules/test_contacts_owner.py tests/daemon/test_liveness_reporter.py tests/core/test_prometheus.py -q --tb=short -n 0`: **13 passed in 11.07s**, exit 0.
- `uv run --no-sync pytest tests/connectors/test_gmail_connector.py::test_source_auth_classification_requires_response_bound_token_post tests/connectors/test_gmail_connector.py::test_source_origin_fetch_and_token_health_preserve_failure_and_reset -q --tb=short -n 0`: **2 passed in 1.45s**, exit 0.
- `.venv` is a real directory. Root source not edited. Existing untracked `.beads.gate.lock` and `.claude/settings.local.json` were left untouched.

Limitations: no new real-Postgres, full pipeline, frontend, broad CI, cluster, provider or historic rollout test was run in this slice. Parent is independently verifying merged PR/queue evidence. Source review and 15 unit tests cannot replace the genuine role/transaction/deployment proofs required by the governing contracts.

## Operational evidence follow-up

Parent retrieved `psarp-comments.json` and `conversation-comments.json`: historical inventory/authorization only, no additional rollout receipt. PR3960 independently merged October 8 at 81737ed (outside the source-change interval); the October 11 closures still carry an operational evidence gap for per-writer deployed-version/process/health. Parent also checked live tracker mapping: bu-ss4o2u remains in_progress, bu-yq0rz9 blocked, bu-0kf2fd blocked. Their landed source/proposal PRs must not be counted as closed whole-feature beads.

## Independent challenge: Calendar cross-owner unresolved fence (confirmed P1)

This additional peer challenge concerns PR4464, outside the original assigned source slice. **High-confidence P1:** `src/butlers/modules/calendar_response.py:501` queries an unqualified per-schema `calendar_action_log` for unresolved target attempts. Its database-wide advisory transaction lock does not make another schema's marker visible. When Messenger has `egress_started`/`uncertain`, Relationship can approve and execute a new command for the same physical invitation.

Counterevidence checked:

- Both `roster/messenger/butler.toml:32` and `roster/relationship/butler.toml:163` enable responses; both have actual calendar_respond approval gates. This is not a disabled/config-only alternative path.
- `calendar.py:3272` verifies each account but does not enforce exclusive physical-event ownership. Default configuration uses the same primary Google account.
- `calendar.py:5980` onwards discovers all provider calendars in each module, records the same account email, and sets ordinary user calendar `butler_name=None` at line 6013. `_target` at `calendar_response.py:252` admits None or the current butler. Account/source checks therefore permit both projected entries legitimately.
- Dashboard owner uniqueness at `calendar_workspace.py:2661` is by local entry UUID, not physical calendar/event/self identity. Independently projected instances have distinct UUIDs and each resolves to one owner.
- Provider If-Match at `calendar.py:2618` protects a stale conditional mutation. It does not enforce the specified no-second-attempt contract: an uncertain connection failure before provider effect preserves the ETag, so a second owner can dispatch with it. A later new preparation can also bind a newer ETag; neither operation resolves the first marker.
- Approval authorization is required for both calls. The defect is cross-owner uncertainty/idempotency enforcement, not an approval bypass.

Executed peer's reproducible source-level coordinator control:
`PYTHONPATH=/home/orca/orca/projects/butlers uv run --no-sync python /home/orca/.tmp/butlers-audit-20261013/calendar-fence-repro.py`
Observed Messenger uncertain/provider writes=1, Relationship applied/provider writes=1, same target_key=True, original fence remains unresolved=True. This uses modeled independent schema pools and provider calls; it proves the coordinator dispatch gap, not a real Google duplicate effect or real PostgreSQL execution.

Governing requirement: `openspec/specs/module-calendar/spec.md:933` prohibits a new request key bypassing a started unresolved command; lines 986–990 prohibit same/replacement requests dispatching a second write for that target. Fix must make physical target fence ownership unique fleet-wide through a sanctioned shared authority or one canonical executor, and cover two real schema/role owners. Broad cross-schema writes are not an acceptable shortcut around schema doctrine.
