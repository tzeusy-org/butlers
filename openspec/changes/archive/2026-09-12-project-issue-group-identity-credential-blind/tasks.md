# Tasks

## 1. Contract
- [x] 1.1 Add the `dashboard-audit-log` requirement **Credential-Target Audit
      Groups Are Identified Without Free Text**, stating the decision, its two
      load-bearing properties (content-blind, still distinguishable), and the
      accepted cost that identity is the credential rather than the cause.
- [x] 1.2 Place it as an ADDED requirement so it cannot clobber
      `durable-issue-condition-ledger`'s unarchived MODIFIED block on
      `dashboard-api` §`Issues Aggregation`.
- [x] 1.3 Record the rejected alternatives in the proposal, including the
      `PROBE_FAILURE_VOCABULARY`-token direction the bead proposed and why the
      token is not recoverable from the audit row.

## 2. Enforcement
- [x] 2.1 Export `CREDENTIAL_TARGET_PATTERN` from `api/models/audit.py` so the
      model's withholding predicate and the grouping predicate are one literal.
- [x] 2.2 Compute `error_summary` through a `CASE` on that predicate inside the
      shared `normalized_errors` CTE, so all four grouping consumers inherit
      the rule by construction.
- [x] 2.3 Build the credential title only from `action` and `target` — the two
      columns bu-ove06 keeps publishing for such a row.

## 3. Tests
- [x] 3.1 DB-free: the credential branch reads none of the withheld columns.
- [x] 3.2 DB-free: the CTE's predicate is the model's exported pattern.
- [x] 3.3 DB-free: feed, occurrences, and row-resolver builders emit the same
      CTE, so a group's title cannot disagree with its own drill-down.
- [x] 3.4 DB-free: two credential groups project to two distinct `issue_key`s.
- [x] 3.5 DB: absence sentinel on `GET /api/issues` for a synthetic provider
      failure string, for every credential-key spelling.
- [x] 3.6 DB: absence sentinel on `GET /api/issues/{key}/occurrences`.
- [x] 3.7 DB: two credentials stay two groups; two causes on one credential
      stay one group with a truthful count.
- [x] 3.8 DB: non-credential rows keep their `error_summary` verbatim.
