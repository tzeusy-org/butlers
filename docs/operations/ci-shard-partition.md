# Computed CI shard partition

The assigned files come from one actual installed pytest inventory of `tests/` and
`roster/`. Unit uses the unchanged unit predicate and tests/e2e path exclusion;
integration keeps integration-marked e2e items and the existing nightly/bench/perf
exclusions. The inventory binds actual checkout SHA/tree, workflow run/attempt/event,
Python and installed pytest/plugin identity, exact configuration bytes and selectors.
PR synthetic checkout SHA and PR author head are different identities; neither may
be relabelled as the other. Both unchanged budgets use this same inventory.

`.github/ci-test-weights.json` is one advisory input. Missing, stale, malformed,
nonpositive or incompatible costs use the finite lane median or one; weights never
establish membership. Whole files remain together within each lane. Largest cost
first, lexical tie order, least-loaded bin and bin-index tie order make assignment
deterministic. Every eligible file appears exactly once in five unit/six integration
bins. New tests need no registration change. The read-only six-hour observer emits
uncommitted candidate weights or UNKNOWN from a bounded successful-run/artifact
horizon; source updates require a separately reviewed PR.

Each child independently recollects the exact assigned selector before invoking the
preserved runner/xdist loadfile/three default unit workers/auto integration workers.
Preflight checks all eleven receipts' actual collected item hashes, logical start
multiplicity, complete phases, source/run/attempt/config/nonce/assignment and real
command/files. Empty, stale, missing, duplicated, changed-parameter or partial
populations refuse. Setup skip and legitimate xfail are outcomes, not omissions.
Raw node IDs and failure payloads are never uploaded; hashed names are minimization,
not anonymity or authentication. Complete flags alone never establish provenance.

The DAG is route→guards→5+6 backend and2Vitest children/browser. Preflight and
coverage start after both backend matrices; peak fan-out is14. The final expanded
workflow contains21 rows including conditional affected/coverage/skips, rather than
21 concurrently active jobs. Required check is verdict-only and always evaluates;
unknown/missing/skipped-without-policy prerequisites fail. Full merge-group remains
the terminal broad gate. Scoped PRs retain the existing conservative affected lane,
docs/main push retain explicit skip policy, and failed route never narrows execution.

The original dedicated smoke selector is retained exactly. Full reuse requires its
complete actual identities to have passed in the children, otherwise it runs again.
Derived evidence distinguishes actual shard commands and elapsed timers from the
unexecuted dedicated command. Eleven individually validated coverage databases
replace the historical ten; combine/upload/badge are independent reporting.
Frontend retains static gate order, Node300-second survivors, two actual locked
Vitest shard populations and source-bound dist reuse. Browser cache is advisory;
version pin, OS dependencies, finite retry and ordinary process cleanup survive.

Rollback is a reviewed PR restoring the preserved prior public runner/workflow and
hand-file source bodies together, then exact-head normal/protected verification.
Never restore a manifest as a current proof without recollecting the actual corpus.
Caches are advisory and may be unavailable; default lexical assignment remains
complete. Failed observers emit UNKNOWN, never silently reuse a prior success.

This change claims no wall-clock gain. Ten actual merge-group route≤15s,
guards≤180s and shard-spread<15% samples, the independent five-minute routine-lane
targets and all original actual scratch-PR negative controls remain mandatory.
Source/miniature tests and historical timings do not satisfy those outcomes.
