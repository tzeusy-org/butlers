# Goal ledger and admission

RFC0038's first control-plane slice is additive and dark: nothing in ingress,
scheduling, the daemon, a module, or an API calls it. `GoalAdmission` accepts a
durable `SourceRef` and loads evidence through an injected trusted source reader.
It is not a public request model, authentication adapter, runner, broker or Stop
implementation. Actual producer integration and runtime execution remain future
work. Project references and budget pools belong to separate milestones.

`goal_admission.py` owns deterministic ceilings/exposure and the transaction;
`goal_ledger.py` owns qualified SQL. The injected `CatalogProvider` returns a verified current immutable `CatalogView`
publication in the transaction or refuses. The view is a pinned protocol snapshot.
Its publisher must validate the
real catalog's complete authorization metadata and exclude private custody and
owner-decision capabilities. Fixtures prove that protocol's consumption, not
real catalog publication. Contracts and catalog cannot be loaded from prompt
text. A later adapter must use RFC0017 verified owner-channel authorization or
the authenticated dashboard principal; successfully resolved unknown senders
are third party, while an unavailable verifier returns a typed refusal.

## Durable records

All five tables live in Switchboard's schema:

| Table | Retained invariant |
| --- | --- |
| `goals` | Unique normalized source identity; immutable origin, admitted exposure, ceiling and policy digests; monotone current exposure; same-goal cause/effect FK; D11 state |
| `invocations` | Same-goal parent which is a lead; depth 0/1 shape; one launching/running lead; immutable grants/scopes and finite inert turn/token caps; timing placeholders and token hash only |
| `effects` | Same-goal invocation FK; capability/argument digests and D11 state; exact unique delivery, verified recipient and finite prompt expiry |
| `budgets` | One per goal; explicit finite maxima, reserved/used counters and child occupancy; sums bounded by maxima; absolute deadline and config snapshot |
| `admission_decisions` | Append-only attempts; at most one accepted association per source; fixed refusal reasons, immutable quota window/retry-after, delivery/effect and valid cause references |

No message body, arguments, results, bearer token, approval permission or
executable predicate is stored here. Telemetry consumers must use identifiers
and digests only. A refusal creates no goal or budget. Downgrade refuses any
persisted goal or decision history rather than discarding evidence.

## Authority and failure

Owner messages derive broad non-transact ceilings. Third-party goals begin
exposed and omit owner-authority records, actuation and administration; ordinary
communication is park-only except owner notify. An obligation requires a genuine
declaration, defaults to its owning domain's read/record plus owner notify, and
may narrow to exact declared grants. Missing creation exposure lineage is
exposed. Domain/feed inputs also need the publishing domain's typed untainted
owner-authority stamp to start unexposed. Runtime-created obligations intersect
the creator invocation's immutable grant. QA staffers always start exposed.

Staffer contract version 1 identifies staffer, owning domain, Git declaration
digest and pinned catalog digest, with a closed list of exact capability IDs,
exact resource ID sets and park-only restrictions. Missing/stale contracts,
unknown/duplicate/excluded capabilities and wildcard constraints refuse. All
ceilings exclude transact; admission never bypasses later approval policy.
Caused goals inherit current causing-goal exposure, intersect the declaration
with that goal's origin ceiling, and persist valid cause references even when
the configured chain limit (default two) refuses further admission.

The fresh `switchboard.runtime_config.goal_admission_config` snapshot requires
an explicit version, all four origins exactly once, finite origin/source COUNT
quotas and fixed UTC windows, plus each origin's complete finite goal/invocation
limits. NULL, malformed or unreadable configuration cannot fall back to a cache.
No numeric launch defaults are seeded. Each admitted decision records the exact
captured window charged; waiting across a boundary never changes that window.
Only new admitted goals consume slots; joins/refusals do not. Different origins
cannot consume owner capacity. Repeated attempts return their exact decision;
after a retryable refusal, a new server attempt reuses the same durable source.

Lock order is dedupe attempt/source keys, all referenced goals in sorted order,
then effect rows, then sorted aggregate origin/window and source/window quota
keys. Database constraints and unique indexes remain final race authority.
Future Stop, broker and budget mutators must share goal-before-effect ordering
and hold the relevant budget row while changing its counters. No such runtime
mutators are implemented here.

An owner reply joins only an exact server-held delivery/effect bound to the
verified owner entity/channel and a live goal whose prompt accepts input. Expiry
is checked after lock waits. Joining preserves origin, ceiling, cause and budget;
exposure can only increase, and no invocation or approval is produced. A terminal
or expired prompt creates an ordinary verified owner goal with delivery/effect
reference and no caused-goal link. Unknown/foreign/mismatched bindings follow
ordinary message admission without guessing continuity.

Database errors, failed decision persistence and cancellation roll back partial
writes. Commit uncertainty raises `AdmissionUnavailable`; retrying the same
durable source resolves any committed association without replenishing budget.

## Isolation and verification

`sw_047` uses enabled **non-FORCE RLS** with exact `current_user` Switchboard
runtime policies, following `sw_035`. The existing trusted migration/table owner
retains ordinary administration and `row_security=off` backup authority. No new
owner, definer, public SELECT, BYPASS, runtime fallback or backup exclusion is
introduced. The service checks its actual role and rejects superuser/BYPASS or
membership in a ledger table owner before doing admission. All other butler,
connector and Chronicler runtimes must be unable to assume a ledger owner.

`tests/switchboard/test_goal_admission.py` covers deterministic boundary cases.
The PostgreSQL and migration owners exercise real migrated core+Switchboard
constraints, source/attempt dedupe, last-slot and fixed-window races, reply/Stop
serialization, rollback/cancellation, bootstrap role denial and the existing full
backup/restore scripts with exact row/owner/policy/constraint parity. Collection,
static checks or synthetic pinned catalog fixtures do not prove those database
outcomes. Activation still requires real PostgreSQL execution and independent
review, then the later catalog/producer/runtime milestones.
