# Monthly spend-ceiling measurement

This measurement accompanies `bu-m0vjeb.13`. Results and the cache decision are
pending the real PostgreSQL execution of
`tests/core/test_model_routing_quota.py::test_monthly_ceiling_measured_synthetic_cost`.
This draft does not establish per-call cost or justify a cache yet.

The owner measures the actual `await check_monthly_ceiling(pool)` path, including
pool access, the configured-ceiling query, month-to-date ledger aggregation and
Python pricing. It uses the existing migrated, disposable core database and
ordinary asyncpg pool, with no production data or credentials. Each timed call
also checks the expected ceiling, priced subtotal and allow/refuse result;
an error swallowed by the existing fail-open policy cannot count as a sample.

The synthetic profiles contain 0, 1,000, 10,000 and 100,000 current-month ledger
rows, with the same number of previous-month rows and three priced model IDs.
Every row has explicit input, output, cache-read and cache-creation token counts;
historical rows have twice those counts to expose accidental historical inclusion.
The receipt retains every full-call sample, first-call and warmup distinctions,
serial and three-caller concurrent samples, and separate pool-acquire, SQL and
pricing timings. Component timings are separate observations, not an additive
decomposition of the complete call.

Database migration, fixture provisioning and initial pool creation are outside
the timed owner and are not measured as per-call latency. Seeding and `ANALYZE`
time are recorded separately. The first call uses an existing pool after seeding;
it does not claim a cold operating-system or PostgreSQL cache. Initial concurrent
calls may expand the pool from one to three connections. No cache is flushed and
no execution-time assertion imposes an arbitrary performance threshold.

The bounded, synthetic-only JSON receipt travels in one explicitly named
informational pytest warning, because successful xdist worker stdout is not
forwarded and the existing uploaded JUnit is sanitized. Existing CI logging and
warning policies stay unchanged. The final report must bind the receipt to the
actual executing source, checkout, tree, run, attempt, job and non-skipped owner.
These synthetic measurements cannot establish production cardinality, production
latency or fleet p95.
