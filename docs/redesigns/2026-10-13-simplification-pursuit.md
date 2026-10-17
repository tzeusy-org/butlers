# Platform simplification pursuit: run 18 (2026-10-13)

**Reader:** owner deciding what cleanup and simplification work to release. **Status:** proposed planning record; not adopted requirements or deployment evidence. Audited at baseline `2c4c54c68` (Asia/Singapore, 2026-10-13 18:59 to 2026-10-14 03:10).

A non-standard-lens run of the JARVIS pursuit, aimed at the owner's goal: clean up and simplify the butlers platform. The question for every lens was what can be deleted, merged, collapsed or made single-home without weakening a guarantee. Eighteen read-only agents measured and audited the platform; a critic tried to falsify the boldest claims. 74 candidate moves reduce to fifteen ranked moves, eight owner decisions and eighteen vetted follow-ups, deduplicated against each other, every non-closed bead and the run-17 SDLC epic.

Structured evidence: `git show b747a55a530364d987fb37457c343193e9362fe7:docs/redesigns/2026-10-13-simplification-pursuit-data.json` (removed from the tree by run 20 under the two-newest-runs rule).

## Decision in view

Epic `bu-dss7he` is held by owner gate `bu-i4khaz`. Closing the gate releases sixteen children (fifteen moves plus one terminal reconciliation); the owner may instead close only chosen children. Every child carries a full Dispatch Readiness Packet in its structured fields (`bd lint`: zero warnings on 18 beads). The eight owner decisions below are not children and are not released by the gate.

## North star

A platform an agent can hold in its head: every concept has one home, one name and one mechanism; every route, tool, flag and file earns its place through a live caller; retired things are deleted, not carried beside their replacement; and no simplification weakens a trust guarantee.

## Where the complexity sits

From the `measure-complexity` lens at baseline. Complexity is concentrated, not spread evenly:

| Measure | Today |
|---|---|
| Largest functions | `Spawner._run` CC 301 over 2,332 lines; `MessagePipeline.process` CC 244 over 2,027 lines; 253 functions rank D-F |
| Most-churned code file | `frontend/src/api/types.ts`, 10,605 hand-written LOC; 136 of its 158 commits in 90 days also touched backend Python |
| Layering | 28 mutually-importing subsystem pairs; core imports the API layer at 5 sites |
| Names for roster code | five namespace aliases plus 22 file-path loaders |
| Schema history | 416 migration files / 59.6k LOC replayed by 13 daemons, plus a 5,832-line privileged `init-db.sql` |
| Dashboard API | 618 operations; 96 (about 5.3k handler LOC) have no caller anywhere |
| Owner-bound delivery | 9 parallel paths; 15 hand-built `notify.v1` envelopes |
| Tracked markdown | 372,611 lines, more than `src/butlers` code; 44% sits in `openspec/changes/archive` |

Prior cleanup mostly held: 10 of 16 retirements the QC lens checked landed and stayed gone (2 partial, 3 not landed, 1 regressed). The gap is anti-regrowth: growth outpaces deletion about 5:1 in code, and the retired-surfaces guard planned in run 17 (`bu-ly3lv5.13`) has not landed.

## Ranked moves

Trust repairs and trust-preserving simplifications lead; then single-home consolidations in dependency order. Cost is S/M/L. Every deletion names the survivor that keeps its guarantee.

### 1. Honest failure surfaces: delete the dead healing dispatcher and the QA Retry route that can never succeed, the unfireable calendar_event_end trigger, and the per-tick schema probes

`bu-dss7he.1` · Trust repair · cost S. QA replaced healing dispatch in #4383, yet api/routers/healing.py:360 still calls an MCP tool `retry_healing` that is registered nowhere, inserting orphan 'investigating' rows; the critic reproduced it. calendar_event_end is specified but cannot fire. Every probed column is created by the core chain. **Payoff:** About 1.4k LOC dispatch + 105 LOC retry fan-out + 722 test LOC + 63 LOC probe helpers + 67 LOC calendar_projection branch; one failure that impersonated a working control removed. **Existing beads:** Registers entries in the retired-surfaces registry once bu-ly3lv5.13 lands; bu-ly3lv5.13 wave 1 omitted this surface (runtime-core ledger note).

### 2. Schema isolation fails closed everywhere: delete the deployment-posture switch and the role-less pool bypass

`bu-dss7he.2` · Trust repair · cost S. db.py:559-591 drops SET ROLE when posture is the default 'dev', and Helm never sets BUTLERS_POSTURE, so k3s runs fail open (trust-machinery). The critic found a second hole: db.py:519 skips verification entirely when role is None. **Payoff:** Deletes one concept (posture) and its dual branches at db.py:17-27, 80, 107, 559-591 and 32-130; turns a documented guarantee into an enforced one. **Existing beads:** Adds evidence to bu-u03nw8 (compose-vs-Helm split leaves posture compose-only).

### 3. One trustworthy audit ledger: append-only public.audit_log with a DB-attested writer, absorbing switchboard.operator_audit_log, with append() in core

`bu-dss7he.3` · Trust repair · cost M. init-db.sql:436/464 grant UPDATE/DELETE on public.audit_log to every runtime role, contradicting core_092; backup_health.py:251-254 therefore treats it as forgeable. 12 switchboard sites write a parallel table nobody reads. Critic: write_audit_entry returns silently when pool is None, so the fold must not use that path as-is. **Payoff:** Drops 1 table and its CHECKs, 12 bespoke INSERTs, 2 of 5 core->api import edges; audit becomes tamper-evident at the DB layer. **Existing beads:** None open; completes epic bu-t141i's intent.

### 4. One route executor for live and recovered rows, then dissolve the pipeline module, then give Messenger its own deliver executor with the caller check preserved

`bu-dss7he.4` · Trust repair · cost M. Live (_routing.py:1193-1425) and recovered (switchboard_wiring.py:385-640) processors already drift: a recovered image route can be served by a non-vision model. Three lenses edit _routing.py, so one order avoids conflicts. Critic: notify.v1 has no request_context, so a direct deliver tool would drop today's trusted_route_callers check unless re-specified. **Payoff:** About 200 LOC of duplicate processor, about 155 LOC pipeline module and its config knob that can turn off ingress idempotency, and _route_execute_inner from 1,379 to about 700 lines.

### 5. One approval decision path and a caller-verified route retirement wave, guarded so uncalled routes fail at PR time

`bu-dss7he.5` · Trust repair · cost S. The uncalled approval twin records the generic actor dashboard:rest-api and skips the edit ban (operations.py:153,368). 96 of 618 operations have no caller; deep-retirement-wave-2 re-verified 38 of them (the raw list overstated). Critic found 67 production functions (1,331 LOC) whose only callers are tests. **Payoff:** 6 approval routes / 359 LOC; 38+ routes / about 2.1k handler LOC including routers/secrets.py 247 and provider_settings.py 387; about 330 LOC of redirects; 1,331 LOC test-only code in the first sweep; a smaller owner-auth perimeter. **Existing beads:** Lands its entries in the bu-ly3lv5.13 registry (adds kind=route); if .13 has not landed, ship the guard here and let .13 adopt it.

### 6. One database authority model: migrator, runtime and bootstrap logins, a roster-derived deny-by-default grant manifest, every definer in Alembic, init-db.sql down to roles and schemas

`bu-dss7he.6` · Trust repair · cost L. One login migrates, serves the dashboard and runs daemons, so runtime code owns immutability triggers and RLS and could disable them; init-db.sql:23-27/464 grant blanket DML on every public table to all 14 runtime roles; 41 of 45 literal role lists in migrations omit butler_concierge_rw. Critic: a naive deny-by-default would weaken DND FORCE-RLS fencing, so the manifest must carry it. **Payoff:** About 5,000 of 5,832 init-db.sql lines, 3 admin schemas, 3 bootstrap singletons, about 12 install/finalize/rollback functions, 4.2k LOC of text-pin tests replaced by catalog tests. **Existing beads:** Supersedes bu-yfd9v1 (a per-table review re-widens on every init-db rerun) and reshapes bu-fi32bc and bu-kqnum.8.8; the owner decides whether to close them in favour of this.

### 7. One Google credential exchange with one revoked-grant rule, then one Google account fleet for gmail, calendar and drive

`bu-dss7he.7` · Consolidation · cost M. 13 refresh-token exchanges in 10 files drift (gmail has no expiry margin; calendar and drive use 60s) and only 2 files use the shared revoked-grant classifier, so a revoked grant is detected differently per surface. **Payoff:** About 866 LOC of mint code to about 80; 214 LOC of client twins; 1,270 LOC of three managers to one fleet of about 400. **Existing beads:** Extends bu-ly3lv5.14, whose google_common slice covers only identical health classes and error helpers; touches bu-z6udd's OAuth callbacks.

### 8. One home per API shape: generate frontend types from the FastAPI OpenAPI schema, delete the hand mirror and barrel, retire the TS-fragment contract test

`bu-dss7he.8` · Consolidation · cost L. 136 of 158 types.ts commits in 90 days were paired backend edits; 432 shapes are hand-copied and only paths are checked, by a custom parser. types.ts is the most-churned code file in the repo. **Payoff:** 10,605 hand-written LOC shrink toward about 418 frontend-only exports; -1.3k barrel; -1,333 LOC parser test; one definition per payload. **Existing beads:** Complements bu-ly3lv5.9 (frontend lane): fewer hand types means less lint and typecheck surface.

### 9. One connector identity: a ConnectorDescriptor table from which Helm values, heartbeat, bearer-scrub labels, OAuth scopes, channel contracts and the dashboard catalog derive

`bu-dss7he.9` · Consolidation · cost M. About 10 hand lists drift: 9 of 13 dashboard catalog entries contradict deployed channel/provider; dashboard-api reaches connectors via 127.0.0.1 (oauth.py:1700, steam.py:753), broken under per-connector Deployments. **Payoff:** About 110 LOC catalog plus hand lists in heartbeat, bearer scrub, OAuth scope registry and channel_defaults; 46 of 82 Helm connector leaves. **Existing beads:** Depends on bu-ly3lv5.14's serve_health (descriptor is the only home of the health port); co-schedule with bu-lsxqb0.19's registry spec.

### 10. Jobs live beside their owners: Module.jobs() and roster JOBS maps replace the central per-butler handler map, validated at schedule sync, with one Chronicler projection job

`bu-dss7he.10` · Consolidation · cost M. scheduled_jobs.py holds 100 _run_* wrappers (1,682 lines) and a registry keyed by butler name kept in sync with 10 toml files by hand (drift bug bu-93y4rt); unknown names fail only at dispatch. **Payoff:** Most of 2,274 LOC scheduled_jobs.py, the 168-line registry and the background.py self-heal; 21 schedule blocks and 21 wrappers for Chronicler.

### 11. Migrate once from one runner: a public chain run once, daemons verify the head, and every chain squashed to a catalog-verified baseline (calendar tables move to their module)

`bu-dss7he.11` · Consolidation · cost L. Two runners (initContainer and lifecycle.py:147-181 in all 13 daemons) replay 416 files / 59.6k LOC, and 57-61 core files must be idempotent across 13 replays with hard-coded schema lists; bu-u20z6z and bu-hc9n55 are symptoms. **Payoff:** 416 files / 59.6k LOC to about 19 baselines; 5,783 LOC of downgrade; 123 migration test files / 32k LOC to per-chain catalog checks; template builds in CI get faster. **Existing beads:** Lands after move 6; needs bu-ly3lv5.12's catalog snapshot widened to owners, relacl, pg_policy, pg_trigger and pg_proc; makes bu-u20z6z's failure class disappear and reshapes bu-ly3lv5.8.

### 12. One owner-notify builder and one Switchboard-deliver transport for every producer

`bu-dss7he.12` · Consolidation · cost M. 13 files re-implement envelope, gating, attention ledger and transport; the route side already has one helper (_switchboard_route_dispatch.py:90). gap_interview_telegram.py:79 even POSTs to the Bot API directly. **Payoff:** 15 envelopes and 10 deliver call sites to 1 builder plus 1 transport; home._send_notify 162 lines and 74 lines of self-delivery copies. **Existing beads:** Gives bu-7exe4.6 its single entry point; bu-jjy9p (direct Telegram egress) should be treated as a trust defect and folds in as a slice.

### 13. One sink per signal: stdout plus a bounded WARN+ log table, an ingestion funnel read from public.ingestion_events, and OTel as the only daemon metrics API

`bu-dss7he.13` · Consolidation · cost L. Four log sinks with three readers on three different sinks; butler_logs is unbounded and unprovisioned for concierge (bu-hc9n55). The funnel queries four series nothing emits and PROMETHEUS_URL is set nowhere, so the page is degraded by construction. **Payoff:** About 250 of 419 LOC core/logging.py, the log scanner's file path, core/prometheus.py 148, about 250 of 577 LOC funnel, 32 PROMETHEUS_URL branch refs, 19 metric definitions. **Existing beads:** Ends bu-hc9n55's failure class; makes bu-468ck moot (its series has no producer); corrects bu-i2yb08; extends bu-ez3ylq beyond dashboard counters.

### 14. Helm is the only deploy path: delete `butlers deploy`, compose --prod and the compose observability profile, and replace the restore-drill executor with an ephemeral-Postgres CronJob

`bu-dss7he.14` · Consolidation · cost M. Two compose-based deploy paths remain beside Helm and need a guard (d444b854d) against double-running schedulers; the restore-drill executor needs Docker cgroups, iptables and sudo and is absent from Helm, yet carries 3 roles, 2 schemas and about 650 init-db lines. **Payoff:** core/deploy.py 957 + 1,169 test LOC; 391 LOC observability config; about 9k LOC of restore-drill executor, firewall, proxy and tests; later docker-compose.yml 972 and compose.sh 852. **Existing beads:** Re-scopes bu-viat6h.4 (port the executor) to the ephemeral CronJob and removes bu-kqnum.8's CREATEDB need; resolves bu-u03nw8 toward Helm-only; bu-qjm94x.1 must give live_listener a Helm home first.

### 15. Git history is the archive: delete COPY_INVENTORY.md, triage the 579 never-landed archived requirements, then delete openspec/changes/archive and sweep expired active changes

`bu-dss7he.15` · Consolidation · cost S. The archive is 44% of tracked markdown (2,024 files, 10.5 MB) and appears in every agent grep; COPY_INVENTORY.md is a 4.9k-line generated file AGENTS.md calls a queued-PR conflict hazard. Critic: deleting the archive outright would bury 579 never-landed requirement units. **Payoff:** -4,896 lines and one CI step pair; -165k lines / 2,024 files, a 372-LOC guard, its 525-LOC test and a 183 KB baseline; about 1.5 MB of stale changes. **Existing beads:** Re-scopes bu-tk618 (decide the 579, not burn them down via the archive), bu-lsxqb0.17 (removal-time check) and bu-lsxqb0.22 (archive is 10x the dossier bodies); cancels bu-60pwv6.7's Source References backfill direction.

## Owner decisions

Real candidates that need an owner answer before any deletion lands; none is a child of the epic.

- Owner decision: activate RFC 0023 as the only approval delivery path, or delete its dark 2,845 LOC / 7 tables (critic confirmed it is dark and its worker still starts).
- Owner decision (bu-lsxqb0.4, stale per v1.md:64-65): close it as 'retire' and delete discord_user.py (about 1.7k LOC); deletion must not land beside an open decision.
- Owner decision with bu-lsxqb0.1/.2: collapse Concierge (no caller for 5 weeks) into the dashboard read models, removing a butler; moots bu-hc9n55.
- Owner decision (bu-s6tb7h): settle the receiver-derived liveness cutover at chart level, then delete the legacy liveness path and flag (values.yaml:100 still defaults to 0).
- Owner decision (bu-lsxqb0.2): fence cross-schema SQL to the owning roster; the 18-file claim is UNVERIFIED by the deep pass.
- Owner decision: one feature, one normative home (spec delta + tasks; RFC only for cross-cutting contracts) and drop the Source References mandate.
- Owner decision: align General and Messenger manifestos with shipped config (General's Steam, inert custody scaffolding, Messenger's calendar duties).
- Design addendum after move 6: re-home the runtime-probe capability plane (2.2k src / 10.9k test LOC, an Ed25519 keyring) onto per-process DB grants; supersedes bu-4cjab, reshapes bu-d8i2s.

## Critic's verdicts

The critic re-ran the boldest claims against source. Four moves as first written would have weakened a guarantee; each ranked move carries the fix.

- FLAG: deny-by-default manifest would weaken DND single-writer and generation fencing
- PARTIAL: operator_audit_log fold is write-only and safe to merge, but the proposed call path drops guarantees
- FLAG: Messenger's direct deliver(notify.v1) tool loses today's Switchboard-only caller check unless one is re-specified
- PARTIAL: deleting openspec/changes/archive would bury 579 never-landed requirement units
- CONFIRMED: healing dispatcher is dead and the QA Retry route can never succeed
- CONFIRMED: sampled zero-caller dashboard routers
- CONFIRMED: RFC 0023 approval delivery is dark, and the live idempotent path is separate
- CONFIRMED: scheduler per-tick schema probes are vestigial
- PARTIAL: deleting the posture switch strengthens isolation but does not make it always fail closed
- PARTIAL: the liveness cutover is not settled at chart level
- GATED: Discord deletion bypasses an open owner decision
- CONFIRMED: restore drill absent from Helm; Concierge has no routing callers

## Platform health board

Grades from the lenses (excellent / good / fair / poor / broken), grouped. Per-lens grades are in the data file (`jq '.audits[].grades'`).

| Area | Grade | Basis |
|---|---|---|
| DB authority model and grant hygiene | poor | one owning login for migrations, dashboard and daemons; blanket public DML |
| Audit integrity | poor | the canonical audit table is mutable by every runtime role |
| Dashboard route surface | poor | 15.5% of operations have no caller |
| Outbound owner delivery | poor | 9 mechanisms, 15 envelopes |
| Connector identity | poor | about 10 hand lists; 9 of 13 catalog entries contradict deploy |
| Logs and metrics | poor | 4 log sinks, 2 metrics libraries; the funnel queries series nothing emits |
| Anti-regrowth protection | poor | no retired-surfaces guard yet |
| Migration shape | fair | works, but runs twice and replays global DDL per schema |
| Module ABC and taxonomy | fair / good | small and tested; the production loader skips dependency checks |
| Frontend route and page set | good | one shell-capability manifest; knip finds no dead files |
| Inbound routing | good | connectors, chat, delegation and events converge on Switchboard |
| Traces | good | one OTLP path |
| Owner-auth isolation | good | dedicated login plus connect-time reach assertion; the template for move 6 |
| Landing fidelity of past code retirements | excellent | every grepped retirement had zero live references |

## Vetted, not ranked

Real candidates held back as later slices, direct beads or next-run material. Each is in the data file under `.synthesis.deferred`.

- After move 12: one durable owner outbox replacing four queue families.
- Vetted (L): one model-attempt loop shared by Spawner, DiscretionDispatcher and structured_classify (Spawner._run CC 301).
- Vetted (L): one roster namespace, deleting 455 LOC of alias loaders; after move 10 moves job code.
- Vetted (S): one declared-only module loader that enforces dependencies, with defaulted ABC members.
- Vetted (S): trim Messenger's 16 unused calendar tools and use one tool-group helper.
- Vetted (S): strict butler.toml schema rejecting unknown keys; drop 93 restated-default lines.
- Vetted (S): 59 never-set env knobs and 11 settled toggles become constants.
- Vetted (S): PUBLIC_BASE_URL derives the 9 site URL env vars.
- Vetted (M): one meta.sources_degraded field replacing five names.
- Vetted (S): one KeysetPage and one cursor codec replacing 14.
- Vetted (M): one kit for empty, error, loading and tile states (-600 to -900 LOC).
- Vetted (M): replace the 2,050-line visual-role ESLint rule with a grep-grade check (reshapes bu-earu7e).
- Vetted (M): butler detail drops tabs duplicated by first-class pages, QA first.
- Vetted (M): one Home Assistant websocket client shared by connector and home module.
- Vetted (S): the Spotify connector uses SpotifyClient.
- Vetted (S): connectors/ keeps only connectors and their kit; fleet primitives move home.
- Vetted (S): delete 5 GEMINI.md forks and 7 test-only prompt-file readers; register in bu-ly3lv5.13.
- Covered: split across moves 1 and 14 and the Discord decision.

## Ledger feedback

The lenses left 101 notes on existing beads (stale, under-scoped, superseded). No existing bead was changed. Read them with `jq '.synthesis.ledger_feedback[] | {bead, note}'`. The ones that change what to do:

- `bu-lsxqb0.5` and `bu-60pwv6.2` are stale (work landed); `bu-60pwv6.32` was closed but the duplicate SVG is still in the tree.
- `bu-ly3lv5.13` (retired-surfaces registry) and `bu-ly3lv5.14` (backend kit) have not landed; several run-18 moves register into or extend them.
- `bu-yfd9v1` is superseded by move 6; `bu-viat6h.4` is re-scoped by move 14; `bu-tk618` and `bu-lsxqb0.17` are re-scoped by move 15.
- `bu-jjy9p` (gap interview's direct Telegram send) is a Messenger-sole-egress trust defect, not a P2 refactor; it folds into move 12.
- `bu-lsxqb0.4` (Discord) is answered in `about/heart-and-soul/v1.md:64-65` and can close as 'retire'.

## Method and access

- Orchestration: 18 agents in 9 batches of 2, about 55 minutes apart (never more than 2 in flight). The orchestrating session was interrupted after batch 6; batches 7-9 resumed from a new session against the durable harvest file, with no agent lost or re-run. Workflow runs `wf_270c6765-e10`, `wf_86440d47-4be`, `wf_dc0361e5-8e7`, `wf_de45004d-b78`; zero agent errors. Models: opus/high for measurement, audits, designs and critique; sonnet/high for the QC, frontend, knowledge and observability lenses.
- Agents were read-only except for scratch files outside the tree; measurement used `rg`, `wc`, AST and radon scans and `git log`. No test suite, DB or live stack was run, so LOC payoffs are static counts; each owning bead measures its own deletion.
- Access: `git show b747a55a530364d987fb37457c343193e9362fe7:docs/redesigns/2026-10-13-simplification-pursuit-data.json | jq '.audits["<lens>"]'`; lens keys are in `orchestration.batch_plan`. `jq '.synthesis.ranked_moves[] | {rank, title, bead}'` lists the moves. Long strings in `audits` are capped for size; the harvest file was not committed.
- Retention: this PR removes `2026-10-06-jarvis-pursuit-data.json` from the tree per the two-newest-runs rule; read it with `git show fb0dad17316c0b20869e7b4de97ea0ba97fbb1a6:docs/redesigns/2026-10-06-jarvis-pursuit-data.json`.
