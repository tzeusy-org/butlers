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

The delivered Chronicler gap-interview now runs its existing deterministic job;
it retains no declaration and cannot inherit the day-close notify ceiling. Both
Relationship prompt blocks remain undeclared pending their separately owned
conversions. The census records that delivered dispatch mode while retaining
99 current schedule identities and 19 producer families, including unfinished ones.
The historical 102 included three Decision Desk schedules retired by the adopted D19 decision.

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
domain plus canonical tool name. Each resource parameter also carries an explicit
`selection_bound` boolean. The three Travel inventory readers use false for
each of their actual resource parameters (an explicit empty map for the reader
without resource arguments), preserving dynamic server authorization rather
than creating an owner-selection grant. Missing metadata fails eligibility.
The phased startup carries the exact seeded runtime snapshot and owning pool
through `StartupState`; final-registration capture performs no replacement
policy read. It is a local value projection, **not** the
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
`GoalAdmission`. The default `obligation_source_mode = "off"` performs no source
or goal writes and keeps today's dispatch paths unchanged. Explicit dark source
staging is described below; it does not enable admission. In particular a passing value test
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

### Delivered fleet metadata at admission

The delivered final-registry publisher and `PublishedCatalogProvider` now retain
each descriptor's exact registered resource parameter names in the immutable
admission projection. The actual `derive_admission()` consumer rejects an
obligation or staffer contract that constrains a key absent from that descriptor;
a valid capability ID cannot make a misspelled resource key authoritative.
An empty parameter tuple means the capability has no resource dimensions, not
an unrestricted set of parameter names. Local declaration diagnostics preserve
the same exact metadata.

This checks dimension names only. It does not resolve recipients, populate
owner-selection records, authenticate a creator or effect, or waive the broker's
per-call resource and approval checks. The reader retains its existing
Switchboard role and same-transaction complete-fleet checks; domain startup does
not gain a new catalog SQL privilege or an admission switch. Source/creator/cause
and runner handoff remain separate required producers.

Core-274 now follows the actually delivered core-275 publication migration.
Only the migration ancestry changes; its owning-source SQL, role checks and
durable evidence/downgrade contracts are unchanged. The existing real-PG catalog
owner additionally exercises malformed-resource refusal and independent committed
decision readback; authoring or collecting it is not a PostgreSQL execution pass.


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
remains unfinished. The current 99-schedule/19-family inventory, deterministic safety
contracts, genuine PostgreSQL execution and hosted delivery obligations remain.

### Held Git source factory and full owning reconciliation

The strict loader now retains the hash of its exact TOML bytes and a hash of
its effective typed obligation inputs. After the final approval-wrapped registry
is built, real startup captures those inputs together with the held effective
runtime policy and local registry generation. Mutating schedules or declarations
after loading refuses the capture. No second TOML parse or runtime-policy query
is used. The policy scope is explicitly `held_effective`, including existing
legacy defaults/stale-cache semantics; the registry scope is explicitly
`local_registration`. Neither is proof of current persisted policy, an
immutable verified fleet publication, a D7 invocation, a creator grant, an
admission mode or a complete invocation/system-prompt context.

`reconcile_loaded_git_sources` is the owning full-inventory adapter. Unlike
selected prepared sync, a complete held config explicitly removes absent Git
scheduled rows and tombstones their source revisions, cancelling pending and
claimed occurrences in the same transaction. It preserves runtime rows, source
origins, existing nominal run slots and dormant new rows. A missing declaration,
job conversion or unavailable local material cannot create a fresh active
source. A dangling active source without its actual scheduled row refuses;
absence alone does not prove deletion. The owning role, relation identity,
full-inventory lock, command/source lock order and whole-transaction rollback
remain mandatory. An already-tombstoned source with a stale TOML row is cleaned
up without a successor revision, duplicate outbox entry or history removal. A
desired row cannot revive that deleted source identity.

A source definition's `active` state describes available configuration material;
it does not admit a goal or validate grants against a fleet catalog. Admission
still requires its actual verified EvidenceProvider and CatalogProvider. Full
reconciliation reads back current desired state; it does not masquerade as
historical command replay. An unconfirmed commit remains unavailable until
explicit current-state reconciliation, rather than a hash-based success claim.
The existing selected-command replay contract is unchanged.

Startup only captures this snapshot. Default OFF still performs no B2 source
I/O and retains ordinary schedule sync/dispatch. Registering durable source
reconciliation with an actual trusted mode/lifecycle selector, full runtime
creator/cause/feed adapters, verified fleet publication, complete invocation
context and genuine goal-runner handoff remain required full-issue work. The
recording-connection controls are software evidence, not PostgreSQL role,
concurrency, locking or commit-durability proof.


## Owning cron/deadline source staging

`[butler.scheduler].obligation_source_mode` is a strict `off`/`dark` selector;
its default is `off`, and no roster setting is enabled by this change. It selects
owning-source persistence only. It is not an admission, permission or activation
switch and cannot supply verified fleet, invocation, creator or cause evidence.

After ordinary TOML sync and final approval-wrapped registration, startup uses
the same strict-loaded config, held runtime policy and final local registry to
capture source material. Explicit dark staging reconciles the **whole** Git
source inventory in one owning transaction before registering its immutable
scheduler binding. Missing/invalid input or unconfirmed reconciliation refuses
registration; a hash is not commit proof. OFF returns before any source IO.
The held-policy/cache and local-registration limits remain explicit.

Both the daemon loop and the zero-argument manual MCP tick closure receive that
same server-held binding. No public/model argument can supply a source runtime.
Job-mode, undeclared and unregistered runtime rows retain their ordinary path;
real authenticated runtime creation remains a separate required producer.

For a registered cron row, the source adapter locks inventory, optional command,
source, then row; checks the current revision and complete source material; and
uses the **selected original `next_run_at`** in the existing scheduler CAS.
Advancing the slot, capturing its revision-bound occurrence, claiming it and
writing its reference-only outbox occur in the same transaction. The staged
`DarkSubmission` is returned only after commit and pool release, before legacy
dispatch. Another CAS winner yields no new occurrence. The normal cron dispatch
failure semantics remain: its already-claimed slot is consumed. Auto-disable
retires and cancels dark work while retaining the legacy final dispatch behavior.

Deadlines retain their different ordering: dispatch failure leaves the threshold
unfired and writes no source occurrence. After successful dispatch, a transaction
rechecks the persisted target, selected threshold/progress and current material;
records the threshold and captures/claims its date-plus-threshold occurrence
together. Expiry disables the actual row and cancels staged work in one owning
transaction. No network/model callback runs under these source locks. This does
not add a new pre-dispatch deadline claim or repair the legacy possibility of
concurrent deadline dispatches; source recording itself is fenced and idempotent.

A source refusal in explicit dark mode is not successful admission. An unknown
transaction/commit/release outcome never authorizes a second legacy CAS or blind
retry. Actual PostgreSQL role, two-actor locking, rollback and independent
committed readback controls remain separate from software collaborators; newly
authored PG controls are not runtime evidence until executed. Publication also
requires composing core-274 after the actual delivered core-275 prerequisite;
no unmerged migration or fleet authority is imported here.

The real cron/deadline source consumer now builds the closed obligation payload
beside its claim. It copies the capability set, material revision and immutable
creation exposure/invocation from the fenced owning definition; Git sources
carry no creator invocation and are not created exposed. The stored source kind,
identity, occurrence digest and source key must agree with that definition.
The payload and claim token are internal values and must not enter traces or
public/model request schemas. They are not verified `Evidence`, a D7 creator
assertion, a catalog publication or an admission receipt.

`GitScheduleRuntime.read_committed_submission` is the owning-side readback seam
for a future authenticated handoff. It takes a fresh owning transaction and
rechecks the registered source material, current revision, complete payload and
exact claim token. A nested caller transaction, changed row/revision, cancelled
claim, forged payload or unconfirmed release refuses. Its source locks cover
only that local read; they are not a cross-store fence around a later request.
The current scheduler still dispatches a legacy CAS winner even if a subsequent
source read would refuse. Such a historical payload must not become a current
handoff merely because that dispatch succeeded. No control endpoint or admission
call is installed by this readback API, and runtime creator/feed/cause adapters
still require their real authenticated producers.


The two registered Switchboard Insight delivery jobs now declare their
`switchboard__notify` communication ceiling without changing either cron, job
arguments, selection, delivery, or feedback behavior. The startup source
factory checks the actual handler identity used by the background dispatcher;
a familiar job name, an unknown replacement callback, or `dispatch_mode=job`
alone never establishes model-job coverage. A recognized declared model job
uses the owning `model_job` source family and the existing nominal cron CAS,
with a distinct occurrence for each persisted nominal slot. The default OFF
path still makes no source-repository calls.

Model-job source payloads conservatively start exposed because the current
Insight candidate records do not supply verified transitive lineage. The local
registration and communication ceiling establish neither recipient authority
nor a verified fleet catalog. This is durable dark staging of the real job
entrypoints, not a switch of Insight synthesis into the goal runner: selected
candidate input binding, genuine D7 source/cause transport, and the separately
owned runner remain required before that cutover. The deterministic delivery
pipeline and its existing model fallback continue unchanged in dark mode.

### Migration caller and test-observer boundaries

`core_274` anchors its five owning tables and three immutable-trigger functions
to the existing, qualified `scheduled_tasks` migration owner. A managed-bootstrap
re-upgrade therefore preserves the ordinary migration identity rather than making
its caller the permanent owner. For a legitimate shared-predecessor stamp with
no local `scheduled_tasks`, the newly created local `obligation_definitions`
relation supplies the actual owning identity; no peer schema or caller-supplied
identity is substituted. An additional real-Postgres control exercises that
stamped replay with separate committed metadata and individual-ACL readback.
It explicitly converges the same own-schema and
public runtime table privileges as bootstrap, regardless of creator default ACLs.
It adds no runtime role membership, TEMP permission or statistics-reading grant.
The existing ordinary-login migration round-trip schema oracle remains unchanged;
a separate real-Postgres control checks owner identities, individual effective
privileges and ordinary metadata from a fresh connection after re-creation.

The shadow-table attack uses fixture administration to create the shadow, then
switches that same transaction to the real owning runtime before the relation
check. The concurrent claim control uses a separate authorized fixture observer
for the exact blocked and blocking backend PIDs; both claim actors retain their
runtime role. These are test setup and observation privileges, not production
source or creator authority. Corrected PostgreSQL bodies require hosted execution.

The scheduler also passes its one evaluation date to both deadline date helpers.
Threshold selection and durable expiry therefore use the same injected instant,
including at midnight. Existing direct helper callers that omit `as_of` retain
their current-UTC-date behavior; neither clock exemptions nor test-only result
substitution are needed.


### Private same-owning Git admission

`obligation_source_mode = "dark"` still selects durable staging only. The
separate strict `obligation_admission_mode` defaults to `"off"`; `"dark"`
requires dark staging and the actual Switchboard lifecycle source runtime.
No roster enables it. Other-domain sources, runtime creators and caused feed
handoffs remain outside this private adapter. It adds no endpoint or runner.

The scheduler finishes its existing dispatch/outcome path and releases the
source mutation transaction before passing its committed candidate to the
private consumer. GoalAdmission locks the durable attempt/source identities,
then the provider uses that **same connection** to lock owning inventory,
definition, schedule row and occurrence/claim. It reconstructs and compares
the whole held definition, material, exact declaration and claim tuple; those
fences remain through the admission commit. An earlier readback object alone
cannot authorize a later transaction. There is no second pool or network call
under these locks.

One current complete fleet publication snapshot must contain the held actual
Switchboard registry. That same snapshot projects both typed owner-notify
eligibility and resource parameter keys. Eligibility does not establish an
actual recipient's ownership or call authority. Missing/stale publications,
role/shadow mismatch, changed source or invalid resources refuse. Additional
context is conservatively exposed; a held policy digest does not prove D7.

The occurrence UUID is the durable admission attempt identity. Commit loss is
UNKNOWN, never a success or a fresh-ID retry; explicit reconciliation of that
same identity can recover immutable committed history. Each operation owns one
five-second deadline, including cancellation cleanup. Resistant work is retained
and quarantines the consumer; shutdown explicitly fails if it is still unsettled,
without resetting the deadline. Existing dispatch is not suppressed by refusal.
A committed goal records its source/declaration references, not an executed
prompt/context payload. Runner handoff and actual Insight execution stay separate.

The delivered Switchboard is a staffer, whose actual notification registrar
excludes `notify`. Its two existing Insight declarations request
`switchboard__notify` and therefore refuse against the actual final registry.
The private mechanism's positive tests use a loaded Git read declaration for an
actually registered Switchboard tool. They do not deliver those two Insight
workflows or all 99 current schedules/19 dynamic families. Runtime creator, Finance
reaction cause, feed, runner, genuine PostgreSQL and delivery gates remain.

## Insight selected-content dark preparation

The real daily and urgent Insight collectors have an optional private preparation
consumer (`BUTLERS_INSIGHT_DARK_PREPARATION=1`, absent by default). It observes
selected content before formatting/model/notify calls; it changes no dispatch
mode, recipient resolution, delivery bookkeeping or capability registration.
Job arguments cannot enable it. Unavailable preparation is reported as a closed
`unavailable` outcome while legacy delivery continues; caller cancellation still
propagates. No new model calls or notifications are made by preparation.

The Switchboard source transaction takes its Git inventory/command/source locks,
locks and rereads the exact pending `public.insight_candidates` rows, and compares
the complete selected material. It persists canonical raw and decorated text,
selection order, context/selection reference and the exact deterministic synthesis
prompts in the immutable revision body. Candidate IDs identify a selected-set
occurrence, **not** a content revision. Full material determines the revision.
Changed/deleted content, contradictory replay, a stale claim or a tombstone refuses;
a lost commit/release reply stays unconfirmed until original-identity readback.
The existing source revision, occurrence, stable claim and reference-only outbox
commit together. No model/network call runs under those source locks.

Prepared-action statuses and premise verdicts are captured observations used to
reproduce that cycle's decorated text. They neither authorize the action nor
promise a different domain's state remained unchanged through this transaction.
The source's creator remains absent and its admission status is explicitly
`unverified_unavailable`. The real STAFFER `notify` catalog refusal remains; no
empty ceiling or recipient/grant/creator authority is substituted. This is M6 AC2
payload preparation; staging alone is not an admitted goal, D7 proof, runner
delivery or D18 cutover.

When the existing source and admission controls are both `dark`, lifecycle also
holds a private Insight admission consumer built from the final post-approval
registration and captured configuration. The daemon supplies it only to the two
exact registered Insight handlers, outside job arguments. Source staging releases
its transaction and pool acquisition before admission takes its immutable
attempt/source-ledger locks. On that admission connection the selected-source
reader locks the owning inventory, full definition, pending candidate rows,
occurrence and claim, and verifies the complete current material and held handler
before the durable decision commits. A selected-set UUID is not a scheduled-task
ID, so this path does not relax the ordinary Git schedule reader.

The actual whole current fleet is read once, preserving native-notify eligibility
and resource metadata together. The current STAFFER Switchboard has no native
notify capability: deriving its requested contract refuses `invalid_contract`;
the existing admission service persists the minimized
`source_evidence_unavailable` refusal. No positive Insight admission or notify
authority is substituted. The collector still returns `staged_unverified`, never
an execution receipt, and its ordinary delivery/amendment/failure behavior stays
in place. Both controls OFF or preparation-only add no admission IO. A single
five-second owned lifetime covers preparation plus admission; incomplete workers
remain retained and quarantine repeats, and daemon shutdown attempts downstream
releases before propagating the truthful incomplete outcome. Runner payload,
result, actual delivery and D18 execution remain separate unfinished work.

Full Git reconciliation recognizes only this fixed registered Insight subtype:
matching owning role/domain, immutable identity/body/declaration and current
loaded configuration and candidate material. It preserves a current prepared
source, tombstones removed/changed ones and cancels their occurrences, and still
refuses unknown/malformed/dangling ordinary Git sources. It introduces neither a
new origin nor a fabricated runtime creator. Existing source bodies without this
optional payload retain their original encoding.


## Durable domain-wake dark preparation

The existing `butler.scheduler.obligation_source_mode="dark"` also binds a private
preparer to the daemon's registered `receive_domain_event` closure. The default
`off` returns before inspecting configuration or adding bus/source IO. No public
argument selects a preparer, and this path does not call GoalAdmission or change
legacy wake dispatch, validity semantics or reaction reporting.

After successful legacy creation/reconciliation, the preparer uses one fresh
owning-role transaction. It locks the owning inventory, command and source before
the durable shared event, active subscriber, delivery and subscriber-local task.
It checks the actual loaded domain declaration, held policy/final local registry,
complete event payload and exact generated task material. A matching footer or
transport event ID alone cannot authorize staging. The existing bus writer's
serialized JSON-object representation is decoded conservatively; scalar or
malformed data refuses dark preparation.

The immutable `occurrence_captured` journal carries only occurrence/event,
subscription, delivery and task UUIDs plus a full-material digest. Replay compares
the first binding, rather than appending changed input beside it. Committed dark
readback independently repeats the durable checks and claim fence; source failure
or lost commit acknowledgement never becomes claimed success. Stable source,
occurrence and claim identities allow explicit reconciliation. No raw event or
prompt is added to the journal.

Missing publishing-owner stamps always project `created_exposed=True`, regardless
of payload-supplied owner, exposure or lineage fields. The current bus does not
provide verified causing-goal/effect assertions: this is durable dark preparation,
not caused admission, creator/Grant evidence or runner execution. Finance still
has no actual domain declaration, and this mechanism does not add one or certify
all current 99 schedules / 19 families.

Each operation retains its own five-second absolute lifetime. Cancellation,
including repeated cancellation, joins that same task without resetting grace;
an incomplete release stays retained and refuses further work. Daemon shutdown
attempts the remaining resource owners before propagating an incomplete close or
cancellation. Local software controls and PG collection establish neither genuine
PostgreSQL outcomes nor enabled production delivery.

The private Switchboard accepted-feed consumer is selected separately with
`butler.scheduler.obligation_feed_admission_mode = "dark"`; its strict default
is `"off"`, before any added Home configuration, catalog or source reads. No
roster enables it. The registered ingest closure offers only the committed
canonical request UUID after existing legacy queue setup, including duplicate
acceptances. It does not change the response, global Home Assistant skip policy,
connector filters, deterministic Home history/presence writers or dispatch.

The admission transaction takes its existing attempt/source-ledger locks, then
locks the owning inbox, matching public canonical event and exact connector
registry composite-key row. This private reader requires explicit `feed`
classification, rather than the public helper's legacy message fallback.
Persisted skip, metadata-only, quarantine, missing content and unmatched feeds
cannot yield a reasoning goal. Only the actual loaded Home
`ha-event-response` subscription and one verified current fleet snapshot can
supply the restricted Home ceiling. The same snapshot supplies resource keys
and typed native notify eligibility; actuation, transaction, administration and
owner-authority capabilities are refused. Full accepted sanitized material and
loaded declaration material bind the durable goal contract digest. Input labels
and notes remain exposed; they establish no owner stamp, recipient, creator,
Grant or caused-goal lineage.

The request retains `SourceRef("message", canonical_uuid)` and a deterministic
attempt UUID across retries and registry-kind drift. Committed no-goal decisions
remain no-goal. Lost commit replies remain unavailable until that same durable
identity is read back. Scheduling the private operation is not admission
success: one retained operation has a single five-second absolute bound;
capacity, cancellation and incomplete cleanup are explicit unavailable states.
Shutdown attempts downstream resources even after an admission close error.
The new migrated-role/concurrency/readback cases are authored and collected
locally only. A goal reference and ceiling are not runner prompt execution or
Home domain recording. The original full nine criteria, real creator/cause,
runner, hosted PostgreSQL and delivery obligations remain separate.


## Chronicler reduced-material dark preparation

The existing registered `chronicler_narrate_daily` handler optionally selects a
private preparer with `BUTLERS_CHRONICLER_DARK_PREPARATION=1`. The default is off
before any added configuration, source or catalog IO; job arguments cannot enable
it. Narration still applies its original disabled, no-data and feeder-dark
skips, builds the same reduced prompt, and uses the existing discretionary model
call and narrative writes. Source refusal is content-free and does not change
that legacy result. Cancellation propagates.

After those skips, the preparer captures the closed local date/timezone, actual
rollups/flags, capped selected titles and complete prompt. It verifies the
configured module's real registered default entry, current owning scheduled row
and configuration. Its explicit version-1 declaration has an empty capability
set: no native narrative writer is registered. It cannot borrow day-close
read/notify capabilities, synthesize a creator/Grant, or admit a reasoning goal.

One fresh Chronicler-role transaction takes inventory, command and source locks,
then locks the scheduled row and all underlying reduced-material tables through
commit. Temporary material shadows refuse. Re-reading actual storage helpers on
that connection must reproduce the captured input; a digest or caller DTO alone
is insufficient. The existing repository commits a typed `model_job` revision,
occurrence, claim and content-free outbox reference. The passed-connection
readback repeats those fences. Reconciliation handles only this exact Chronicler
subtype and preserves the separate Insight subtype.

The private caller has one five-second absolute bound, including its cleanup
wait. A fixed pool-scoped owner retains the transaction and the actual pool
release until settlement, including asyncpg's shielded holder reset. Timeout or
repeated cancellation never returns a committed reference while release is
incomplete, and quarantines further preparation. Daemon shutdown closes that
owner and defers shared-pool close until all its retained work has settled;
an expired deadline earns no new grace and reports incomplete cleanup. OFF
creates no owner and preserves legacy narration. The native asyncpg pool is not
weak-referenceable; its unique pool-owned queue anchors the weak owner registry
through close. The registry has no strong edge to that queue or pool, and a
discarded settled pool does not leak an owner. A lost commit acknowledgement is unavailable;
retry reconciles the same captured day, command, occurrence and claim rather than
creating another attempt. Changed material cannot silently renew that identity.
These identities do not enforce a global once-per-day model allowance across
legacy retries, replacements or recovery; that remains a real gap.

The new controls prove software repository-protocol behavior and collect authored
migrated-role PostgreSQL cases only. Actual PostgreSQL outcomes, a native writer
and verified current catalog/admission remain unearned for this increment.
The inventory remains 99 schedules / 19 families; this preparation-only path does
not certify the full original eight functional criteria or their test estimate.
