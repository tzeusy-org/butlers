# Integration cost attribution: ten hosted merge-group runs, 2026-10-17

The slowest integration child per run has **410s median / 436s nearest-rank p95 job wall**; the slowest integration test command has **385.5s / 414s**. Both exceed the five-minute lane target in this descriptive cohort. Setup dominates the Relationship dedup and optional-schema files; migration bodies remain substantial. These are observations, not measured optimization gains or a passing seven-minute objective. Tests: +0 ~0 -0.

This follows the [2026-10-15 baseline](ci-cost-baseline-2026-10-15.md) with newer untraced hosted phase evidence. The content-blind data, now kept in the cited runs' CI artifacts and in git history before bu-29wkwj.1 rather than committed, retains run/head/attempt, artifact and receipt SHA-256, projected job/step metadata, source blobs, file phase aggregates and all quantiles. No JUnit, failure text, testcase identifiers, parameters, node hashes, classes or node-to-file maps are committed. No local Docker, profile, secret/provider or cluster operation ran.

## Cohort and method

Selection is the newest ten successful **first-attempt** CI `merge_group` runs targeting `gh-readonly-queue/main/` after **2026-10-15T09:32:15Z**. A bounded 100-success API index crosses the cutoff (latest 2026-10-16T15:49:15Z, oldest 2026-10-08T22:55:21Z), containing 20 eligible first attempts. All selected heads are exact first-parent commits of main at `27ba30d28a8dc6d907aa0791751dfe7afb86d58f`. All 60 integration artifacts are complete; **zero UNKNOWN replacements** were needed. Failure/cancellation/retry populations are outside this success-only sample; no all-run latency or causal rollout claim follows.

| Run (attempt 1) | Tested head | Created UTC |
| --- | --- | --- |
| [38927012323](https://github.com/tzeusy-org/butlers/actions/runs/38927012323) | `27ba30d28a8dc6d907aa0791751dfe7afb86d58f` | 2026-10-16T15:49:15Z |
| [38924036334](https://github.com/tzeusy-org/butlers/actions/runs/38924036334) | `d8599adf529da7ee43a2996ebf26325291ebfbce` | 2026-10-16T15:27:06Z |
| [38832816064](https://github.com/tzeusy-org/butlers/actions/runs/38832816064) | `51ec60a37b9ab2544fb172d80b989e34f9875349` | 2026-10-16T01:01:23Z |
| [38828475429](https://github.com/tzeusy-org/butlers/actions/runs/38828475429) | `633075b4443cdf59115de95e9704b6f2e4bdadd3` | 2026-10-16T00:15:46Z |
| [38828416026](https://github.com/tzeusy-org/butlers/actions/runs/38828416026) | `27694036608432b9bfd18e6d70964a6142b9c0a4` | 2026-10-16T00:15:12Z |
| [38820543948](https://github.com/tzeusy-org/butlers/actions/runs/38820543948) | `cb2b4a7981b82f61d7b182b434edf23330153db7` | 2026-10-15T22:57:25Z |
| [38814969781](https://github.com/tzeusy-org/butlers/actions/runs/38814969781) | `a5ab052d3b6458e0071283834a42129f0d698ebd` | 2026-10-15T22:07:57Z |
| [38812590287](https://github.com/tzeusy-org/butlers/actions/runs/38812590287) | `3f184e63a0817ae92087b7b50bd98c94f41b3659` | 2026-10-15T21:48:05Z |
| [38798033562](https://github.com/tzeusy-org/butlers/actions/runs/38798033562) | `7759a9da4f55a8aa9ed17674fe054033594191fe` | 2026-10-15T19:57:17Z |
| [38786791398](https://github.com/tzeusy-org/butlers/actions/runs/38786791398) | `656d545b3f5cac40ca0419ff8e0b8e1a49c65735` | 2026-10-15T18:34:49Z |

Each downloaded artifact must have exactly one matching observation, correct run/attempt/head/lane/shard, complete=true, integer pytest_exit=0, matching selected/logical-start/file populations, exactly one logical start, permitted complete phases/outcomes and finite nonnegative durations/completion offsets. Every phase total reconciles exactly with `file_durations_s`; files occur in exactly one shard per run. Job/step success and run/head/attempt identities are checked independently. Missing, expired, incomplete or mismatched artifacts would be UNKNOWN and replaced by the next eligible run, never extrapolated. Archive digests match GitHub metadata where provided; downloaded ZIP and observation SHA-256 are retained.

For each run/file, sum setup, call (body) and teardown report durations across its cases. These are **cumulative phase elapsed seconds across parallel cases**, not CPU time, shard wall or recoverable savings. Rank top 25 by median per-run total. Compute ordinary median and nearest-rank p95 `sorted(values)[ceil(.95*n)-1]`; with ten samples p95 is the observed maximum. Independent quantiles do not add. Missing files are never zero-filled. All top 25 files have ten samples; the complete file union and source-blob variant counts remain in JSON. This is a rolling mixed-source cohort, not a controlled before/after experiment. Matching file blobs alone cannot establish fixture/dependency/pipeline compatibility.

The seven recorded workflow/observer/partition/project/lock/root-conftest inputs each have one blob across the cohort. Every observation reports three effective workers and no active tracer. All top 25 test files have one source-blob variant; three other integration files have two. This does not seal the entire application/fixture dependency closure or turn this descriptive sample into a controlled experiment.

Classify setup-dominated when pooled setup / pooled total across the ten runs is at least 60%. The packet's residual body-dominated bucket is displayed as **body/mixed**: below 60% setup does not prove majority body. In particular, Concierge is mixed. No setup classification authorizes fixture reuse.

## Shard job and test-command costs

All seconds. Job wall is actual job start to completion. Test command is the named `Integration tests (shard N)` API step; observer time is a separate controller/wrapper clock. This report does not substitute any of them for required-context end-to-end latency.

| Shard | Job p50 / p95 | Test command p50 / p95 | Observer p50 / p95 |
| --- | ---: | ---: | ---: |
| 1 | 373.50 / 408.00 | 349.00 / 387.00 | 347.92 / 384.99 |
| 2 | 361.00 / 388.00 | 334.50 / 366.00 | 333.32 / 365.08 |
| 3 | 366.50 / 393.00 | 338.50 / 364.00 | 337.45 / 362.50 |
| 4 | 367.00 / 405.00 | 345.00 / 376.00 | 343.41 / 375.31 |
| 5 | 394.50 / 409.00 | 367.50 / 386.00 | 366.41 / 384.64 |
| 6 | 408.00 / 436.00 | 381.00 / 414.00 | 379.85 / 412.96 |

## Available clocks and the missing critical path

Shard 6 is slowest by both median and p95 job wall (408/436s) and command (381/414s). Its maximum observed wall occurs in the run identified below; the JSON also retains the slowest actual shard of every run. **A per-worker critical path is UNKNOWN**: receipts retain `effective_workers` and per-phase `completed_s`/`duration_s`, but no phase/node-to-worker assignment. Worker counts, loadfile order, file assignment, division by three or cumulative phase sums cannot reconstruct worker clocks.

| Shard | Scheduling wait p50 / p95 | Before test step p50 / p95 | Dependency install p50 / p95 | After test step p50 / p95 |
| --- | ---: | ---: | ---: | ---: |
| 1 | 2.00 / 97.00 | 18.00 / 24.00 | 2.00 / 3.00 | 4.00 / 6.00 |
| 2 | 2.00 / 186.00 | 20.50 / 24.00 | 2.00 / 3.00 | 4.00 / 6.00 |
| 3 | 2.00 / 178.00 | 22.00 / 24.00 | 2.00 / 3.00 | 5.00 / 6.00 |
| 4 | 2.00 / 3.00 | 21.00 / 27.00 | 2.00 / 3.00 | 5.00 / 6.00 |
| 5 | 2.00 / 167.00 | 20.00 / 24.00 | 2.00 / 3.00 | 4.00 / 6.00 |
| 6 | 2.00 / 3.00 | 18.50 / 24.00 | 2.00 / 3.00 | 4.00 / 6.00 |

At the maximum, run [38812590287](https://github.com/tzeusy-org/butlers/actions/runs/38812590287), shard 6: scheduling wait 1s; job wall 436s = pre-test window 19s + test command 414s + post-test 3s. Scheduling wait is outside job wall. The pre-test window includes checkout/cache/dependency/inventory/timing-cache work, not only environment installation. Step intervals remain in JSON; gaps/overlaps are not renamed as useful work.

Shard 6 controller clocks p50/p95: first setup completion **31.59 / 50.80s**; first result **31.60 / 51.60s**; first 1% completions **31.62 / 68.02s**; last 5% tail **135.68 / 148.55s**. These include wrapper work from `test_step_started_at`. `setup_complete_s` is the first item setup completion, not job environment readiness. The last 5% marker is observer finish minus the 95%-completion teardown offset, not worker idle time or only teardown cost.

| Requested component | Disposition |
| --- | --- |
| Scheduling wait | Measured from each job's own created_at to started_at; not inferred from guards/inventory topology. Pre-workflow enqueue/requeue and dependency-release-to-job-creation delay are UNKNOWN here. |
| Environment preparation | Named dependency-install step and full job-start-to-test-step window measured separately. Internal cache-hit/repair subphases are UNKNOWN without their own clocks. |
| Collection | UNKNOWN: no dedicated collection-start/end carrier. First setup/result includes collection and cannot isolate it. |
| Per-worker test time / critical path | UNKNOWN: no node-to-worker assignment. Effective workers do not supply this mapping. |
| Tail | Measured last 5% controller marker above; scheduling/worker-idle/finalization decomposition remains UNKNOWN. The maximum-wall run's direct global result span is given below. |

For that maximum-wall run, the original matching artifact directly yields a global first-result-to-last-teardown span of **379.75s**, from **31.39s** to **411.13s** on the controller clock. Another **1.82s** elapses to observer finish. This is an observed global span, not a worker critical path.

These overlapping controller markers are not additive components of job wall. No change to worker count, shard assignment or test population is justified by this decomposition alone. Ten samples also cannot satisfy REQ-ci-latency-001's minimum 20 complete required-context observations.

## Top 25 file phase costs

Each cell is p50 / nearest-rank p95 seconds across ten per-run file sums. Setup share is pooled; S=setup-dominated, B=body/mixed. Every row has n=10.

| File | Setup | Body | Teardown | Total | Setup share / class |
| --- | ---: | ---: | ---: | ---: | --- |
| `roster/relationship/tests/test_entity_dedup_curation_job.py` | 183.39 / 194.40 | 0.50 / 0.52 | 0.15 / 0.17 | 184.06 / 195.07 | 99.6% / S |
| `tests/api/test_optional_schema_lifecycle_db.py` | 173.49 / 209.03 | 2.63 / 3.26 | 0.02 / 0.02 | 176.43 / 211.91 | 98.5% / S |
| `roster/concierge/tests/test_dashboard_read.py` | 81.12 / 88.12 | 72.97 / 82.17 | 0.05 / 0.07 | 154.78 / 170.35 | 52.8% / B |
| `tests/migrations/test_runtime_attention_outbox_migration.py` | 11.74 / 21.96 | 132.67 / 143.23 | 8.65 / 21.79 | 152.74 / 165.23 | 8.5% / B |
| `tests/integration/test_runtime_attention_delivery_worker.py` | 0.04 / 9.79 | 127.59 / 137.64 | 7.88 / 11.90 | 136.95 / 147.47 | 1.4% / B |
| `tests/config/test_migrations.py` | 0.02 / 10.21 | 131.79 / 135.95 | 0.02 / 0.02 | 131.83 / 145.77 | 1.6% / B |
| `tests/migrations/test_core_255_insight_premise_binding_migration.py` | 0.00 / 12.42 | 116.42 / 156.23 | 8.44 / 20.82 | 125.36 / 166.48 | 1.8% / B |
| `tests/config/test_schema_matrix_migrations.py` | 0.00 / 11.45 | 122.98 / 130.18 | 0.00 / 0.00 | 123.65 / 130.18 | 1.7% / B |
| `tests/config/test_switchboard_connector_operational_role_migration.py` | 7.18 / 15.94 | 94.97 / 97.60 | 0.01 / 0.02 | 103.23 / 108.04 | 8.6% / B |
| `tests/integration/test_approval_delivery_authority_transport.py` | 27.61 / 38.47 | 63.56 / 69.27 | 0.50 / 0.56 | 90.89 / 105.83 | 29.2% / B |
| `tests/integration/test_integration.py` | 76.42 / 83.86 | 6.03 / 6.69 | 0.06 / 14.22 | 83.53 / 96.19 | 90.9% / S |
| `tests/migrations/test_core_252_definer_search_path_migration.py` | 0.00 / 9.95 | 77.71 / 83.68 | 0.00 / 5.96 | 80.52 / 92.05 | 2.4% / B |
| `tests/integration/test_dispatch_outcome_recorder.py` | 0.02 / 8.42 | 75.23 / 85.98 | 0.02 / 0.04 | 76.16 / 86.04 | 1.2% / B |
| `roster/finance/tests/test_claim_reconciliation.py` | 69.56 / 77.00 | 1.50 / 1.63 | 0.01 / 0.01 | 71.05 / 78.48 | 97.9% / S |
| `tests/config/test_runtime_config_policy_migration_integration.py` | 0.01 / 7.55 | 70.80 / 74.44 | 0.01 / 0.01 | 70.82 / 74.47 | 1.1% / B |
| `tests/integration/test_conversation_reply_db.py` | 0.02 / 12.24 | 68.31 / 84.86 | 0.01 / 0.01 | 68.96 / 89.63 | 3.2% / B |
| `tests/config/test_restore_drill_executor_acl_integration.py` | 4.18 / 11.82 | 57.32 / 60.07 | 2.09 / 4.83 | 64.79 / 71.72 | 8.7% / B |
| `tests/migrations/test_runtime_attention_producer_upgrade.py` | 4.45 / 17.09 | 50.55 / 59.87 | 2.92 / 12.67 | 64.04 / 75.52 | 11.2% / B |
| `roster/lifestyle/tests/test_taste_ledger.py` | 58.80 / 65.23 | 0.34 / 0.56 | 0.04 / 0.04 | 59.13 / 65.66 | 99.3% / S |
| `tests/config/test_schema_acl_isolation.py` | 0.01 / 2.56 | 56.60 / 62.46 | 0.01 / 0.01 | 56.62 / 62.49 | 0.5% / B |
| `tests/integration/test_decomposition_flow.py` | 52.36 / 65.09 | 0.56 / 1.00 | 0.05 / 0.06 | 53.17 / 66.15 | 98.8% / S |
| `roster/relationship/tests/test_jobs.py` | 17.08 / 17.33 | 32.03 / 41.25 | 0.12 / 0.13 | 49.28 / 55.03 | 33.7% / B |
| `tests/integration/test_relationship_fact_content_authority.py` | 21.37 / 24.17 | 25.79 / 28.58 | 0.17 / 0.23 | 48.10 / 51.07 | 44.5% / B |
| `tests/scripts/test_pg_restore_definer_ownership.py` | 21.03 / 31.03 | 19.43 / 32.68 | 8.12 / 13.14 | 46.76 / 76.86 | 41.6% / B |
| `roster/relationship/tests/test_tools.py` | 39.43 / 54.38 | 3.31 / 6.38 | 0.71 / 0.76 | 43.16 / 61.50 | 90.5% / S |

## Ownership routing and bounded candidate packets

The complete live-bead index (697 open/in_progress/blocked/deferred records) and all 64 open PRs were checked during this snapshot. File/path/title hits were distinguished from actual optimization scope. No issue was created or foreign note edited by this worker; ROOT owns those mutations. These packets allocate no source ownership and preserve every real-Postgres, migration, role, concurrency and isolation obligation.

All seven setup-dominated top 25 findings go to existing **bu-ly3lv5.8 / PR #4447**, including Relationship dedup, optional schema lifecycle, general integration, Finance claim reconciliation, Lifestyle taste ledger, decomposition flow and Relationship tools. Exact file/phase values are in the table/JSON. Attach this report and its JSON to that owner; do not start another template experiment. Reported historical clone savings remain unverified hypotheses.

Three body-dominated migration-chain candidates, ranked by body median (exposure, not savings):

### 1. `tests/migrations/test_runtime_attention_outbox_migration.py`

Body phase exposure: **132.67 / 143.23s** p50/p95. Expected wall seconds saved: **UNKNOWN**; neither file sums nor worker division predict a speedup. No dedicated live optimization owner found; ROOT rechecks before creating one.

Bounded fresh-reference phase clocks inside existing migration/body helpers; identify repeated migration/bootstrap work before proposing any change. Preserve every role/state/order case. No template substitution for the migration under test.

Retain: real PostgreSQL full core upgrade and finalized catalog/ACL predicates; ordinary producer append-only versus Switchboard delivery authority; protected-boundary downgrade/reapply and durable-evidence refusal; advisory-lock/concurrency, idempotency and no historical backfill.

Revert only the separately reviewed candidate; keep the original fresh-path executing owner and full case/invariant population. Condensation admission applies to any actual later loss.

### 2. `tests/config/test_migrations.py`

Body phase exposure: **131.79 / 135.95s** p50/p95. Expected wall seconds saved: **UNKNOWN**; neither file sums nor worker division predict a speedup. Existing bu-ly3lv5.8 / PR #4447 already names this file and fixture helpers; attach evidence there, do not open competing work.

Route the observed body cost to existing owner. Separate body-initiated fresh migration/bootstrap from asserted behavior in that owner's approved comparison; do not infer it is reusable fixture setup.

Retain: fresh complete migration chain and enabled schema outcomes; downgrade/reapply/version-stamp idempotency; real effective runtime-role grants, schema isolation and security-definer boundaries.

Revert only the separately reviewed candidate; keep the original fresh-path executing owner and full case/invariant population. Condensation admission applies to any actual later loss.

### 3. `tests/config/test_schema_matrix_migrations.py`

Body phase exposure: **122.98 / 130.18s** p50/p95. Expected wall seconds saved: **UNKNOWN**; neither file sums nor worker division predict a speedup. Existing bu-ly3lv5.12 explicitly owns schema-matrix catalog work; route to that owner, no parallel file rewrite.

Existing catalog owner should first measure core/module/butler migrations, discovery and catalog-query work separately. Preserve fresh database semantics and entire matrix; no timing-only stand-in.

Retain: real core/module/butler migration chains and dynamic roster discovery; complete expected-table matrix and diagnostics; retired messenger table absence and chronicler memory/domain isolation.

Revert only the separately reviewed candidate; keep the original fresh-path executing owner and full case/invariant population. Condensation admission applies to any actual later loss.

The runtime-attention delivery-worker and core255 premise-binding costs remain visible in the top 25 but are not extra candidate packets. The schema-matrix packet must not duplicate bu-ly3lv5.12's existing catalog outcome; config-migration work must not take PR #4447's fixture/helper scope. For the outbox candidate, ROOT must refresh dedupe and shape a bounded packet before any issue/source allocation. No quantitative saving is earned by these proposals.

## Verification and limits

The data reduction validated all60 receipts and reconciled file sums before computing statistics. A separate inventory lane independently selected the same ten run IDs. JSON syntax, privacy schema, counts, quantile recomputation, links and documentation guards are checked for this documentation change. Tests: +0 ~0 -0; no test bodies ran. The report records historical hosted successes, not a new source/test/CI optimization or a successful local full gate. ROOT receives exact setup and candidate attachments outside the repository for lifecycle routing.
