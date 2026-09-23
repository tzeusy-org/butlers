## Evidence and corrected premise

`OwnerAuthMiddleware` now authenticates generic `/api/*` reads and requires
CSRF plus exact Origin for unsafe cookie-backed requests. The generic dashboard
state API is therefore an owner surface, not an anonymous network surface.
Its authorization remains all-or-nothing: `StateSetRequest.value` accepts any
JSON value, PUT and DELETE accept any path key, and writes proxy to the same
MCP tools exposed to a daemon session. `state_set` increments a stored version
but does not compare one; the separate `state_compare_and_set` helper is not
used by the generic routes or tools.

The caller boundaries are:

| Caller | Current authority | Proposed boundary |
| --- | --- | --- |
| Authenticated dashboard owner | Generic list/get plus MCP-proxied set/delete for every key | Policy-filtered generic operations; specialized owner routes remain authoritative for managed writes |
| Ephemeral per-butler LLM session | Every enabled state MCP tool in its own schema | Policy-filtered state tools; no protected value or mutation reaches the model surface |
| Dashboard MCP tool-call tab | Owner-authenticated, then invokes the same daemon tool | The MCP policy still applies, preventing a second route around API filtering |
| Deterministic daemon jobs/modules | Direct low-level `butlers.core.state` calls in their own schema | Unchanged; policy belongs at exposed API/MCP adapters, not the storage primitive |
| Specialized API/tool | Validated domain contract, often backed by the same row | Unchanged and named as the only allowed managed mutation path |
| Database owner/migration login | Trusted host/database authority | Unchanged and outside application authorization |

Schema isolation limits a caller to one butler but does not distinguish keys
inside that schema. `core_groups` can remove the whole state tool group, but it
cannot preserve ordinary state use while protecting one key. A denylist inside
only the dashboard router would leave direct MCP calls open; a check inside only
the MCP wrapper would leave raw dashboard reads and would make denial dependent
on daemon reachability. The policy must be shared by both exposed adapters.

## Policy representation and matching

The future implementation owns an immutable registry in repository code. Each
entry contains a butler name, one exact key or a bounded prefix/suffix matcher,
and decisions for these operations:

- dashboard `list_value`, `get`, `set`, `delete`;
- MCP `list_key`, `list_value`, `get`, `set`, `delete`;
- the named specialized surface, if any.

Only the most-specific match applies. Duplicate or equally specific conflicting
entries fail tests/startup rather than choosing by declaration order. The
registry is not stored in the state table, runtime config, TOML supplied to a
model, or a request. Low-level storage helpers do not consult it: internal
deterministic readers and specialized routes need a stable seam that generic
callers cannot invoke remotely.

An unclassified legacy key retains current generic behavior. This is a
compatibility default, not a sensitivity conclusion. A repository guard over
the registry's explicit namespace inventory requires each newly introduced
code-owned exact key or prefix to declare `ordinary`, `managed`, or `private`.
Dynamic user-created keys remain ordinary unless a governing capability
reserves their namespace.

## Initial operation matrix

`allow` means preserve current behavior. `inspect` means an authenticated
dashboard owner may read/list the value, but generic mutation is refused.
`omit` means a list does not return the key or value. `deny` means a fixed
surface error before MCP/storage access.

| Butler and key rule | Dashboard list/get | Dashboard set/delete | MCP list/get | MCP set/delete | Specialized/internal path |
| --- | --- | --- | --- | --- | --- |
| `home` exact `home:presence:owner_entities` | `omit` / `deny` | `deny` / `deny` | `omit` / `deny` | `deny` / `deny` | Adopted dedicated owner GET/PUT; deterministic producer low-level read |
| any butler `module::` prefix with `::enabled` or `::disabled_by` suffix | `inspect` | `deny` | `omit` / `deny` | `deny` / `deny` | `module.states` and `module.set_enabled` retain their existing contract |
| `general` exact `settings.general` | `inspect` | `deny` | `omit` / `deny` | `deny` / `deny` | `GET/PUT /api/settings/general` |
| `home` prefix `home:thresholds:` | `inspect` | `deny` | `omit` / `deny` | `deny` / `deny` | `GET/PATCH /api/home/settings/thresholds` |
| `chronicler` exact `chronicler/owntracks/ssid_places` | `allow` | `allow` | `omit` / `deny` | `deny` / `deny` | Transitional owner-authenticated generic dashboard editor; future dedicated route required before dashboard denial |
| all other keys | `allow` | `allow` | `allow` | `allow` | Existing behavior |

The Chronicler row is deliberately not labelled fully protected while its only
documented editor is generic dashboard state. Central owner authentication and
generic audit redaction still apply, but validation/CAS remain absent and must
be described honestly. Removing that dashboard access requires a separately
specified replacement; MCP access need not remain open for compatibility.

Workflow/checkpoint, deduplication, domain-content, and additional owner-config
namespaces found during the content-blind inventory are candidates, not silently
protected by this first matrix. The implementation packet must record their
classification without inspecting values and propose follow-ups for any entry
that needs a replacement surface.

## Read/list mechanics and content blindness

An exact denied GET checks policy before acquiring a pool. A denied dashboard
write checks policy before acquiring an MCP client or parsing/forwarding the
submitted value beyond central authentication/audit handling. It returns one
fixed `409 MANAGED_STATE_KEY`; the response does not echo the key, submitted or
stored value, expected shape, specialized-route state, or row existence.

Collection reads must not fetch a private value and discard it afterward.
They first obtain only keys/timestamps, apply policy, then fetch values only for
allowed or inspectable keys. MCP `state_list(keys_only=True)` likewise uses a
keys-only query. `keys_only=False` fetches values only after filtering. Omitted
keys produce no placeholder that could reveal presence. MCP direct denials use
one fixed tool error and attach no raw value or exception detail to telemetry.

The existing audit middleware redacts the generic `value` field. Future denial
tests still prove that request values, stored values, list values, and driver
errors do not enter audit rows, logs, metrics, traces, session/tool results, or
notifications. Key-policy metadata is low-cardinality and must not include a
caller-created raw key as a metric label.

## Atomic Home rollout and compatibility

The first implementation must combine these effects in one reviewed PR and
deployable tree:

1. implement the already-adopted, owner-authenticated Home presence GET/PUT
   contract with its validation, local-reference check, advisory lock, version
   CAS, rollback, and content-blind audit;
2. add the shared policy registry and API/MCP enforcement;
3. activate the exact Home policy entry only in that same tree; and
4. update the active Home change's honest-boundary text to describe the now
   closed generic paths without overwriting unrelated requirements.

This sequencing neither treats prior Home-spec adoption as authority for this
new policy nor strands existing configuration. The implementation requires the
owner's separate Option A adoption here. Ordinary state keys and their State-tab
UX remain wire-compatible. Managed denials are intentionally new behavior and
must be surfaced as a stable unavailable-via-generic-state message, with a link
to a specialized screen only when such a screen exists.

There is no schema migration and no stored-value rewrite. Rollback retains
state rows untouched. If the combined implementation must be reverted, revert
the dedicated route and its Home policy entry together and report that the
legacy bypass has returned; never leave the policy active without a supported
owner editor or claim the route remains exclusive after its guard is removed.

## Future verification packet

### Pure policy and compatibility tests

- exact, prefix-plus-suffix, most-specific, duplicate/conflict, and unknown-key
  decisions;
- ordinary unclassified keys preserve current API/MCP behavior;
- every initial matrix row exercises every declared operation;
- namespace-inventory guard rejects a newly introduced code-owned key lacking
  classification without relying on state values.

### Mounted API and MCP tests

- central owner auth still rejects unauthenticated generic reads and writes;
- denied exact GET/set/delete fail before pool/MCP/storage access with the
  fixed content-blind category;
- collection listing never selects or serializes an omitted value;
- MCP get/set/delete and both list modes omit/refuse protected data before
  calling low-level state helpers;
- direct dashboard tool invocation cannot bypass the MCP check;
- inspectable managed rows remain owner-readable but raw writes are denied;
- the Chronicler transitional owner editor remains usable while every MCP
  operation is denied.

### Real PostgreSQL and Home contract tests

- the dedicated Home route and generic guard run against the migrated schema;
- two divergent dedicated PUTs from one version produce one winner, while a
  generic API or MCP write cannot participate or increment the version;
- validation, malformed-state refusal, transaction rollback, exact retry, and
  producer compatibility remain as adopted;
- module/general/threshold specialized paths still work while raw mutations
  are denied;
- no denied operation changes a value, version, timestamp, audit consequence,
  or producer output.

Report `Tests: +a ~b -c`, run the dirty-worktree test planner, focused mounted
auth/API/MCP and real-Postgres cases, collection for any moved tests, Ruff,
strict OpenSpec, overwrite/countable/guards/budget, and terminal hosted CI.
Implementation, merge, deployment, private value submission, and live exercise
remain separate authorization gates.
