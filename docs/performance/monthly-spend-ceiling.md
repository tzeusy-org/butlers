# Monthly spend-ceiling measurement

`bu-m0vjeb.13` measured the actual `await check_monthly_ceiling(pool)` path against
real PostgreSQL, including pool access, ceiling lookup, ledger aggregation and
Python pricing. **Decision: retain the uncached kernel gate.**

The 1,000- and 10,000-current-row synthetic profiles had serial medians of
3.390 ms and 6.626 ms. At 100,000 current rows the median rose to 48.660 ms, or
128.333 ms per caller in the three-caller sample. The larger profile exposes a
scaling concern: its isolated ledger-query median was 43.122 ms. These results do
not establish actual traffic cadence or how often another call arrives inside a
short cache TTL. Without evidence of that repeated-call benefit, adding cached
spend-authority state and delayed refusal is not justified. This is an engineering
decision from disclosed evidence, without an arbitrary latency pass threshold.
A demonstrated high-cardinality burst workload would warrant reconsideration;
this report does not claim that the larger-profile cost is negligible.

No cache or production implementation changes are included. Current DB/pricing
fail-open warnings, no-limit behavior, current-month pricing and immediate fresh
ceiling decisions remain unchanged. The conditional cache acceptance criterion is
not activated by this measured no-cache outcome.

## Executing source and scope

- Source: `b662aabd1260f64b92b1ae12e696d6af196b26cb`.
- Actual PR checkout: `de9fad877b1b944433423fdad7d725189ecd8675`;
  tree `81fdc2f9d29a56572f807d327383a039179a6cfb`.
- [CI run 40061829308, attempt 1, integration group 2](https://github.com/tzeusy-org/butlers/actions/runs/40061829308/job/120776850770),
  job `120776850770`, **SUCCESS**.
- Owner: `tests/core/test_model_routing_quota.py::test_monthly_ceiling_measured_synthetic_cost`,
  executed without a skip. All ten complete-file cases passed without skips
  across that integration group and unit group 4.
- Relevant source/test/fixture/pricing blobs and the complete source, roster,
  migration, fixture and dependency trees/files were independently compared
  between the author source and actual checkout and were identical. Later report
  edits do not change the executing measurement owner or product implementation.
- Full raw receipt SHA-256:
  `9534e2c0347d9d788988fc0b36d219c7b985620f8e4cd5aa1899fadb454da334` (pretty-printed retained JSON).

The owner asserts the configured ceiling and expected priced subtotal after every
sample, plus healthy allow/refuse results and empty omission counters. A swallowed
SQL or pricing failure would return no configured ceiling and fail these controls.
Historical rows have twice the token counts, exposing accidental historical
inclusion. The configured empty ledger returned zero spend with the ceiling intact.
Ten no-ceiling control samples had a median of 0.405 ms
(range 0.309-0.725 ms).
The positive 100,000-row subtotal was $626.245825; lowering its ceiling to $1
produced a real refusal in 51.335 ms.

## Environment and method

This is synthetic disposable-fixture evidence, not production or fleet latency.
The runner reported Linux, four logical CPUs, Python 3.12.15, asyncpg
0.31.0 and PostgreSQL 17.11 (Debian, x86_64), with UTC timezone,
128 MB shared buffers and JIT on. Migration chain: `core_275`. The existing
fixture uses a random owned database and a normal login with `superuser=false`,
`bypassrls=false`, `createrole=false`. Transport was TCP without TLS. The asyncpg
pool had minimum 1 and maximum 3 connections; first concurrent calls may expand it.
The catalogs reported RLS disabled on these three existing tables; this fixture
makes no claim about a production runtime identity or deployment topology.

`public.token_usage_ledger` is monthly range partitioned. The fixture contained
partitions August 2026 through March 2027. Existing ledger indexes were the
`(catalog_entry_id, recorded_at)` btree, the `(id, recorded_at)` primary key and
the partial unique `(attempt_id, recorded_at)` index for non-NULL attempts.
`model_catalog` retained its ID primary key, unique alias and
`(complexity_tier, enabled, priority)` index; `spend_ceiling` retained its ID
primary key. No index, schema, role or query was changed.

Each profile had 0, 1,000, 10,000 or 100,000 current-month rows plus exactly as many
previous-month rows. Three flat-priced model IDs were distributed round-robin:
`claude-haiku-4-5-20251001`, `claude-sonnet-4-6`, `claude-opus-4-6`. Each current row
had 1,000 input, 200 output, 250 cached-input and 50 cache-creation tokens; historical
rows had twice those counts. The existing pricing loader and estimator were used,
with pricing-file SHA-256 `f6be1f04985fde137232e1d0d36064f5364fa26e87e30e3e8409ced2fbe53d84`.

Database creation, migrations and initial pool startup occur in the existing
fixture, outside this owner; they are not included or claimed measured per-call.
Owner metadata/catalog setup and no-ceiling controls took 24.420 ms,
separate from the profile timers. Seeding and `ANALYZE` are recorded separately
in the raw receipt. The first call is after seeding with an existing pool, not a
cold OS/database-cache claim. No cache flush occurs. Every profile then retains
three warmups, twenty serial samples and twelve samples in four waves of three
concurrent callers. `perf_counter_ns` measures elapsed milliseconds around the
complete await, with status assertions outside its timed interval. Pool wait,
network round trips and synchronous Python pricing are included. Other shared
runner work can affect the observations.

## Observed distributions

Values are milliseconds. The columns show **minimum / median / empirical p95 /
maximum**; p95 is the nearest-rank statistic of these small samples, not a
production percentile estimate or confidence interval. Serial n=20; concurrent
n=12. The configured ceiling was $100,000 during these profiles.

| Current rows (equal historical rows) | First call | Serial min / median / p95 / max | Three-caller min / median / p95 / max |
| --- | ---: | --- | --- |
| 0 | 5.198 | 2.230 / 2.937 / 3.700 / 3.726 | 2.888 / 7.277 / 48.075 / 48.075 |
| 1,000 | 4.446 | 2.865 / 3.390 / 4.469 / 4.866 | 3.760 / 7.284 / 13.410 / 13.410 |
| 10,000 | 10.028 | 5.969 / 6.626 / 9.005 / 9.500 | 8.986 / 13.200 / 16.606 / 16.606 |
| 100,000 | 53.816 | 42.995 / 48.660 / 59.466 / 72.540 | 117.786 / 128.333 / 180.856 / 180.856 |

Separate component observations (n=5 each) had these medians in milliseconds.
Query timings include their own pool acquisition/round trip. Pricing includes the
real pricing-file load; these independent timings are **not additive** components
of the earlier full-call samples.

| Current rows | Acquire only | Ceiling query | Ledger query | Python pricing |
| --- | ---: | ---: | ---: | ---: |
| 0 | 0.009 | 0.506 | 0.812 | 1.898 |
| 1,000 | 0.009 | 0.365 | 1.383 | 2.123 |
| 10,000 | 0.008 | 0.330 | 4.655 | 1.523 |
| 100,000 | 0.024 | 0.480 | 43.122 | 2.432 |

## Raw bounded samples

The complete metadata and raw values remain in the explicitly named
`MONTHLY_CEILING_SYNTHETIC_MEASUREMENT` informational warning in the linked job log.
Successful xdist worker stdout is not forwarded, and existing uploaded JUnit
removes captured content/properties. A disposable successful control with the
actual xdist/split/filter options retained all 500 synthetic control values in
the warning summary. No warning filter, CI policy, artifact route or secret
handling was changed. Only synthetic JSON was extracted; unrelated raw logs were
not retained. The full-call/component vectors below permit recomputing the
reported distributions without using testcase wall duration as per-call evidence.

<details>
<summary>Raw millisecond vectors by current-row count</summary>

```jsonl
{"no_ceiling_ms":[0.661848,0.651653,0.429867,0.725309,0.459316,0.380257,0.326344,0.309329,0.365704,0.345904],"reached_ms":51.334907}
{"current_rows":0,"previous_month_rows":0,"expected_mtd_usd":0.0,"seed_analyze_ms":18.780715,"first_ms":5.198443,"warmup_ms":[2.617242,2.369101,2.423192],"serial_ms":[2.385936,2.544075,2.565287,2.23017,3.422872,2.963992,3.001749,2.921382,3.725511,3.103483,2.886545,2.808126,2.982429,2.898051,2.681969,3.575974,3.674614,3.700303,2.952099,2.275248],"concurrent_ms":[6.490676,48.075201,45.533374,4.543745,5.855654,2.888468,4.735116,11.981697,8.063901,13.438375,9.428749,5.46386],"concurrent_wave_ms":[48.218258,5.966682,12.142702,13.528933],"components_ms":{"acquire":[0.015584,0.010306,0.007011,0.008553,0.007612],"ceiling_query":[0.656775,0.505857,1.033326,0.390862,0.282348],"ledger_query":[2.353797,0.485386,0.81194,1.216497,0.273104],"pricing":[2.77787,1.475776,3.011359,1.897907,1.773167]}}
{"current_rows":1000,"previous_month_rows":1000,"expected_mtd_usd":6.258325,"seed_analyze_ms":41.240394,"first_ms":4.446229,"warmup_ms":[4.476768,4.058079,3.510346],"serial_ms":[4.866355,3.426793,3.353663,3.660447,2.864846,3.546184,2.956606,2.939915,2.938944,3.142211,3.119488,4.469007,3.033407,3.297708,2.980297,3.979214,3.673336,3.669992,3.569269,3.684223],"concurrent_ms":[11.106256,8.580499,13.409627,4.455812,6.669357,8.754046,3.760158,7.89873,5.710724,8.035227,3.952019,6.030469],"concurrent_wave_ms":[13.63579,8.975272,8.071001,8.108047],"components_ms":{"acquire":[0.009464,0.009425,0.009234,0.010156,0.009444],"ceiling_query":[0.344562,0.302869,1.306921,0.382019,0.365094],"ledger_query":[0.848616,1.545663,1.383367,1.87423,1.284176],"pricing":[1.79488,1.938909,2.330912,2.123228,2.374228]}}
{"current_rows":10000,"previous_month_rows":10000,"expected_mtd_usd":62.620825,"seed_analyze_ms":237.157836,"first_ms":10.027917,"warmup_ms":[8.535565,9.95082,10.194609],"serial_ms":[8.293599,7.269202,6.12315,6.216485,6.319517,5.969296,9.500478,7.931199,8.638291,9.004777,7.990779,7.178419,7.143375,6.368471,6.407771,6.76383,6.460035,6.039292,6.488653,6.37381],"concurrent_ms":[8.986259,13.029079,16.606046,11.957325,14.148616,15.817475,10.583264,15.407506,13.3719,9.403094,10.908506,15.419982],"concurrent_wave_ms":[16.833992,15.970808,15.540917,15.663161],"components_ms":{"acquire":[0.008253,0.008232,0.008493,0.007341,0.00636],"ceiling_query":[0.333345,0.311462,0.302509,0.3298,0.407979],"ledger_query":[4.654764,4.725822,4.597117,4.522694,5.985921],"pricing":[1.523108,1.501155,1.548897,1.465942,1.971758]}}
{"current_rows":100000,"previous_month_rows":100000,"expected_mtd_usd":626.2458250000001,"seed_analyze_ms":2652.049666,"first_ms":53.815707,"warmup_ms":[50.047618,53.43515,51.356521],"serial_ms":[48.12414,47.999379,50.307647,44.459545,48.577837,53.724299,44.916564,48.742681,43.093892,48.907711,49.171551,42.994541,45.665579,45.553444,45.043796,49.554447,57.966797,72.540046,59.465738,50.245587],"concurrent_ms":[119.443546,130.257476,127.616588,120.224443,123.994966,126.787847,132.209543,129.050376,117.785699,154.755923,180.855978,178.305103],"concurrent_wave_ms":[130.46497,126.971836,132.305808,181.086207],"components_ms":{"acquire":[0.022794,0.024577,0.022785,0.027391,0.024267],"ceiling_query":[0.458805,0.465705,0.479697,0.490483,0.521951],"ledger_query":[44.216207,41.809126,43.122195,40.856278,46.9798],"pricing":[2.453207,2.431725,2.234125,2.958722,2.358063]}}
```

</details>

Tests: +1 ~0 -0. The single added real-PG measurement owner preserves all nine
existing tests and all fixture/helper bodies, assertions and parameters unchanged.
