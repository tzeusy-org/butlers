# Obligation declarations

RFC 0038 D2/D5 assigns source custody to the domain daemon and admission to
Switchboard. A declaration describes a ceiling. It does not establish an owner,
approve an effect, authenticate a creator, or enable a goal runner.

## Version 1 value format

The strict parser in `butlers.core.obligations` accepts this value:

```toml
version = 1
capabilities = [
  { capability = "health__measurement_list", resources = { entity_id = ["exact-entity-id"] } },
]
```

The example illustrates syntax; it is not a claim that this capability/resource
pair is registered. Actual IDs are `<owner_domain>__<canonical_name>` from the
final approval-wrapped registry, including dotted canonical names such as
`health__route.execute`. There is no dotted-domain alias. Validation rejects
unknown, ungrantable, private-custody,
owner-decision and `transact` entries. A resource key must be declared by that
actual tool. Values are unique, nonempty exact sets; wildcards are refused.
Omitting a resource restriction means unrestricted for that argument, not empty.
An empty capability list is an explicit empty ceiling, not permission to infer a
default. `park_only = true` can only narrow a grant.

Only `version` and `capabilities` are accepted at the top level. Each capability
accepts `capability`, optional `resources`, and optional `park_only`. All values
are copied into immutable tuples and canonicalized before digesting. Unknown
keys, duplicate capabilities/resources, non-boolean flags and unsupported versions
are refused. The name `preconditions` is reserved for **bu-jv1moa.1**; version 1
neither accepts nor evaluates it, even if it is empty. No allowance or spend
behavior is implemented here.

## Git schedule carrier

[decision] A prompt schedule declares its ceiling in a nested
`[butler.schedule.obligation]` table. `ScheduleSpec` invokes the single
`parse_declaration()` parser during its normal typed validation. The field holds
an immutable `ParsedDeclaration`, never the original mutable table. Missing or
malformed declarations retain only a closed status for dark startup diagnostics;
ordinary unknown schedule keys, bad cron, dispatch-mode and deadline validation
remain startup errors. Invalid declaration contents are neither retained nor logged.
The conversion imports the parser only when the field is validated, avoiding the
config/admission/runtime-config import cycle without a second TOML parser.

The actual lifecycle passes those same typed schedules into `sync_schedules()`.
Its `project_loaded_schedule()` consumer preserves existing SQL argument types,
timezone/quiet/stagger behavior and its `None` result. The same parsed carrier is
used by post-registration diagnostics. The projection's definition digest includes
the declaration, but is not a row UUID, context witness, creator grant or source
registration. OFF makes no new B2, admission or reservation calls.

### Education schedule inputs

Education's three Git prompt schedules now declare their actual workflow
capabilities. The weekly digest includes the analytics snapshot/mastery fallback;
the daily nudge reads all active maps and pending reviews before its conditional
owner notification. The stale-flow declaration includes the referenced skill's
orphan-map path, both `administer` cleanup capabilities and its memory write.
That memory grant restricts the registered `predicate` resource to
`study_pattern`. Map IDs and memory subjects come from the current source
population, so they are not replaced by guessed static IDs. Omitted resource
restrictions mean all IDs for those exact capabilities.

The `notify` capability does not establish an owner recipient or waive exposure
and approval rules. Its registered resource parameters are `recipient`,
`entity_id` and `request_context`; `channel` is not a resource constraint, and no
owner UUID is invented in Git. Actual recipient resolution and broker/domain
checks remain required. Current prompt text, quiet behavior, schedules, module
groups and deterministic jobs are unchanged. These declarations flow through
the same typed loader, normalized sync and final-registry diagnostics described
above; they still cannot establish a verified fleet catalog or enable admission.
Education's generated teaching/review obligations remain separate unfinished
source families, as do the other roster and subscription declarations.

### Other declared Git prompt inputs

Six more schedules carry dark ceilings through the same typed loader, schedule
sync and final-registry diagnostics:

| Schedule | Selected workflow capabilities, within its owning domain |
| --- | --- |
| Chronicler day-close | `chronicler_day_close_bundle`, `notify` |
| General tomorrow prep | `state_get`, `calendar_list_events`, `notify` |
| Health weekly summary | `health_summary`, `trend_report`, `measurement_history`, `medication_list`, `medication_history`, `symptom_history`, `notify` |
| Lifestyle taste digest | `memory_search`, `notify` |
| Travel upcoming check | `upcoming_travel`, `notify` |
| Travel document expiry | `document_expiry_inventory`, `memory_fact_inventory`, `calendar_scan_reminder_events`, `calendar_create_event`, `notify` |

The selected Health graph includes the referenced skill's medication and symptom
reads. Lifestyle's taxonomy examples do not add memory writes to its search
workflow. Travel's document graph includes reading existing calendar reminders
before creation; a declaration does not itself implement repeat-safe identity.
Only notification and calendar creation are classified `communicate` in these
graphs; their actual recipient and approval checks remain mandatory.

These six tables omit resource restrictions rather than invent dates, state
keys, medication/trip IDs or owner recipients. This is not authorization for
arbitrary arguments: actual creator/context/goal intersection and recipient and
exposure policy remain required. `memory_search` registers `scope`, not its
predicate/time filters, as a resource dimension; `notify` does not register
`channel` or `owner`. An unknown owning-domain capability is diagnosed as
`unknown_capability`. An other-domain capability stays
`fleet_catalog_unavailable`, because a local registry cannot validate its remote
existence. Even healthy owning-domain declarations remain unverified fleet data.

The adjacent Chronicler gap-interview and both Relationship prompt blocks remain
undeclared pending their separately owned deterministic conversions. No prompt,
cron, job, module or current dispatch behavior changes here. The census still
contains all 102 schedules and 19 producer families, including unfinished ones.

Health's skill now uses the registered history/trend APIs and distinguishes
expected coverage from logged observations; General's unbound travel-time
instruction is removed. Travel's document workflow uses the complete authorized
document inventory, bounded canonical `passport_expiry` fact inventory and the
explicit reminder calendar scan. Their new readers preserve incomplete/error
states rather than claiming empty coverage. Actual both-order reminder-write
idempotency, exact payload/race/lost-reply proof remains separate unfinished work;
the existing writer is not repaired or authorized by the read declarations.
These source changes do not establish provider execution, authenticated source
or creator authority, a verified fleet catalog or admission readiness.

## Validation and catalog custody

`build_obligation_catalog()` projects immutable final `ToolCatalog` snapshots.
It checks classification and authorization completeness and uses the registered
domain plus canonical tool name. It is a local value projection, **not** the
fleet's authenticated, freshness-verified `CatalogProvider`. Conditional recipient
prose is not executable owner resolution: the projection conservatively marks no
capability as an unconditional owner default. The broker must still resolve the
actual recipient and enforce approvals, resource selection and exposure.

`validate_declarations()` logs fixed reason codes and indexes before the switch,
returning only valid declarations. After the switch the same invalid population
raises a startup validation error. Neither function toggles a switch or discovers
which due sources exist. That authoritative switched startup integration still
requires the real control reader and complete published catalog.

Current startup runs `diagnose_loaded_schedules()` and
`diagnose_loaded_subscriptions()` only after core/module
registration and approval wrapping finish and `_finalize_tool_catalog()` returns.
It can report missing/invalid declarations and disprove unknown or ungrantable
local capabilities/resources. A valid local declaration, an empty ceiling or a
remote capability still reports `fleet_catalog_unavailable`: the local snapshot
cannot prove a complete verified fleet. These fixed index/reason warnings do not
suppress current dispatch or enable a goal. This is actual dark diagnostic
wiring, not completed after-switch startup validation or source authority.

## Source identity and runtime creation

`ObligationSource` binds a domain, source family, durable UUID, material revision
and declaration. An occurrence key includes the revision and a source-owned
persisted nominal slot. A retry must reuse that slot; it must not invent a fresh
clock token. Subscription identities additionally bind the durable event UUID
and distinguish feed events from caused domain events. No message body or event
payload chooses origin, cause or feed registration.

The owning repository must derive and atomically store the revision from the
entire effective definition, including prompt/context and declaration. It must
re-read activation, revision, occurrence and event membership at admission.
Editing a prompt/declaration or changing job mode to prompt mode must follow the
same creation/authority checks as creating an obligation.

`RuntimeCreationSnapshot` copies the actual verified server-held invocation,
ceiling and exposure at creation. Its value check rejects requested widening,
including removal of a resource restriction or a `park_only` constraint; it does
not silently clip. The immutable exposure applies to every occurrence. This
object is not a verifier or request schema. The actual broker-owned creator
verification remains required before a production caller can construct
authoritative evidence. The owning transactional repository and schedule adapter
are implemented below; their software controls do not authenticate callers.

## Dark integration and remaining work

The implementation remains dark. No source adapter is registered with
`GoalAdmission`, current production paths write no new source or goal table,
and today's dispatch paths remain unchanged. In particular a passing value test
does not prove committed
source creation, duplicate admission, creator authentication, feed subscription
verification or causal lineage. Those require the actual source-owned repository,
verified broker/domain interfaces and genuine PostgreSQL controls.

`docs/runtime/obligation-inventory.json` records all current git schedules and
the known dynamic/model producer families. It explicitly retains unclassified
job wrappers and missing declarations. `job` is not a synonym for deterministic:
Chronicler's narration wrapper reaches a model. Deterministic presence, departure
and safety alerts must keep their original jobs and remain independent of goal
admission, model availability and budgets. The source inventory guard proves
census coverage, not a completed semantic sweep or live database inventory.

The remaining full bu-o9qax4 work includes the three separately owned Git
prompt conversions and complete dynamic/model declaration coverage; production
schedule/subscription source registration and committed occurrence consumption;
verified creator/cause interfaces; final-registry publication; actual
scheduler/deadline/chain/feed/model admission producers; and real source/admission
commit and replay proof. The Home read/record/owner-notify source default and
required lifecycle/roster declaration documentation are implemented; this does
not grant a feed any live admission or recipient authority. The existing D18 model-function,
lead-policy, memory-consolidation, project and allowance units retain their scopes.
There is no switch activation, migration application, deployment or live send.

## Prepared owning schedule transaction

`obligation_schedule_sources` is an unregistered owning-schema adapter, not a
replacement config parser or a trusted caller. It copies the complete normalized
scheduled-row material and declaration. Missing actual context material makes
the definition unavailable; a supplied Python value does not authenticate the
context, Git provenance, catalog or creator. The typed loader and normalized
schedule sync already carry the declaration. Production registration still
requires the actual context/source binding and a verified creator/catalog.

The writer uses the same owning connection for actual `scheduled_tasks` row
insertion/update, source revision, pending/claimed occurrence cancellation,
command receipt and reference outbox. It validates the owning role/schema and
rejects a temporary `scheduled_tasks` shadow. Lock order is command, source, then
existing scheduled row. A nested savepoint prevents a caught refusal from
committing only the row update; the returned value remains staged until the
outer transaction and pool release succeed.

A stable command replays its original receipt without reverting a newer row.
A true no-op preserves generation and emits no new outbox entry. Material,
declaration, context or activation changes advance the generation. Removed
declarations/context and job conversion disable an existing source and cancel
its outstanding occurrences; they cannot create fresh authority. Runtime
identities cannot be relabelled Git, and a deleted identity stays terminal.
Mutable run timestamps are excluded from material and preserved on updates.
A newly inserted dormant row has no nominal run slot (`next_run_at` is null);
normal scheduling/cutover registration is still missing. An unconfirmed commit
requires reconciliation using the original command, not a fresh callback retry.

Recording-connection tests exercise this software path, not PostgreSQL locks,
role enforcement or durability. The additional migrated PostgreSQL cases are
authored but unrun locally. Current OFF schedule/domain-wake hooks still do no
B2 source IO, admission, reservation or suppression. Verified catalog/control,
production config/sync source registration and context producers, final occurrence fences, runtime
creator/cause and other source families remain full-issue implementation work.

### Selected typed Git sync consumer

The real `sync_schedules()` entrypoint now accepts an internal
`prepared_git_sources` tuple. Before acquiring a connection or replaying any
historical command, it binds every selected `ScheduleSpec` projection and its
already-parsed declaration to exactly one `GitScheduleSyncCommand`. Duplicate
names, commands, selected source IDs, mixed owning domains, an incomplete set or
different row/declaration material refuse. Prepared values cannot add calendar
linkage or other row fields that the typed Git schedule did not provide.

This branch writes the selected scheduled rows, revisions, cancellation and
reference outbox through one `run_source_transaction()`. All command locks are
taken in UUID order before source locks in UUID order, then rows in name order.
The existing revision adapter preserves actual nominal slots and keeps new rows
dormant. Matching historical replay returns its original receipt without
reverting a newer row; contradictory submitted material refuses before replay.
Losing a declaration/context disables an existing source and cancels its pending
or claimed occurrences. A new unavailable source refuses the entire batch.

This is selected-source persistence, not full configuration reconciliation:
omission does not mean deletion, and the branch performs no implicit removal
sweep or legacy fallback. The production factory must still select and verify
Git/context/catalog material and explicitly reconcile removed sources before it
can register this path. No such factory, activation, dispatch replacement or
creator authentication is inferred from these internal values. The ordinary
call without `prepared_git_sources` retains its existing SQL, removal sweep and
`None` result, including deterministic service jobs. Recording-connection
controls establish software behavior only; genuine PostgreSQL transaction,
role, concurrency and commit-loss proof remain required.

## Engineering context decision

[decision] Keep QA's useful Diagnostic Context section, anonymize its contents
through the existing RFC 0015 anonymizer, JSON-quote it, and label it untrusted
incident evidence rather than instructions or owner authority. Drop all five
ambient situational/memory/timezone/routing/blind-spot reads for `qa` and `healing`
before prompt composition. Preserve their empty MCP configuration. Ordinary
schedule context reads and MCP behavior remain unchanged. This changes source
behavior without granting engineering executors owner authority or goal access.

## Session-waking subscriptions

[decision] Git-owned domain-event and device-feed ceilings use
`[[butler.subscription]]` in the same strict loader as schedules, with a unique
`name`, `kind` (`domain` or `feed`), `source`, and `[butler.subscription.obligation]`.
A domain source is an exact publisher event type; a feed source is an exact
registered channel name. Wildcards, duplicate names/source identities and unknown
keys are errors. Declarations use the existing version1 immutable parser and
reserved `preconditions` rule. A malformed/missing declaration retains only its
closed dark diagnostic status.

After final approval-wrapped registration, startup diagnoses these ceilings using
the actual local catalog. It can disprove a local tool/resource but does not
publish or verify a fleet catalog. A matching source string does not authenticate
a feed/event, register a live subscription or enable goal admission. The actual
registered ingress and committed subscription/source/cause adapter remain required;
absence of a declaration never licenses a feed goal at cutover. Existing OFF
routing and deterministic safety/presence/departure producers remain unchanged.

Home declares the HA event workflow as read/record/owner-notify, including the
actual entity-resolution dependency for fact writes. The fact-write ceiling is
restricted to scope `home` and the three observed-event predicates
`comfort_deviation`, `device_issue` and `usage_pattern`; it does not allow writing
owner preference/policy records. Its feed skill does not
invoke an HA service or scene. A separately authorized owner action retains the
existing physical-risk and approval rules; a feed/preference is not that action.
No caller-supplied creator UUID, grant or exposure is accepted by this format.


## Runtime schedule and deadline owning writer

The internal `schedule_create` persistence path now accepts a frozen
`RuntimeScheduleDefinition` plus a durable command UUID. The existing MCP tool
schema exposes neither field. Its ordinary OFF call still issues the same legacy
row insert and does no source IO. A future authenticated D7 adapter must select
both values from the actual invocation; neither a Python value, a fact token,
a caller-supplied context nor a creator UUID authenticates that invocation.

The source branch compares every normalized row field against the actual create
arguments before touching the pool. Cron and deadline creation then use one owning
transaction to insert the real scheduled row, its immutable creator/grant/exposure
snapshot, revision and reference-only outbox. Deadline dependency IDs participate
in the material digest and are inserted with the pending deadline and empty fired
thresholds. The real deadline validation/defaults and its ordinary callers remain
unchanged. A replay retains the command UUID and returns its historical row ID;
it does not reset the due slot or establish current source authority. Different
material, missing policy context, a wider ceiling, wrong owning role, or a failed
source write cannot become a successful creation. Software recording controls
prove these call paths and rollback branches, not PostgreSQL durability or role
security.

The corresponding internal `schedule_update` branch requires the expected
source revision and original creator snapshot. It locks command, source and
actual row before running the existing row mutation on that same connection.
Submitted values use the ordinary update normalizers before command reconciliation;
replay must match the selected historical definition without reading or mutating
a newer row. The actual first-call resulting row must match that definition. Row mutation,
source revision and cancellation of pending/claimed attempts commit together;
already-cancelled occurrences and immutable revision/command evidence remain
historical. The closed occurrence states are pending, claimed and cancelled;
these software controls do not establish a committed goal or dispatch outcome. Delete uses the same ordering,
checks the entire selected row and retains an immutable deleted source revision
after removing the row. A failed revision write rolls the row deletion back.
Exact command replay returns the historical result without mutating a newer row
or resurrecting a deleted one. Neither replay nor a missing row proves current
authority. These internal parameters remain absent from public MCP schemas.

This is an owning persistence adapter, not the missing authenticated creator
producer, current-context reader, cutover route, or control-plane publisher.
Public update/delete/toggle and deadline mutation, generated Education/chain/wake
producers and model-backed caller adapters still require their actual
source-revision and creator/cause integration. Legacy OFF mutators do not acquire
a second source lock or promise a global fence; a future source consumer must
revalidate actual current row material. Nothing in this path enables admissions
or replaces current dispatch. Real source+row PostgreSQL concurrency/rollback/
readback evidence and the full original M6 obligations remain required.

### Deadline transitions, toggles and generated review transactions

The internal deadline update adapter runs the ordinary deadline validator and
row mutator under the same command/source/row transaction. Target-date changes
retain the existing fired-threshold reset and pending-status reset. Explicit
status transitions bind their selected status in the revision material;
terminal transitions retain automatic disable. Dependency edits are checked
against the resulting row, and historical commands validate their submitted
values before returning without replaying against a newer row. Deadline delete
uses the existing row/source tombstone path. These are internal persistence
inputs, not an authenticated public creator interface.

The source-aware toggle uses the same row mutation and ordinary refusal rules
as today's interactive toggle. A new command returns its actual in-transaction
row observation after source commit; a historical command instead returns a
`reconciled` source revision and requested state. It deliberately omits live-row
and next-run observations, because a historical revision proves neither.
The public OFF toggle retains its existing response and transaction behavior.

Education's registered spaced-repetition handler now locks the reviewed node
and keeps progress, prior-review deletion and individual/batch schedule creation
on one acquired connection and transaction. Its production callback factories
pass that owned connection to the actual core scheduler. Any count, deletion or
creation failure propagates and rolls the progress transaction back. Best-effort
terminal-map cleanup remains separate. This fixes the existing local producer
transaction boundary; it does not supply a verified creator grant or register
new obligation admissions. Recording-store controls prove software ordering and
rollback only. Real PostgreSQL locking, durable readback and source/goal delivery
remain required.

When the review cap selects an already-existing batch, the registered Education
producer refreshes that same `db` prompt schedule through the ordinary core
update operation on the owning progress connection. Its ID is retained, the
next-run/24-hour window is recomputed, and it is enabled for the new review.
A TOML-managed or job-mode name collision refuses without replacing its owner.
Individual creates keep the ordinary strict duplicate-name behavior. This is
local schedule persistence, not creator authentication or goal admission.
The authored PostgreSQL successor induces a real batch-update SQL refusal and
checks rollback independently; its concurrent requests wait behind a held map
row until both actual lock waits are observed. No database execution or forced
contention proof is earned by authoring or collecting that test.


### Named jobs are not automatically deterministic

Startup now inspects actual job handlers selected by the background dispatcher,
using the same final post-approval tool catalog as prompt/subscription checks.
The two Insight delivery handlers can reach cluster synthesis, Chronicler daily
narration can invoke its model, and Memory consolidation can launch a reasoning
session. Their loaded job schedules therefore undergo declaration diagnostics;
`dispatch_mode = "job"` is not an exemption. Missing/invalid declarations and
unknown owning-domain capabilities are reported with closed reason codes and
schedule indices, never names, arguments or source content. Valid local values
still report `fleet_catalog_unavailable`; no local projection becomes a verified
fleet catalog, creator, cause or admitted goal.

The source inventory's known model-backed wrapper list is checked against the
actual registered handlers by its existing single inventory guard. The two
current Insight roster rows remain explicitly missing model-job declarations:
this increment discovers and consumes the gap rather than inventing their full
capability graph. Memory consolidation's goal conversion remains separately
owned by `bu-vbdug9.2`. No schedule, cron, prompt, module or dispatch behavior
changes. Presence, commuting-ETA and Home safety handlers retain explicit
service-plane classification; unknown transitive graphs stay unclassified.
Replacing any of these handlers loses its old classification rather than
inheriting authority from the job's name.

These checks are dark startup diagnostics only. Actual model-call replacement,
complete source declarations, server-owned context/creator and cause producers,
authenticated catalog publication and the real runner/result handoff remain
mandatory before switching these jobs to goals. Direct dashboard model callers
are not covered by the scheduled-job registry; their owner-goal conversion
remains unfinished. The 102-schedule/19-family inventory, deterministic safety
contracts, genuine PostgreSQL execution and hosted delivery obligations remain.
