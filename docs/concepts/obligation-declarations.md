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

Current startup runs `diagnose_loaded_schedules()` only after core/module
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
verification and transactional source repository remain required before any
production caller can construct authoritative evidence.

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

The remaining full bu-o9qax4 work includes roster declarations; current parser and
schedule/subscription sync integration; source revisions and durable occurrence
storage; verified creator/cause interfaces; final-registry publication; actual
scheduler/deadline/chain/feed/model producers; Home Assistant's read/record/owner
notify default; removal of unsupported travel-time wording; lifecycle/roster docs;
and real source/admission commit and replay proof. The existing D18 model-function,
lead-policy, memory-consolidation, project and allowance units retain their scopes.
There is no switch activation, migration application, deployment or live send.

## Prepared owning schedule transaction

`obligation_schedule_sources` is an unregistered owning-schema adapter, not a
replacement config parser or a trusted caller. It copies the complete normalized
scheduled-row material and declaration. Missing actual context material makes
the definition unavailable; a supplied Python value does not authenticate the
context, Git provenance, catalog or creator. The real context producer and typed
config/sync carry-through remain required before production registration.

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
actual config/sync and context producers, final occurrence fences, runtime
creator/cause and other source families remain full-issue implementation work.

## Engineering context decision

[decision] Keep QA's useful Diagnostic Context section, anonymize its contents
through the existing RFC 0015 anonymizer, JSON-quote it, and label it untrusted
incident evidence rather than instructions or owner authority. Drop all five
ambient situational/memory/timezone/routing/blind-spot reads for `qa` and `healing`
before prompt composition. Preserve their empty MCP configuration. Ordinary
schedule context reads and MCP behavior remain unchanged. This changes source
behavior without granting engineering executors owner authority or goal access.
