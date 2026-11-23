# QA remediation-loop liveness

QA cannot investigate itself and cannot read the tracker, so the health of its remediation loop is
judged by the independent control-plane controller from outcome rows QA already writes
(`public.healing_attempts`, `public.breaker_resets`). Spec:
`openspec/changes/qa-remediation-loop-liveness/`.

## Conditions

| Source | Condition | Opens when | Thresholds |
| --- | --- | --- | --- |
| `qa_loop_capability` | capability below target | latest launched investigation closed on a structural cause | cause: `publisher_unconfigured`, `proposal_retention_unavailable` |
| `qa_loop_breaker` | breaker open too long | breaker tripped for longer than T | T = 6 hours |
| `qa_loop_proposal` | proposal unadopted | retained proposal without a PR for longer than N | N = 7 days |
| `qa_loop_funnel` | no verified fix | zero merged over W with at least K publication-stage investigations | W = 14 days, K = 3 |

Each check is present, absent or unmeasurable. A dead database or zero opportunities is
unmeasurable: it never opens and never resolves. Only a complete measurable absence resolves.

Conditions show on `GET /api/qa/summary` (`loop_conditions`; `staffer_status` is `degraded`, or
`unknown` when they cannot be read). They are not yet paged: the runtime-attention producer's
allowlist is bootstrap-owned in `scripts/init-db.sql`, so adding them needs an owner-run upgrade
(follow-up).

## Resetting the breaker

Name the cause: `POST /api/qa/circuit-breaker/reset` with `{"cause_ref": "<cause_code>:<ref>"}`,
where `<ref>` is the PR, bead or deploy that changed the cause. Causes come from the streak's
`error_detail` (`publisher_unconfigured`, `proposal_retention_unavailable`, otherwise
`execution_failure`). While the dominant cause is structural the reset must name it, and is refused while the cause is still present: `publisher_unconfigured` is re-evaluated from the live wiring (`butlers.core.qa.publisher_wiring`), `proposal_retention_unavailable` from a retention-store probe, and an unreadable probe refuses. If five more
identical structural failures follow a reset, the same `<ref>` is refused (`structural_cause_unchanged`);
fix the cause and use a new reference.

Every refusal is recorded as a `public.breaker_resets` row with `outcome = 'refused'` and its
`refusal_reason`, so the decision is auditable. An identical refusal within 10 minutes is not recorded again. Refusal rows never clear the breaker: admission counts
only `outcome = 'reset'` rows.

## Prerequisite stall watchdog

Runs where `bd` access exists, never in the runtime:

```bash
bd export -o /tmp/issues.export.jsonl
uv run python scripts/qa_prerequisite_watchdog.py /tmp/issues.export.jsonl --days 7
```

Exit 0 all ok, 1 a prerequisite is blocked or unowned for more than D days (7), 2 evidence missing
(`unknown`; claim nothing). It prints the `bd note` a coordinator lane may file; it writes nothing.
Watched: `bu-ejow5p.11`, `bu-6a9xxw.*`, `bu-gjmx8w`, `bu-ratgnb`.
