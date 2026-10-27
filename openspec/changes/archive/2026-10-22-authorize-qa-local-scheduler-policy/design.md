## Evidence and decision

Q1's approved target is `REQ-staffer-qa-006`: QA patrols continue through
derived remote staleness, while an explicit administrative stop remains
effective. Today `src/butlers/core/scheduler.py::_butler_dispatch_gated`
uses `resolve_routing_target(..., allow_stale=False,
allow_quarantined=False)`, so both legacy TTL staleness and owner holds stop
cron/deadline passes. `src/butlers/lifecycle.py` wires
`ButlerDaemon._create_audit_pool` as the eligibility pool. That pool uses the
Switchboard schema with no QA runtime role; it is for audit, not local
policy authority.

`roster/switchboard/migrations/035_registry_control_plane.py` records four
policy states and five provenances, grants table SELECT only to
`butler_switchboard_rw`, and leaves RLS enabled. A matching legacy TTL hold
becomes `active/legacy_ttl`; an operator hold becomes
`paused|quarantined/legacy_operator`; uncertain history becomes
`review_required/legacy_ambiguous`. The authenticated owner writer sets
`operator` provenance. Neither a stale observation nor a legacy
`eligibility_state` value is an owner stop.

| Candidate | Switchboard-process outage | Owner-stop safety | Disposition |
| --- | --- | --- | --- |
| MCP read on each tick, no cache | No policy answer; fail-closed would stop QA patrol | Safe only by stopping | Reject for the circular-outage target. |
| MCP plus local last-known cache | A cached active answer appears available | New owner pause can be bypassed; cached hold can survive release | Not an authority source. |
| QA/public mirrored policy table | Reader survives process outage | Duplicate writes and cache/rollback ordering can diverge from owner policy | Reject duplicated authority. |
| Audit-pool or broad table SELECT | Reader survives process outage | Migration login bypasses runtime RLS; other rows can leak | Forbidden. |
| Fixed QA role-bound database projection | Reader survives while DB is operational | Reads current committed Switchboard policy without a positive cache | Proposed; requires new Rule 3 owner adoption. |

## Candidate producer and consumer

The future Switchboard migration owns a single `public.qa_local_schedule_policy()`
read function. It takes **no arguments** and binds the literal name `qa`
to the effective `butler_qa_rw` role checked inside the function. The
function uses `SECURITY DEFINER`, a fixed search path, fully qualified
Switchboard table names, and a bounded single-row read. The only successful
columns are `policy_state` and `policy_provenance`; the function validates
both enums and their pair before returning. Candidate valid pairs from
`sw_035` are `active/(none|legacy_ttl|operator)`,
`paused|quarantined/(legacy_operator|operator)`, and
`review_required/(legacy_ambiguous|operator)`. Any other pair is malformed,
not silently converted to active. A source row absent from the exact fixed
name is missing, not an implicit active default.

The function has no EXECUTE privilege for PUBLIC, any other runtime role, or
a role-less migration-login call. Only `butler_qa_rw` receives EXECUTE. No
QA SELECT grant on `switchboard.butler_registry_control_plane`, broad
Switchboard schema grant, writable public copy, or caller-supplied name is
admitted. The migration and bootstrap replay must preserve this boundary
despite `init-db.sql`'s broad public-table development grants; RLS on the
source table remains enabled. A database owner remains trusted under the
existing deployment threat model, but the audit pool must not use that owner
privilege as the runtime read seam. This proposal does not allocate a
migration revision until the current chain head is rechecked.

Q1's deterministic scheduler consumer uses QA's existing own-schema
runtime-role pool, never the separate audit pool, and verifies role
enforcement before treating a result as known. It obtains a fresh projection
at each local admission boundary; no positive policy result is cached across
ticks. The Switchboard process and network may be unavailable while the DB
remains live. Switchboard's own eligibility sweep reads its policy under its
existing Switchboard role and is tested separately; it does not call QA's
projection or gain a new grant.

## Admission and failure matrix

| Read result | QA local dispatch | Meaning |
| --- | --- | --- |
| `active` + `none`, `legacy_ttl`, or `operator` | Admit due work | The remote observation may be stale; no explicit current hold is represented. |
| `paused` or `quarantined` + valid provenance | Skip new due work | Explicit owner or proven historic operator hold. Preserve next-run continuity; no catch-up stampede. |
| `review_required/legacy_ambiguous` | Skip new due work | Unresolved history, not owner intent. |
| `review_required/operator` | Skip new due work | Explicit owner review hold. |
| Missing row, invalid enum/pair, denied role, unreadable function/DB | Skip new due work with typed unavailable reason | No legacy `eligibility_state` or cached active fallback. Keep the loop and independent overdue observation diagnosable. |

This refines, rather than silently overrides, `REQ-staffer-qa-006`:
derived **remote observation** staleness never gates an otherwise known
active policy; ambiguous legacy `review_required` and an absent or
untrustworthy **policy authority** are separate safety holds, not remote
staleness. The owner must explicitly adopt these exceptions alongside the
Rule 3 projection. Unknown states are not labeled as owner-directed stops;
an `operator` review hold retains its owner provenance.

Each successful read is one committed snapshot. An owner policy change that
commits before a subsequent admission must be seen there; an already
launched job is not retroactively cancelled. Q1 must not retain an `active`
answer across admission boundaries. If a tick dispatches multiple due jobs
after one long-running step, it rechecks before a later new dispatch so a
mid-tick stop is not ignored. Failure to perform that recheck is a test
failure, not an invitation to use the old routing eligibility flag.

## Future implementation and verification packet

This package makes no runtime or migration change. After exact owner adoption
and independent review, `bu-ch4p1v` owns the next available Switchboard
migration, fixed projection, ACL/RLS/bootstrap replay and rollback tests.
Q1 `bu-fvw4ap.1` then owns only QA scheduler admission/wiring and QA
manifesto/RFC 0001 alignment. Neither implementation may start merely
because this proposed change exists.

Extend `tests/integration/test_infra_state_views_migration.py` and
`tests/config/test_init_db_bootstrap.py` against migrated PostgreSQL:
effective QA role receives only its own fixed row; another runtime role,
PUBLIC, a role-less audit/migration login, and direct QA table SELECT are
denied; `init-db.sql` replay does not widen access; all four states,
`active/legacy_ttl`, invalid/missing pairs, concurrent owner policy change,
role reset after success/error/cancellation, and non-destructive downgrade
keep the intended result. Preserve existing migration outcome and RLS tests
when condensing; the integration test budget has no spare slot.

Extend `tests/core/test_core_scheduler.py`,
`tests/core/test_scheduler_continuity.py`, and only the changed owning
cases in `tests/integration/test_qa_pipeline.py`: remote stale with
`active/legacy_ttl` still runs one due patrol; each explicit hold prevents
new dispatch without duplicate catch-up; `review_required` and typed
unavailable stay distinguishable; a mid-tick owner stop blocks later
admissions; restart does not double-dispatch. Test Switchboard's own
eligibility sweep separately under its existing role. Report the measured
`Tests: +a ~b -c` in each future implementation PR, run the dirty-worktree
test planner, role/rollback real-Postgres nodes, Ruff, strict OpenSpec,
overwrite/guards/budget, and terminal hosted CI.

## Rollback and adoption boundary

If the producer cannot be installed or verified, Q1 remains blocked; do not
turn on an `allow_stale` shortcut. On rollback, retain the protected
`sw_035` policy/provenance representation and the read interface while an
active consumer needs it. Disable the new consumer only with a reviewed
fallback that preserves explicit owner stops and does not recreate a
QA self-observation gate. No migration downgrade may erase an owner hold,
boot history, or a policy receipt. Owner adoption of this exact Rule 3
exception is a separate decision from the implementation, merge, and live
activation gates.
