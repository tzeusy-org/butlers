# SDLC velocity pursuit: run 17 (2026-10-06)

**Reader:** owner deciding what CI, test and cleanup work to release. **Status:** proposed planning record; not adopted requirements or deployment evidence. Audited at baseline `9cc43f3e5` (Asia/Singapore, 2026-10-06 21:40 to 2026-10-07 08:40).

A non-standard-lens run of the JARVIS pursuit, aimed at the owner's four targets: CI under 7 minutes, test condensation, code cleanup and deduplication with legacy retirement, and th-engineering cruft cleanup. Twenty-four read-only agents measured CI, tests and code; a critic re-checked the boldest claims. Fifteen moves survive deduplication against the held five-minute-lane program `bu-gvut0`, 426 non-closed beads and prior engineering proposals.

[Structured evidence](2026-10-06-sdlc-velocity-pursuit-data.json)

## Decision in view

Epic `bu-ly3lv5` is held by owner gate `bu-7lh5ew`. Closing the gate releases sixteen children (fifteen moves plus one terminal reconciliation); the owner may instead close only chosen children. Every child carries a full Dispatch Readiness Packet in its structured fields (`bd lint`: zero warnings on 18 beads).

Two decisions sit beside the gate:

- **The held program `bu-gvut0`.** This run measured what that program planned to measure. The table below gives a keep / reshape / supersede verdict per child; no `bu-gvut0` bead was changed.
- **Doctrine.** `about/craft-and-care/testing-and-verification.md` sets a five-minute p95 per lane. The owner's new target is end-to-end: under 7 minutes wall clock for a PR and for a merge-queue run. The two are compatible (lanes of about 5.5 minutes or less give about 7 minutes of wall clock), but the wall-clock budget is not yet doctrine. Adopting it is an owner amendment; no child adopts it.

## North star

Fast, honest feedback: every PR gets a trustworthy verdict in under 7 minutes wall clock and the merge queue stays the terminal broad gate; speed is never bought by silently deleting assurance; one home per helper; legacy retired, not carried.

## Where the time goes today

Measured over 25 recent successful runs per event (lens `measure-ci-critical-path`):

| Path | Today | Long pole |
|---|---|---|
| pull_request, full matrix | p50 about 807s (10-17 min wall) | integration shard 5 (629s), frontend (505s), preflight (464s, of which 300s re-collects the corpus to verify hand-kept manifests) |
| merge_group | p50 754s, p95 805s | integration shard 5 is the critical path in 84% of runs |
| push to main | 6-8 min | re-runs frontend, e2e and guards the queue already proved |

Fixed costs dominate more than test bodies. Every pytest process pays 4.4-5.7s importing `sentence_transformers` it rarely uses; every shard spends about 22-25s starting a Postgres container no test connects to, and downloads about 2.6 GB of CUDA wheels; about 30% of integration test-sum is re-running migrations into fresh databases. Condensing tests by count buys little: 83% of unit tests take under 5% of unit time.

### The derived path under 7 minutes

From the `deep-seven-minute-plan` design, each step measured or derived from named runs (see the data file). Order matters: trust first, then the cheap fixed costs, then structure.

| Step | Moves | merge_group effect |
|---|---|---|
| Close the preflight fail-open; coverage off the required path | 1 | -45s; restores 7 merge-blocking checks |
| Per-shard fixed overhead and the import tax | 4, 5 | -80 to -100s per shard |
| One computed partition, route job, sixth integration shard | 6 | integration max 902 to 752 test-s; preflight leaves the path |
| Frontend split and Vitest per-file cost | 9 | frontend about 505s to about 210-250s |
| Template-cloned integration databases | 8 | integration body about 180-245s (clone cost unmeasured) |
| **Target** | | **p95 about 410s with the sixth shard alone; about 330-350s with templates** |

PR runs get the same graph, plus an honest planner (move 7) that scopes far more PRs (replay: 14 to 57 of 145 branches).

## Ranked moves

Trust repairs to the CI signal lead; speed follows in critical-path order; then condensation, cleanup and guidance. Cost is S/M/L.

### 1. Fail-closed `check`: read every needed job's verdict, take coverage off the required path, and put a timeout on every job

`bu-ly3lv5.1` · Trust repair · cost S. Today a red preflight cannot block a merge: `check` runs with always() and its gate script never reads the preflight result (critic CONFIRMED, ci.yml:1903-1925; ruleset 22281319 requires only check/guards/frontend). **Payoff:** Restores 7 advisory checks to merge-blocking; about 45-49s off both PR-full and merge_group critical paths (check p50 54s to about 5-8s, measured from the push-event check); caps hang cost at about 2x p95 (one incident cost about 340 runner-min). **bu-gvut0:** Adds to bu-gvut0.5.5's audit list (fail-open and missing timeouts); no overlap with other children.

### 2. Make the nightly lane honest and owned: triage the red backlog, fix the time-bombs, and escalate a second red night to a bead

`bu-ly3lv5.2` · Trust repair · cost M. The only time-bomb detector has been red for ten weeks, so its 99 failing node ids (34 in test_approval_delivery_authority_transport.py, 22 in test_meeting_debrief_db.py) are invisible. **Payoff:** About 78 runner-min per night reclaimed once green or bounded; 74 future main-breaking failures caught early; restores the exact-image sandbox assurance.

### 3. Stop stale-branch push storms: prune merged and stale remote branches, delete branches on merge, and clear branch-scoped caches

`bu-ly3lv5.3` · Trust repair · cost S. A 2026-10-03 storm queued about 1,515 junk runs and starved the runner pool (worst PR waited 5h50m against a 12.8 min p50); the 10 GB cache budget is 99% non-main refs, so PR `npm ci` misses the cache. **Payoff:** Removes the measured multi-hour runner-starvation class; restores cache hits; saves about 39 min per session-link body fix (4 in two weeks).

### 4. Pay the embedding stack only when it is used: lazy sentence_transformers, retire the memory module's file-path sibling loader, and an import-time budget test

`bu-ly3lv5.4` · Speed · cost S. Every pytest process pays 4.4-5.7s and about 820 MB to import sentence_transformers via approvals -> decision_memory -> memory.storage (memory/embedding.py:9), confirmed by four independent importtime measurements. **Payoff:** Per process -4.5 to -5.7s and -820 MB; full unit collection 25.2s to 16.4s; about -80 to -116s off today's 300s preflight manifest step; single-file agent floor 9.4s to 3.7s (about 1-2s with the conftest slice). **bu-gvut0:** Independent of bu-gvut0; shrinks bu-gvut0.5.1's step before any inventory change.

### 5. Per-shard fixed-overhead budget: drop the orphan postgres service, lock CPU-only torch, cache the venv, and A/B workers and the coverage core

`bu-ly3lv5.5` · Speed · cost S. Every shard spends about 22-25s initialising an unused Postgres container, downloads about 2.6 GB of CUDA wheels it never uses, and runs 3 xdist workers on a 4-vCPU runner for a reason that only applies to the shared local host. **Payoff:** Derived about -80 to -100s off the backend critical path per shard (container -22s, startup -45s, install -6 to -13s, tail -9 to -19s); sysmon cut tracer overhead from +89% to +29% in a local proxy; 4 workers bound up to about -100s per unit shard if CPU-bound (A/B decides). **bu-gvut0:** Not covered by bu-gvut0; complements bu-gvut0.5.

### 6. One computed shard partition and one inventory: replace the ten hand-kept manifests, collapse the copy-pasted shard jobs into a matrix, fold the planner into routing, and fund a sixth integration shard

`bu-ly3lv5.6` · Speed · cost M. The 300s 'Verify CI test shard manifests' step and 52s budget step re-collect the corpus 13 times; every new test file needs a hand manifest edit (85 manifest commits since 2026-08-31) that also escalates the PR planner to the full matrix; integration shard 5 is the persistent long pole. **Payoff:** Preflight inventory 352s to one collection (about 22s local, about 45s CI); about 1,000 ci.yml lines removed; integration max 902 to 752 test-s with six shards (LPT simulation); PR full path -22 to -34s from the plan fold; the register-every-test-file toil disappears. **bu-gvut0:** Supersedes bu-gvut0.5.1 (inventory reuse) and bu-gvut0.5.2 (rebalance); reshapes bu-gvut0.5's 'no shard added' non-goal.

### 7. An honest planner: changed docs, skills, manifests and frontend paths select their reader tests instead of escalating, with a cost ceiling and named triggers

`bu-ly3lv5.7` · Speed · cost S. Only 4 of 50 non-docs PRs are scoped today; .github/ is on the full-suite allowlist (scoped_runner.py:49-63) so every test-adding PR loses the scoped lane; docs-only PRs that change text pinned by tests caused 2 merge-queue ejections (critic corrected 3 to 2). **Payoff:** Replay: scoped PRs 14 to 57 of 145 branches and 15 more leave the backend matrix; each moved PR goes from about 558-807s to the scoped bound (about 3-4 min once move 6 lands); removes the 1,618-2,194s scoped outliers and the PR #4286 ejection class. **bu-gvut0:** Precondition for bu-gvut0.5.3's shadow baseline; does not touch merge_group.

### 8. Provision integration databases by cloning a migrated template per worker instead of re-running migrations per test

`bu-ly3lv5.8` · Speed · cost M. About 30% of integration test-sum is DB provisioning with no template reuse (1.4-1.6 ks of 4.5-5.0 ks); test_optional_schema_lifecycle_db.py alone is 169.5s over 30 cases. **Payoff:** If a clone costs <= 1s (UNMEASURED; slice 1 measures it), about 1.0-1.2 ks CPU and 50-60s per integration shard wall; with move 6's sixth shard, integration p95 body about 180-245s; retires schema_standins.py (729 LOC) and most of an 821-LOC parity test. **bu-gvut0:** Reshapes bu-gvut0.4 from profiling into implementation (the profiling is done); lifts its 'no fixture-scope change' non-goal.

### 9. Frontend lane under four minutes: static gates as a parallel job with an ESLint cache, cut Vitest per-file cost, then shard Vitest under one required name

`bu-ly3lv5.9` · Speed · cost S. The frontend job runs lint (53s), build (42s) and Vitest (367s) in series; 462s of Vitest is collect overhead, and 74 DOM-free files pay jsdom setup. **Payoff:** Frontend critical path roughly max(static about 125s, Vitest) instead of their sum, about -140s before sharding; up to about -260 cpu-s of collect from the date-fns fix; two shards about 210s; about 600 runner-seconds per merge from the push-to-main trim. **bu-gvut0:** Reshapes bu-gvut0.5.4: fix per-file cost before sharding, and split static gates into their own job.

### 10. Fail before CI, react at the first red shard: an enforced pre-push hook for tree-deterministic guards, the session-link fix at the source, and a red-run reaction kit

`bu-ly3lv5.10` · Speed · cost S. 10 of 60 failed PR runs in two weeks were static, tree-deterministic failures CI could have been spared; red PRs lose about 12.5 min each to late reaction and repeat cycles of 21-25 min. **Payoff:** About 10 red PR runs per two weeks avoided; about 200 agent-minutes per 45 PRs from earlier reaction; about 2.6 h of PR lead time per fortnight from the session-link class.

### 11. Executable condensation proof and the first three clusters: coverage-context subsumption, a survivor ledger, and route-table contract tests on one module-scoped app

`bu-ly3lv5.11` · Condensation · cost M. 83% of unit tests take under 5% of unit time, so condensing by count buys little; the cost sits in per-test app construction in about 49 files, and bu-gvut0.2/.3 lack the proof machinery to delete safely. **Payoff:** First clusters: 172 cases worth 225-288s of test-sum fold into tables on one app; shared-app fixture about 1,215s CPU (about 81s per unit shard wall) at today's tracer cost, about 27s per shard after sysmon; about 700 LOC of copied migration-loader helpers single-homed. **bu-gvut0:** Reshapes bu-gvut0.2 and bu-gvut0.3 to cost-based targets and supplies their proof gate; decouples condensation from the seven-minute target.

### 12. Behaviour over text: replace the ci.yml string pins, doc-phrase pins and migration SQL-substring tests with truth tables, one data-driven checker and a catalog snapshot

`bu-ly3lv5.12` · Condensation · cost M. Text pins break on harmless edits and force rewrites on every CI or doc change (the ci.yml pin file changed in 5 of 5 CI restructures in 60 days); 30 SQL-substring files were added since September, about 100 LOC each. **Payoff:** About 300 of 493 LOC of ci.yml string asserts; 64 tests / 2,287 LOC of doc pins collapse to one checker plus data; up to 182 cases / 3,070 LOC of migration substring tests; about 40 module boilerplate tests; each future CI restructure and migration PR avoids a test rewrite. **bu-gvut0:** Part of bu-gvut0.2's corpus but a distinct class; file under this epic.

### 13. A retired-surfaces registry with a guard, and the first verified retirement wave

`bu-ly3lv5.13` · Cleanup · cost S. Retirement intent has no home: 85 untracked backward-compat markers and 8 bespoke absence tests of 77-223 LOC each; about 2.7k LOC of zero-reference or test-only code exists today. **Payoff:** Wave 1 about 3.95k LOC verified (4 test files / 49 tests leave unit-2, unit-4 and integration-3); each future retirement costs 6-10 TOML lines instead of a bespoke test; the guard adds under 3s to guards.

### 14. One home per backend mechanism: a connector ingest, pool and health kit, one router DB dependency, and an AST duplicate ratchet

`bu-ly3lv5.14` · Cleanup · cost M. 24 ad-hoc ingest sites, about 10 copies of pool bootstrap, 71 json.loads guards, about 473 LOC of near-identical health servers and 64 router stubs (critic corrected from 54) mean every connector or router change is an N-site edit and a merge-conflict hotspot. **Payoff:** About 1.3k LOC removed across connectors (est. -600 ingest/pool, -700 observability), about 300 LOC of router stubs, about 350 LOC of small helpers; test patching moves from 288 refs to one seam; the ratchet stops regrowth for about 3.9s in guards.

### 15. A guidance diet with a staleness guard: bd prime under 10 KB, AGENTS.md under the 32 KiB cap, and dead references failing at PR time

`bu-ly3lv5.15` · Guidance · cost S. Every session loads about 45 KB of bd prime (26 entries stale or superseded, e.g. merge_pr_exact_base guidance after the merge queue) plus 38.8 KB of AGENTS.md, above Codex's 32 KiB cap; at least 5 guidance references point at files that no longer exist. **Payoff:** About 37 KB (about 9k tokens) less context per session; the last 6 KB of AGENTS.md visible to Codex again; dead references caught at PR time instead of in an agent's failed command.

## The held program bu-gvut0, child by child

Recommendations from the `deep-seven-minute-plan` agent, checked against this run's measurements. The owner decides; nothing was changed.

| Bead | Verdict and reason |
|---|---|
| `bu-gvut0.1` | RELEASE-NOW, reshaped. The 25-run baseline already exists (measure-ci-critical-path timings.json, measure-test-cost costs.json). Persist those scripts and outputs instead of re-sampling; the remaining work is S. |
| `bu-gvut0.2` | KEEP, but decouple from the 7-minute target: 83% of unit tests take 4.2-4.7% of unit time, so condensing by count buys <5%. The speed lever is fixture economics in the 49 top files (shared app per module, sysmon), not deletion. |
| `bu-gvut0.3` | KEEP as a clarity/maintenance item. It is not on the critical-path order, for the same Pareto reason as bu-gvut0.2. |
| `bu-gvut0.4` | SUPERSEDE as an investigation; the profiling is done (30% of integration test-sum is provisioning, no template reuse). Reshape into the template-clone implementation. Its non-goal 'no fixture-scope change' blocks the only lever that reaches 7 min on integration. |
| `bu-gvut0.5` | RESHAPE: the outcome should be the target DAG and the per-job budget table (route 12, shard ≤330, guards ≤180, vitest ≤210, check ≤8). Its non-goal 'no shard added' must allow the 6th integration shard funded by the plan/preflight/frontend-static folds (peak concurrency held at 14). |
| `bu-gvut0.5.1` | SUPERSEDE: one collection covers both lanes (measured 22.3s locally for 22,551 nodes), and the manifests it preserves are deleted by the computed partition. Its contract list (exact-once, mixed-marker once per lane, non-empty shard, collection errors fail, budget equals the executed population) carries over into check reconciliation. |
| `bu-gvut0.5.2` | SUPERSEDE: LPT partition from a weights file plus duration-ordered xdist scheduling (--no-loadscope-reorder) rebalance on every run. Its 'whole files stay intact' contract is kept. |
| `bu-gvut0.5.3` | KEEP held. The 7-minute full path must not depend on it. When released, its baseline should be the import-graph selector (ci-selective-testing), not the prefix map. |
| `bu-gvut0.5.4` | RESHAPE: move lint/gates/knip/build into guards (parallel, one run), and Vitest --shard 1/2 and 2/2 under a `frontend` fan-in that keeps the required name. The 'lint->knip->build->tests order' rule existed to stop masking; separate jobs under an ALL-success gate do not mask. |
| `bu-gvut0.5.5` | KEEP, and add to its audit: the check-preflight fail-open (ci.yml:1904 always() plus contract test test_ci_test_targets.py:440-448) and the missing timeout-minutes. |
| `bu-gvut0.6` | KEEP; reconcile against the budget table above rather than the 'five-minute lane' wording, since lanes ≤5.5 min yield ≤7 min wall. |

## QC of past CI-speed work

| Change | Verdict |
|---|---|
| #3946 shard by measured file cost (5 unit + 5 integration, exact-once manifests) | partial |
| #3991 merge_group trigger and terminal broad gate | source-confirmed-as-designed |
| #3991 single guards job | source-confirmed-as-designed |
| #3991 docs-only path filter skips backend shards | partial |
| #3991 push:main skips backend shards by design | source-confirmed-as-designed |
| #3991 test budget ratchet | partial |
| #4001 PR-only affected-test lane, fail-closed to full | partial |
| #4037 exclude check-preflight from fan-in shard loop | source-confirmed-as-designed |
| #4071 retried bash uv installer replacing setup-uv | source-confirmed-as-designed |
| #4162 preflight-bound scoped run (data point cited by bu-gvut0.5) | source-confirmed-as-designed |

## Critic's verdicts

The critic re-ran the ten boldest claims against source, CI and `gh`. Corrections are folded into the moves above.

- CONFIRMED (strengthened): preflight verdict is fail-open in the required `check` gate
- CONFIRMED: `.github/` is a full-suite trigger, so every test-adding PR loses the scoped lane
- CONFIRMED: eager sentence_transformers import dominates conftest import
- CONFIRMED with caveat: postgres:16 service is unused by tests, but DATABASE_URL is not inert
- CONFIRMED: unit lane runs 3 xdist workers on 4-vCPU runners for a local-host reason
- CONFIRMED: dead twins exist (jobs/chronicler.py has zero refs; tools/extraction_queue.py is test-only)
- CONFIRMED (corrected): route-guidance copies are duplicated, but differ by docstrings, not AST-identical
- CONFIRMED (corrected): router DB-manager stubs number 64, not 54
- CONFIRMED and worse: scheduled and release workflows are dead or red
- PARTIAL: 'docs-only PRs caused 3 of 6 ejections' holds for 2 of the 3 cited PRs
- UNVERIFIED: DB-, prod- and graph-dependent savings
- GAP: SDLC areas no lens covered

Two lenses graded the fan-in "good" because shards fail closed; three others, and the critic, found that the preflight verdict never reaches it. The source (`.github/workflows/ci.yml:1903-1925`) settles it: fail-open.

## SDLC health board

Grades given by the lenses (excellent / good / fair / poor / broken), grouped. Full per-lens grades are in the data file (`jq '.audits[].grades'`).

| Area | Grade | Basis |
|---|---|---|
| Required gating (check fan-in) | broken | preflight verdict never read; no job timeouts |
| Nightly / time-bomb signal | broken | red every night since 2026-07-24, 99 failing node ids |
| Per-session agent context | broken | 45 KB bd prime exceeds the hook cap, so sessions see a 2 KB preview |
| Test selection (planner) | poor | scopes about 7% of backend PRs; self-inflicted escalation on manifests, docs and skills |
| Test inventory and shard ownership | poor | 14 collections (352s) and 1,379 hand-kept manifest lines |
| DB provisioning | poor | no template reuse; the 228-revision core chain replays per module or test |
| Dependency and cache strategy | poor | CUDA torch in every job; stale branches fill the cache |
| Dead-code prevention (backend) | poor | no backend ratchet; about 2.7k LOC zero-ref or test-only |
| Frontend lane | fair | static gates and Vitest in series; 71% of Vitest CPU is per-file collect |
| Merge-queue stage | good | 43 of 45 PRs enter once; enqueue to merge p50 14.5 min |
| Architectural fitness tests | good | mostly AST- or behaviour-based |
| Venv provisioning | excellent | fresh uv sync from the hardlinked cache takes 1.2s |

## Vetted, not ranked

Real candidates held back: owner decisions, deferrals until a ranked move lands, or measurement-only work. Each is in the data file under `.synthesis.deferred`.

- Design addendum: a deterministic import-graph selector replaces the hand prefix map; shadow first, and it becomes bu-gvut0.5.3's baseline. Ranked after move 7 makes escalation honest.
- Owner decision: selective merge_group for provably isolated docs-only and frontend-only PRs saves about 7.9 queue-hours per 3.5 weeks but narrows the terminal gate; held until moves 6-7 land.
- Deferred: fail-closed affected-Vitest on PRs, after move 9 makes the full lane fast and stable.
- Deferred with frontend-pipeline#1 (same mechanism).
- Vetted: retire frontend/src/api/index.ts and settle one import path (about 1,290 lines, a conflict hotspot); next run or a direct bead.
- Vetted: production-mode knip pass; folds into move 13's ratchet family next run.
- Vetted: one renderWithProviders harness home (187 manual cleanup sites).
- Vetted: merge micro test files by module; small gain.
- Design addendum (L): rebaseline migration chains on a cadence behind a schema-catalog equivalence proof (409 files / 58k LOC regrown since the 2026-03-26 squash).
- Owner decision: retire the never-run release pipeline (about 404 LOC).
- Owner decision: compose-era retirement after splitting the restore drill from the compose base (up to about 7.3k LOC incl. 414 tests).
- Vetted: fold the 6-hourly e2e watchdog into the push-to-main run.
- Vetted: serial single-file pytest runs by default (about 10s per run).
- Vetted: provision frontend deps for worktrees from a root cache.
- Vetted: `make quick` mirroring check-affected through pytest_gate.
- Vetted: a weekly fleet lead-time ledger from bd and gh (measurement, no direct minutes).
- Out of repo: coordinator wakes on runnable ready work (lives in the dotfiles coordinator skill); up to about 230 min off median ready-to-claim.
- Deferred: flake ledger with one recorded merge_group retry; modest on current evidence (1 flake ejection in two weeks).
- UNVERIFIED (critic): 79 production functions only tests call; feed into move 13's ratchet, not a bulk delete.

## Method and access

- Orchestration: 24 agents in 12 batches of 2, 55 minutes apart (never more than 2 in flight); Workflow run `wf_1127ed0c-6c3`; zero errors or retries; about 2.8M subagent tokens. Models: opus/high for measurement, design and critique; sonnet/high for the QC pass, source-pin audit, frontend dedup and tooling cruft.
- Agents were read-only except for scratch files outside the tree; measurement was limited to `gh` reads, pytest collection, `-X importtime` and static analysers. No test suite, DB or live stack was run, so template-clone and Vitest-selection savings are derived, not measured; each owning bead measures first.
- Access: `jq '.audits["<lens>"]' docs/redesigns/2026-10-06-sdlc-velocity-pursuit-data.json`; lens keys are in `orchestration.batch_plan`. `jq '.synthesis.ranked_moves[] | {rank, title, bead}'` lists the moves. Long strings in `audits` are capped for size; the harvest file was not committed.
- Retention: this PR removes `2026-10-03-jarvis-pursuit-data.json` from the tree per the two-newest-runs rule; read it with `git show 9cc43f3e5443dd812354ff6ee7112d9575205288:docs/redesigns/2026-10-03-jarvis-pursuit-data.json`.
