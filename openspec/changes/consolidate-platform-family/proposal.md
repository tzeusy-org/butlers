# Platform family consolidation proposal

## Why

The complete Phase 2a Platform family needs one cohesive, owner-signed consolidation with full requirement/scenario carryover and strict post-archive traceability. Current source-home deltas and genuine proof gaps still hold execution. This current stage records the reviewable documentation proposal only.

## What Changes

Current stage: documentation-only native change metadata, proposal, design and truthful tasks. No spec-level content or source behavior changes occur. Native skip_specs metadata is temporary for this actual docs-only stage; all future spec work stays pending. This active change is not archived as the completed consolidation. Remove the marker before actual deltas and revalidate after fresh reconstruction and required gates.

Complete future scope and gates (binding proposal, not accomplished changes):

Phase 2a of docs/plans/2026-10-03-shape-condensation-plan.md; the complete cluster B target list, T2 dispositions, every T3 contradiction and all T4 altitude moves in docs/plans/2026-10-03-shape-condensation-evidence.md remain binding. This is documentation/spec consolidation: no runtime behavior change and no production code edits beyond doc pointers. PRIMARY shaping is a full read-only proposal checkpoint, not implementation completion or permission to move a spec.

T1 full survivor list (30): core-daemon, core-modules, core-scheduler, core-state, core-sessions, core-skills, core-spawner, runtime-adapters, core-telemetry, core-credentials, core-notify, runtime-config, model-catalog, model-failover, catalog-token-limits, e2e-benchmark-harness, cross-butler-briefing, cross-butler-delegation, domain-event-bus, ingestion-event-registry, ingestion-policy, switchboard-rule-promotion, switchboard-identity, s3-blob-storage, qa-staffer, qa-investigation, qa-dashboard, owner-condition-ledger, owner-timezone-context, system-overview-page.

T2 complete non-keep dispositions (27 literal source rows; clipped source cells remain retained rather than guessed):
| B | core-credentials | 45.3 | rewrite-altitude | core-credentials (slim) + google-multi-account-oauth + module-spotify  | Observed | 46KB grab-bag: generic CredentialStore is core, but Google OAuth lifecycle, Spotify token storage and Codex device-auth  |
| B | core-daemon | 18.7 | rewrite-altitude | core-daemon (slim); runtime_seed reqs -> runtime-config; blob reqs ->  | Observed | Carries other capabilities' requirements, a stale blob startup contract, and garbled double-negated runtime_seed scenari |
| B | core-notify | 49.5 | rewrite-altitude | core-notify; Messenger route.execute gate -> messenger/module-approval | Observed | 50KB, 28 reqs, 9 active deltas; [TARGET-STATE] tags are stale for implemented Switchboard routing; Messenger-side defens |
| B | core-spawner | 52.4 | rewrite-altitude | core-spawner (slim); adapter matrix + Codex auth -> runtime-adapters;  | Observed | 54KB with two internal restatements of catalog resolution and failover rules owned by model-failover. |
| B | core-telemetry | 10.9 | rewrite-altitude | core-telemetry (keep behavior; replace instrument catalog with referen | Observed | Metric instrument lists duplicate RFC 0005's Metrics Catalog; also contradicts staffer-qa metric names. |
| B | cross-butler-briefing-aggregation | 6.0 | merge-into | cross-butler-briefing | Observed | Aggregation and contribution are one producer/consumer contract sharing schema and the staffer exclusion. |
| B | cross-butler-briefing-contribution | 12.3 | merge-into | cross-butler-briefing | Observed | Same as above; per-butler contribution content (Health/Finance/... L46-L146) could alternatively move to each butler's s |
| B | docs-information-architecture | 15.7 | retire | about/craft-and-care/review-and-documentation.md §Document Lifecycle + | Observed | A documentation standard, not product behavior; its target docs tree has already drifted (docs/ has plans/, redesigns/,  |
| B | domain-event-delivery-recovery | 2.3 | merge-into | domain-event-bus | Observed | Its replay requirement is a near-verbatim duplicate of domain-event-bus; only the panel-visibility scenario is new. |
| B | healing-anonymizer | 6.5 | merge-into | qa-investigation | Observed | Anonymizer exists only to gate QA/healing PR egress; QA dispatch already restates its contract. |
| B | healing-model-tier | 4.0 | merge-into | model-catalog (one scenario) + qa-investigation (resolution rule) | Observed | Change-shaped spec ("API Validation Update", "Seed Data") whose enum content is owned by complexity-classification. |
| B | healing-session-tracking | 16.8 | merge-into | qa-investigation | Observed | healing_attempts is the QA investigation ledger (RFC 0015 D5); attempt state machine and API are one capability with dis |
| B | healing-worktree | 7.4 | merge-into | qa-investigation | Observed | Worktree lifecycle is restated in qa-investigation-dispatch#Worktree-Based Investigation. |
| B | model-benchmark-harness | 4.8 | merge-into | e2e-benchmark-harness | Observed | Test-harness contract implemented in tests/e2e; belongs with the E2E harness from testing and with tool-call-scorecard. |
| B | owner-condition-ledger | 11.5 | rewrite-altitude | owner-condition-ledger (fix Purpose; Finance req -> finance butler spe | Observed | Purpose is the archive TBD; a Finance-butler job requirement sits in a cross-cutting ledger spec; shares one engine with |
| B | qa-investigation-dispatch | 30.9 | merge-into | qa-investigation | Observed | Becomes the survivor that absorbs healing-* and core-spawner healing reqs. |
| B | qa-log-scanner | 12.2 | merge-into | qa-staffer | Observed | One discovery source; staffer-qa#V1 Discovery Sources already specifies it, with RFC 0015 D1 a third copy. |
| B | qa-triage | 8.7 | merge-into | qa-staffer | Observed | Triage is a patrol stage; its three-source dedup is copied from RFC 0015 D2. |
| B | runtime-config-api | 5.5 | merge-into | runtime-config | Observed | Three 4-6KB specs over one table plus core-daemon's seeding reqs describe one capability. |
| B | runtime-config-dashboard-ui | 3.8 | merge-into | runtime-config (or A-dashboard butler-detail spec) | Observed | Self-contradictory on which tab hosts the editor. |
| B | runtime-config-table | 6.6 | merge-into | runtime-config | Observed | See runtime-config-api. |
| B | runtime-opencode | 9.0 | merge-into | runtime-adapters | Observed | Only adapter with its own spec; claude/codex/gemini live inside core-spawner#Multi-Runtime Adapter Support. One adapters |
| B | session-process-logs | 6.5 | merge-into | core-sessions | Observed | Per-session child table of sessions; no independent capability. |
| B | staffer-archetype | 9.1 | retire | about/heart-and-soul/architecture.md §The Staffer Archetype (already t | Observed | Restates doctrine nearly line for line; the testable parts (type parse, briefing skip, switchboard registration) are alr |
| B | staffer-qa | 25.0 | merge-into | qa-staffer | Observed | Survivor for patrol/discovery/triage; drop Future Discovery Source Catalog (roadmap, already in RFC 0015 D1) and MANIFES |
| B | testing | 60.1 | retire | docs/testing/ (Quick Start, Debugging E2E); about/craft-and-care/testi | Observed | 63KB mixing run commands, debugging how-to and engineering rules; none is product capability. Pytest verdict rule alread |
| B | tool-call-scorecard | 2.3 | merge-into | e2e-benchmark-harness | Observed | 2KB, 4 reqs; is the scoring half of the benchmark harness. |

Preserve the entire original family outcome in one cohesive signed OpenSpec change. Newly cohesive capability homes are runtime-adapters, runtime-config, e2e-benchmark-harness, cross-butler-briefing, qa-staffer and qa-investigation. Sessions absorb process logs and lifecycle/ingestion lineage; failover absorbs the orchestration/classification rules; shared ledger primitives retain their behavior. Provider-specific credential writer/lifecycle rules move to their actual owning provider capability, with mandatory read/access/privacy boundaries preserved. Neighboring source owners retain implementation authority. Requirements and scenarios may collapse only as proven duplicates with a surviving permanent ID; every unique required result, refusal, retry, idempotence, privacy, authorization, failure and unavailable state keeps a mandatory home.

T3 all seven concrete corrections: (1) S3 connectivity failure disables blobs and warns while startup continues; (2) one linked measured or unmeasurable usage row per actual invocation, none for synthetic quota/exhausted skips; (3) registry instantiation is distinct from startup selection, while accepted declared/started-set equality remains mandatory and the currently observed schema-less inclusion is a diagnostic source gap; actual selector/start-set proof and relay/fallback/deadwire work remain owned by bu-1fe7xv; (4) core OTel butlers. namespace and accepted unprefixed QA Prometheus names are distinct; (5) runtime editor is on Management/Manage, with Config retaining Git configuration; (6) runtime_seed wording/titles match operational parse, ignored obsolete nested sections and rejected top-level/catalog-owned knobs; (7) remove stale target-state status only from actually shipped Switchboard-to-Messenger routing, not every draft capability. Cite actual owning decision records, including coordinator-recorded ordinary factual judgments where no prior exact citation exists; never invent an ID/signature or turn source observations into owner adoption.

T4 all thirteen placements: staffer concept -> architecture doctrine with type/routing/briefing behaviors kept in specs; QA infrastructure contract -> doctrine/QA manifesto; future source roadmap -> RFC0015 rationale; QA protocol/source table/dedup rationale -> RFC0015 but SHALLs remain qa-staffer per closed .10; core metric catalogue -> RFC0005 with recording behavior retained; S3 scheme/config rationale -> RFC0016 with behavior retained; Spotify Tier2 rationale -> security doctrine with connector writer/reader/privacy authority retained; testing quickstart/debug -> docs/testing; engineering test standards -> craft; docs-IA entire mandatory standard -> craft; invalid-input motivation -> craft while MCP error/empty behavior stays modules; ingestion legacy procedure -> docs while unique migration outcomes remain policy data model; blob migration/local-cruft procedure -> docs while S3 behavioral outcomes remain mandatory. No v1-mandatory demotion to post-v1.

Current authority: bu-hycu7t CLOSED releases execution but explicitly preserves per-family signed change. bu-lsxqb0.6 CLOSED delegates actual RFC0015 fallback cleanup to bu-1fe7xv; the old wait-for-.6 note is stale. bu-lsxqb0.10 CLOSED keeps QA SHALLs in specs and rationale in RFC, sequenced after authorize-qa-local-scheduler-policy and confine-qa-publication-authority. bu-1fe7xv OPEN rounds2–4 owns startup passage/equality test, relay-only declarations/shared skill reachability, fallback and dead spawner wiring, plus its separately governed removal scope. .17 must not implement that source behavior or reclaim its tests. Closed implementation records are not active-delta archive receipts or proof of live activation.

Gated execution stages: A coordinator reviews/applies all four complete fields and explicitly resumes the lane. B bounded authoring may create the one approved native CLI change envelope/proposal/design/tasks as an explicitly documentation-only current stage, with valid .openspec.yaml metadata declaring skip_specs: true and NO specs/ content; the complete future consolidation stays pending in its tasks/ignored full draft. An unmarked zero-delta envelope is invalid. Remove skip_specs BEFORE authoring any actual delta, revalidate the real change and retain all original outcomes; never archive this docs-only stage as the whole consolidation or credit its artifact status as fulfilled spec work. If this staged envelope is not selected, retain the full draft ignored and record the reviewed proposal in the existing owned planning document until source gates release. Neither route moves a spec or touches a held delta/canonical capability. C reconcile source ownership and all relevant active holder archives, then reconstruct the complete fresh family, validate the full carryover and exact gate citations, and obtain actual owner signature on that complete change before any spec file moves. D execute the signed pure moves/consolidation/pillar/pointer edits only after those gates; preserve unresolved holdings instead of partially declaring the original outcome done. E use normal CLI sync/archive and verify strict traceability on every actual touched canonical spec, zero live retired-capability citers outside openspec/changes/archive, and no duplicate live requirement text. Failed/NOT RUN gates and baseline debt remain explicit. No scaffold, tracked edits, tests, installs, commits or push occurred at PRIMARY.

Active sequencing snapshot: 66 relevant current delta files in 33 change folders (53 original target/neighbor files plus 13 incoming mandatory-home files). core-notify has 11 current holders versus the original historical nine; also reconcile prospective public PR4064 notification hunks before reserving names/IDs or moving its home. Each relevant actual holder must archive in its current home before .17 touches it. Refresh the census before each gated authoring/move stage. Complete current holder folders:
- account-security-perception
- add-person-posture
- add-personal-baselines
- add-runtime-tool-surface-discovery
- artifact-bound-filtered-event-restore-verification
- ask-once-knowledge-gaps
- authorize-qa-local-scheduler-policy
- confine-qa-publication-authority
- decision-loop-one-tap-approvals
- durable-dashboard-terminal-action-recovery
- durable-precommit-cancellation-admission
- generation-fenced-codex-auth-rotation-provenance
- harden-memory-retention-schedule-recovery
- harden-runtime-auth-and-breaker-attention
- models-exact-path-vision-proof
- provider-allowance-windows
- reconcile-dashboard-conversation-contracts
- recover-ingestion-target-deliveries
- repair-notification-metadata-read
- repair-telegram-delivery-truth
- repair-whatsapp-identity-reconciliation
- restore-butler-control-plane-liveness
- restore-drill-recovery-truthfulness
- restore-ingestion-console-spec-coverage
- specify-improvement-proposal-spine
- specify-messenger-voice-egress-contract
- specify-roster-identity-owner-operations-overlay
- specify-telegram-confirm-interaction-contract
- specify-telegram-continuity-routed-context-contract
- specify-youtube-playlist-learning-signal
- strict-owner-telegram-wake-recovery
- surface-known-contact-drops
- true-bidirectional-email-correspondence

Full source/ownership recovery is retained under the coordinator-reviewed ignored PRIMARY packet: literal original fields and T1/T2/T3/T4 rows, full 544 requirement/1844 scenario bodies (including five incoming mandatory-home specs), 507 advisory groups (484 qualified capability IDs and 23 named pillar homes without product IDs), 33-holder envelopes, complete public PR exact-head patches/hunk allocations, full Dolt universe, source hashes, verification plan and recovery manifest. Advisory IDs are checked against main/active/archive/public prospective PRs but are not canonical allocations; recheck fresh before authoring. No private PR content, settings, production/provider/live or credential access is authorized.

## Capabilities

Future new or modified survivor homes and retirements are the full original T1/T2 family retained above; none is added, modified or removed at this docs-only stage. Incoming provider/Butler/dashboard mandatory homes retain actual source-owner boundaries.

## Impact

Current tracked scope is these four change-documentation files only. Future signed documentation/spec/pillar/pointer scope is the complete field design; no runtime behavior/source implementation is allocated here.

## Actual ordinary decision authority

Source-only factual alignment is recorded in **bu-2w15b5** under the existing authorities bu-vu7fb, bu-7exe4.1, bu-lsxqb0.3, bu-lsxqb0.6, bu-lsxqb0.10, bu-c8gfj.5 and RFC0003/0005/0015/0016. bu-1fe7xv retains actual startup/load/relay/fallback/deadwire source and proof ownership. This record grants no family signature, spec move, holder archive, runtime change or executed-proof credit. See [the complete decision record](design.md#decision) for all seven exact choices, placements, authorities and alternatives.

## Original completion criteria remain pending

1. Change signed off by the owner before any spec file moves
2. after archive: spec-trace-check.py --strict passes on every touched spec
3. grep shows each retired spec name has zero live citers outside openspec/changes/archive
4. each T3 contradiction in cluster B is resolved with the decision bead cited
5. no requirement text exists in two live specs (reviewer spot-check of the T2 duplicate clusters).

No criterion is completed by this four-file stage. The full canonical criteria remain binding in bu-lsxqb0.17; no spec is moved or retired, no active source holder archived, and no post-archive strict or runtime proof claimed.
