# Shape condensation plan (2026-10-03)

**Reader:** the owner deciding whether to adopt a leaner doctrine and a one-home-per-capability spec corpus, and the agents who will execute it. **Status:** proposed planning record under the th-projects shape workflow (Workflow 3, audit and maintain). It adopts nothing by itself: doctrine amendments need owner adoption, spec consolidations need a signed-off OpenSpec change each. Reviewed at baseline `252b77c76`; the change itself is based on `174e6971c`, one docs commit later. Evidence tables: [2026-10-03-shape-condensation-evidence.md](2026-10-03-shape-condensation-evidence.md).

## What was measured

| Pillar | Files | Size | Finding |
|---|---|---|---|
| Doctrine (`about/heart-and-soul`) | 7 | 96 KB | Authored and coherent, but three files carry inventory, process, or normative values that other pillars own [Observed] |
| Design contracts (`about/legends-and-lore`) | 40 | 785 KB in 37 RFCs | 7 implemented RFCs still carry their full pre-implementation body; one doctrine contradiction; scope records stale [Observed] |
| Capability specs (`openspec/specs`) | 195 | 3.7 MB, 2,026 requirements | 2,222 of 2,557 requirement blocks (specs plus active deltas) have no ID, Source or Scope line; 189 of 195 specs cite no test [Observed, validator] |
| Topology, standards | 16 | 105 KB | Current; receive content moved out of the other pillars [Observed] |
| Pursuit dossiers (`docs/redesigns`) | 15 runs | 729 KB | Eight dossier bodies have no external citer; the ideas ledger stopped absorbing dropped proposals after run 04 [Observed] |

Method: the project-shape scanner, the mechanical spec-trace validator, a per-spec inventory, then five independent read-only reviewers (dashboard, platform, surfaces, domain models, RFCs), each producing verdicts with evidence labels. The orchestrator synthesized; nothing below was reviewed by the agent that wrote it.

## The root cause, in one paragraph

Requirements have no identity, so nothing can tell two statements of the same rule apart. Every feature pass added a new spec or a new requirement block rather than amending the one that already existed, and cross-references were written in both directions (spec cites doctrine section, doctrine cites spec requirement) instead of one owner per rule. The result is 54 duplicate clusters where one rule is stated two to five times, 42 places where the copies now contradict each other or the code, and 55 sections sitting in the wrong pillar. Condensing the prose without first giving each requirement one identified home would reproduce the same drift within a quarter.

## Target shape

Totals from the reviewers' target lists (Inferred estimates; the count matters less than the shape):

| Family | Specs now | Specs after | Size now | Size after |
|---|---|---|---|---|
| Dashboard | 28 | 25 | 999 KB | ~925 KB |
| Platform | 45 | 30 | 770 KB | ~650 KB |
| Butlers, connectors, modules | 60 | 50 | 1,001 KB | ~760 KB |
| Domain models | 62 | 40 | 845 KB | ~670 KB |
| **Specs** | **195** | **~145** | **3.6 MB** | **~3.0 MB** |
| RFCs | 37 | 33 | 785 KB | ~530 KB |

The shape rules behind the target list (full list in evidence table T1):

- **One capability, one spec.** Finance's eleven specs become a ledger, an ingestion, and an intelligence spec. The seven QA and healing specs plus RFC 0015 become a QA staffer spec and a QA investigation spec. Six memory specs become the catalog spec, a lifecycle spec, and the module spec. Seven education specs become a curriculum-graph spec and a learning-state spec. Three runtime-config specs become one.
- **Domain pages get domain homes.** `dashboard-api` keeps only the shared API core; its calendar, secrets, memory and issues contracts move to new `dashboard-calendar`, `dashboard-credentials`, `dashboard-memory` and `dashboard-issues` specs. `dashboard-domain-pages`, `dashboard-visibility` and `dashboard-butler-management` split along the same lines. This is the concrete shape for the blocked bead `bu-60pwv6.6`.
- **Shared contracts are stated once.** The connector base obligations (persistence, replay, heartbeat, multi-account) are stated in `connector-contract`, `connector-discretion` and `connector-registry` and referenced, never copied, by per-connector specs. Spotify's sole-authority rule is stated once in `connector-spotify`.
- **Specs hold WHAT; inventories, prompts and procedures leave.** Butler specs stop restating `butler.toml`, ports, tool lists and manifesto text (the copies have already drifted). Environment-variable tables, run commands, debugging how-tos, test plans and skill procedures move to `docs/` or `about/craft-and-care`.
- **Every surviving requirement gets an ID, Source and Scope line and a test citation as part of its rewrite**, so the validator can keep the corpus honest afterwards. This supersedes the blocked backfill bead `bu-60pwv6.7`, which would have labelled text that is about to move.
- **RFCs become decision records once their contract lives in a spec.** Seven implemented RFCs condense to status, decision and trade-offs; RFCs 0010, 0020 and 0030 become one cross-schema-read-exception register; RFCs 0018 and 0019 leave the pillar for doctrine and the ideas ledger.

## Doctrine dispositions

| File | Disposition | Basis |
|---|---|---|
| `vision.md` | Keep as is. | Already the condensed WHY; nothing in it is restated elsewhere [Observed] |
| `architecture.md` | Keep; one amendment for owner decision: either admit on-demand, column-allowlisted read exceptions (as RFC 0030 already does) or narrow RFC 0030 back to batch. | Doctrine says exceptions are batch-only; RFC 0030 is an accepted on-demand exception [Observed, D] |
| `security.md` | Keep. | Specs restate parts of it (core-credentials Tier 2 framing); the fix is in the spec, not here [Observed, B] |
| `v1.md` | Condensed in this change: scope boundary only. The descriptive inventory leaves (it had drifted: Pipeline, Self-healing, Telegram, Gmail described wrongly, ActivityWatch missing) and is replaced by names-only module, connector and dashboard registries under the original headings, so RFC 0018's anchor to `v1.md#connectors` still resolves and no capability silently enters or leaves scope (WhatsApp, Heartbeat and webhooks stay; ActivityWatch is added because it ships; Discord, metrics and document-renderer stay listed with their pending owner decisions). The stale Helm deferral and the July status line go. Making the adopted spec set the scope registry is the target after phase 2, not a rule adopted here. | [Observed, C1 and D; independent review fixed the first draft's silent scope change] |
| `development.md` | Condensed in this change to its four principles; test ladder, quality gates, git workflow and environment setup already live in craft-and-care, `CLAUDE.md` and `AGENTS.md`. Two rules that had no other home moved to craft-and-care in the same change: the SQL-safety gate rationale (testing-and-verification) and commits-explain-why (review-and-documentation). | [Observed; independent review] |
| `design-language.md` | Not cut yet. Roughly 350 of its 645 lines are normative values (voice and copy, type, motion, hue scopes, contrast floor) that the Dispatch spec also carries, but the two documents delegate rules to each other in both directions, so a cut without a matching spec delta loses rules (the easing rule and the copy bans exist only in doctrine). Planned as spec delta S-A2 plus a doctrine amendment that leaves the WHY sections (what the dashboard is and is not, non-negotiable rules, settled direction). | [Observed] |

## Owner decisions required before execution

The 33 reviewer questions (evidence table T5) first reduced to twelve candidate decisions. An independent adversarial review of the decision packet rejected four as already decided or not owner gates, and corrected the rest; eight remain, filed as decision beads under `bu-lsxqb0`. None is answered here.

1. Owner PII in the public tree: the owner's work email and employer domain sit in RFC 0017 § 1, two test files, and a runtime work-domain default. Remove and placeholder all four, with the git-history rewrite held as a separate question? (`bu-lsxqb0.12`)
2. Dashboard identity: doctrine already admits operator control; the live conflict is 'Not a chat app' versus the shipped global chat surface, and the shell spec's claim that everything is reachable exclusively through the dashboard. Depends on decision 3. (`bu-lsxqb0.1`)
3. Cross-schema reads: amend architecture.md to admit on-demand exceptions that are fixed projections (view or no-argument function) meeting every existing criterion, as accepted RFCs 0030 and 0010 already do, or narrow those RFCs back to batch? RFC 0020's refusal stands either way. (`bu-lsxqb0.2`)
4. Module selection and butler inventories: the module registry instantiates every module on every butler and enablement is runtime state, while non-negotiable 5 says selection lives in git. Make roster/ the declaration of record (with a contract test) and drop inventories from specs? (`bu-lsxqb0.3`)
5. Discord: still in v1 (accepted RFC 0018 says so; code is a self-declared draft) or deferred, which amends the RFC and v1.md and retires spec and code together? (`bu-lsxqb0.4`)
6. Metrics and document-renderer modules: declared by no butler, instantiated everywhere, and imported by two API routers via a Prometheus helper. Retire after moving the helper to core, or name an adopter? (`bu-lsxqb0.5`)
7. Self-healing: keep the module's direct-dispatch fallback as QA's specified degraded mode, or remove it so report_error only relays to QA? The dead spawner hard-crash wiring is deleted as cleanup regardless (`bu-1fe7xv`). (`bu-lsxqb0.6`)
8. Memory retention knob direction: the per-schema and fleet-wide tables govern different dimensions today, and a NULL fleet value disables a cap. May the fleet knob loosen limits, or only tighten? (`bu-lsxqb0.8`)

Resolved without the owner, by reconciliation inside phase 2 (recorded on the closed beads): the finance bulk path (accepted RFC 0012 already chose `finance.transactions`; the SPO-writing HTTP endpoint is drift; the quarterly budget-period enum is a standalone bug, `bu-aubi0k`), the credential table (RFC 0004 Amendment 3 makes `public.entity_info` the credentials store; `relationship.credentials` has no runtime reader and retires), QA discovery authority (specs hold the SHALLs, RFC 0015 keeps rationale), and RFC 0025 versus the beads-export CronJob (no contradiction: draft planning contract, default-off bridge, status quo stands).

Four implemented RFCs (0022 fleet-event bridge, 0023 delivery intent, 0031 entity graph, 0032 fleet case file) have no spec home. They cannot be condensed until a capability spec exists; the plan files them as spec-writing work rather than deciding for the owner.

## Execution program

Sequenced to respect active OpenSpec changes (55 open; `core-notify` alone has nine deltas) and the open children of `bu-60pwv6`. Each family consolidation is one OpenSpec change with its delta specs, signed off before any spec file moves.

| Phase | Work | Gate |
|---|---|---|
| 0 | This change: `v1.md` and `development.md` condensed; cumulative pursuit ledger; this plan and its evidence. | Owner adopts the two doctrine files by merging. |
| 1 | The eight decisions above, as decision beads; four spec-writing beads for the homeless RFCs. | Owner answers. |
| 2a | Platform consolidation: QA and healing to two specs; runtime-config to one; staffer-archetype retired to doctrine; testing and docs-information-architecture to craft-and-care and docs; the three live contradictions (S3 startup, unmeasurable ledger rows, self-healing load) resolved in the surviving spec. | Change signed off; `core-notify` split waits for its nine deltas to archive. |
| 2b | Finance, memory, education, identity consolidations (domain models). | Change signed off; education waits for or folds into `education-mind-map-lifecycle-integrity`. |
| 2c | Connector contract split and per-connector dedup; butler inventories stripped; account registries merged. | Change signed off; connectors with active changes (telegram-bot, switchboard, messenger) go last. |
| 2d | Dashboard domain homes (`bu-60pwv6.6` adopts T1) and `dashboard-credentials`; design-language spec delta S-A2 with its doctrine amendment. | Change signed off; decision 2 answered first. |
| 3 | RFC condensation: seven decision records, the read-exception register, 0018 and 0019 relocated, status vocabulary fixed. | After the owning specs from phase 2 land. |
| 4 | Pursuit history: fold runs 05 to 15 dropped proposals into the ideas ledger as themed clusters with unpark conditions; move the eight uncited dossier bodies to git history behind the cumulative ledger; retire the per-run north-star restatements. | Routine documentation bookkeeping once phase 1 confirms no dossier is still cited as provenance. |

Every phase-2 change carries the ID, Source, Scope and test-citation backfill for the specs it touches, so `spec-trace-check.py --strict` can become a CI gate when phase 2 completes.

## What this change does not do

It does not move, merge or delete any spec or RFC, does not touch `design-language.md`, `architecture.md` or `security.md`, does not fold the ideas ledger, and does not prune any dossier body. Those wait on the gates above. It also does not claim the size estimates are exact; they are reviewer inferences from per-requirement byte counts and will be replaced by measured numbers as each change lands.
