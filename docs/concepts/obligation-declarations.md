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
which due sources exist. Startup wiring and the full published catalog must
provide those inputs; the current Phase A does not install that wiring.

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
`GoalAdmission`, no source or goal table is written, and today's dispatch paths
remain unchanged. In particular a passing value test does not prove committed
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

## Engineering context decision

[decision] Keep QA's useful Diagnostic Context section, anonymize its contents
through the existing RFC 0015 anonymizer, JSON-quote it, and label it untrusted
incident evidence rather than instructions or owner authority. Drop all five
ambient situational/memory/timezone/routing/blind-spot reads for `qa` and `healing`
before prompt composition. Preserve their empty MCP configuration. Ordinary
schedule context reads and MCP behavior remain unchanged. This changes source
behavior without granting engineering executors owner authority or goal access.
