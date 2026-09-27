## Decisions

Feedback is append-only in `public.insight_feedback`; the latest family verdict
drives a family cooldown. `never` uses an unbounded cooldown, `not_now` requires a
future instant, and `useful` removes both exact and family cooldowns. Attribution
comes from the server and is never accepted from the request body.

Category weights rank candidates inside the existing effective global budget. A
uniform engagement history yields equal weights, preserving the previous ordering
and count. The last ten attributed deliveries provide the reversible signal; a
later explicit useful verdict restores the category baseline.

Expiry records `outcome=expired` with one closed `blocked_by` reason. The summary
counts these rows per originating butler. No evidence content is copied into the
ledger or feedback API.
