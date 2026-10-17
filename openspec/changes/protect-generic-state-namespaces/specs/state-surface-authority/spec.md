## ADDED Requirements

### Requirement: [TARGET-STATE] Generic state surfaces enforce a code-owned namespace policy

Generic dashboard state routes and generic state MCP tools SHALL consult one
immutable, code-owned policy keyed by the owning butler plus an exact key or
bounded prefix/suffix rule. Request data, stored state, runtime configuration,
and caller-supplied metadata SHALL NOT create, weaken, or override a policy.
The policy SHALL decide dashboard and MCP read, list, set, and delete operations
separately. Low-level state helpers SHALL remain available only to trusted
same-schema deterministic code and specialized surfaces under their own
contracts.

Unclassified legacy keys SHALL preserve their current generic behavior. Every
new code-owned key or namespace SHALL declare an `ordinary`, `managed`, or
`private` classification before shipping. Conflicting equally specific rules
SHALL fail closed rather than depend on declaration order.

ID: REQ-state-surface-authority-001
Source: heart-and-soul/security.md session sandboxing and anti-patterns;
core-state State Get/Set/Delete/List; dashboard-butler-management State Tab
(CRUD); protect-generic-state-namespaces design policy matrix
Scope: v1-mandatory

#### Scenario: Ordinary state remains compatible

- **WHEN** an authenticated owner or a butler state tool accesses an unclassified legacy key
- **THEN** existing generic list, get, set, and delete behavior remains available
- **AND** adoption of the policy does not silently convert every legacy key into a managed key

#### Scenario: Policy cannot be written through the store it governs

- **WHEN** a caller submits a key, value, prefix, classification, or policy-shaped payload
- **THEN** that input cannot add or weaken a state-access rule
- **AND** the policy is resolved from repository-owned code using the effective butler identity and requested operation

#### Scenario: Conflicting rules fail closed

- **WHEN** two equally specific entries assign different decisions to the same butler, key, surface, and operation
- **THEN** verification or startup fails before the affected generic operation is served
- **AND** declaration order does not choose the winner

### Requirement: [TARGET-STATE] Initial protected namespaces follow the reviewed operation matrix

The initial registry SHALL implement exactly these decisions:

- `home/home:presence:owner_entities`: omit from generic dashboard and MCP
  lists; deny generic dashboard and MCP get, set, and delete; allow only the
  adopted dedicated owner route and deterministic internal reader.
- any butler's `module::` prefix ending in `::enabled` or `::disabled_by`:
  permit authenticated dashboard inspection, deny generic dashboard mutation,
  omit/deny MCP reads and lists, and deny MCP mutation; use the existing module
  surfaces for changes.
- `general/settings.general`: permit authenticated dashboard inspection, deny
  generic mutation, omit/deny MCP reads and lists, and deny MCP mutation; use
  `GET/PUT /api/settings/general` for changes.
- `home/home:thresholds:`: permit authenticated dashboard inspection, deny
  generic mutation, omit/deny MCP reads and lists, and deny MCP mutation; use
  `GET/PATCH /api/home/settings/thresholds` for changes.
- `chronicler/chronicler/owntracks/ssid_places`: preserve all current generic
  dashboard and MCP state operations as an explicit residual risk. Its generic
  dashboard writes proxy the same MCP tools available to model sessions, so no
  restriction SHALL activate until a separately specified trusted owner editor
  can replace that transport without a caller-asserted provenance flag.

No broader namespace SHALL become protected merely by resemblance to one of
these keys. Additional workflow, checkpoint, deduplication, domain-content, or
owner-configuration namespaces require an explicit reviewed classification.

ID: REQ-state-surface-authority-002
Source: bu-bqzuab content-blind current-main authority audit; adopted
home-presence-configuration honest-boundary requirement; design initial matrix
Scope: v1-mandatory

#### Scenario: Home presence has one interactive authority

- **WHEN** a caller lists, reads, sets, or deletes `home:presence:owner_entities` through a generic dashboard or MCP state surface
- **THEN** the generic surface omits or denies the operation without reading or changing the row
- **AND** only the dedicated authenticated Home route may interactively read or replace it after that route and the policy land atomically

#### Scenario: Existing specialized configuration paths remain authoritative

- **WHEN** a caller attempts a raw generic mutation of a module runtime flag, general settings, or a Home threshold
- **THEN** the mutation is denied without changing the row
- **AND** the existing specialized API or tool remains the supported mutation path

#### Scenario: Chronicler protection is deferred rather than fabricated

- **WHEN** no distinct trusted owner editor exists for `chronicler/owntracks/ssid_places`
- **THEN** current generic dashboard and MCP behavior remains unchanged and is recorded as residual risk
- **AND** no caller field, actor string, header, or tool argument is treated as proof that an MCP call came from the owner

### Requirement: [TARGET-STATE] Protected denials and collection reads are content-blind

After central owner authentication, a path-aware ASGI policy guard SHALL return
one fixed `409 MANAGED_STATE_KEY` for an exact denied dashboard operation before
the body-reading audit middleware, FastAPI request-model validation, target
butler pool, MCP client, route handler, or state access. Valid, malformed,
missing-field, duplicate-field, scalar, and oversized protected PUT bodies
SHALL receive the same fixed refusal without being read or parsed by the guard.
An MCP denial SHALL return one fixed tool error before a low-level state
operation. Neither result SHALL echo a submitted or stored value, expected
shape, row existence, validation detail, driver detail, or replacement-surface
state.

Because the path-aware guard bypasses `DashboardAuditMiddleware`, it SHALL emit
exactly one explicit denial audit with operation
`state_generic_policy_denied`. Its field allowlist is server-derived actor,
effective roster butler, fixed `state_operation` (`get`, `set`, or `delete`),
and fixed `outcome=managed_state_key`. It SHALL contain no raw path, state key,
body, submitted or stored value, version, validation detail, or exception. A
denied operation SHALL NOT also emit the ordinary generic success audit.

Dashboard and MCP collection reads SHALL obtain keys before values, apply the
policy, and fetch values only for allowed entries. An omitted entry SHALL expose
neither its key nor a placeholder. Audit rows, logs, metrics, traces, sessions,
tool results, notifications, and errors SHALL remain free of submitted or
stored protected values. Raw caller-created keys SHALL NOT become telemetry
labels.

ID: REQ-state-surface-authority-003
Source: heart-and-soul/security.md; craft-and-care/security-and-secrets.md;
DashboardAuditMiddleware redaction contract; design read/list mechanics
Scope: v1-mandatory

#### Scenario: Denial precedes protected state access

- **WHEN** a generic caller requests a denied exact get, set, or delete
- **THEN** the fixed refusal occurs before target pool acquisition, MCP connection, route-model validation, body buffering, or low-level state access
- **AND** no managed-state value, version, row timestamp, CAS outcome, producer output, or success audit changes
- **AND** exactly one `state_generic_policy_denied` audit is emitted with only the fixed reviewed allowlist

#### Scenario: Malformed protected PUT has the same fixed refusal

- **WHEN** a denied protected-key PUT carries malformed JSON, a missing or duplicate `value`, a scalar body, an oversized body, or otherwise invalid request data
- **THEN** the path-aware guard returns the same fixed `409 MANAGED_STATE_KEY` without invoking FastAPI request validation
- **AND** no submitted content appears in the response, content-blind denial audit, logs, metrics, traces, pool calls, or MCP calls

#### Scenario: List filtering never loads an omitted value

- **WHEN** a dashboard or MCP list spans an omitted private key
- **THEN** the implementation filters from a keys-only projection before fetching allowed values
- **AND** the omitted key and value do not appear in the response or model tool result

#### Scenario: Failure evidence is content-blind

- **WHEN** a protected operation is denied, malformed, unavailable, or fails internally
- **THEN** logs, audit, telemetry, errors, and tool/session evidence contain only fixed operation and outcome categories
- **AND** no submitted or stored protected value or driver exception text is retained

### Requirement: [TARGET-STATE] Home protection and its dedicated owner route roll out atomically

The `home:presence:owner_entities` policy entry SHALL NOT activate before its
adopted dedicated owner GET/PUT route is implemented and verified. The route
and policy entry SHALL land in one reviewed deployable tree so there is neither
an interval with no supported owner editor nor an interval where the dedicated
route claims exclusive validation/CAS while generic mutation remains open.

The rollout SHALL perform no schema migration or stored-value rewrite. Rollback
SHALL keep state rows intact and SHALL change the dedicated route and policy
entry together. A rollback that restores generic access SHALL report that the
legacy bypass has returned and SHALL NOT continue to claim route exclusivity.

This requirement requires explicit owner adoption of Option A in the proposal.
Prior adoption of the Home route contract does not authorize this generic-state
policy, and neither adoption authorizes deployment or live state operations.

ID: REQ-state-surface-authority-004
Source: adopted home-presence-configuration atomic/CAS contract; bu-zdeflb;
protect-generic-state-namespaces proposal owner gate
Scope: v1-mandatory

#### Scenario: Protection cannot strand current configuration

- **WHEN** the dedicated Home route is not present in the exact deployable tree
- **THEN** the Home managed-key policy entry is not activated by itself
- **AND** the system does not falsely claim that a supported replacement editor exists

#### Scenario: One tree closes the CAS bypass

- **WHEN** the dedicated Home route and its managed-key policy are implemented after owner adoption
- **THEN** they are reviewed and delivered in the same tree
- **AND** a generic dashboard or MCP writer cannot race or bypass the route's validation, advisory lock, or version CAS

#### Scenario: Rollback is honest and non-destructive

- **WHEN** the combined implementation is rolled back
- **THEN** no state row or version history is rewritten or deleted by the rollback
- **AND** route and policy behavior remain aligned, with any restored legacy bypass reported explicitly

### Requirement: [TARGET-STATE] Implementation evidence exercises real authority seams

Implementation SHALL include pure policy matching tests, mounted central-owner
authentication and generic API tests, MCP handler tests, and migrated
PostgreSQL Home concurrency/rollback tests. Tests SHALL prove every operation
in every initial matrix row, ordinary-key compatibility, specialized-path
continuity, protected list filtering before value reads, zero version change on
denial, direct dashboard tool-call resistance, and content-blind evidence.

The implementation PR SHALL state `Tests: +a ~b -c`, preserve the integration
test budget through condensation where necessary, and obtain independent
exact-head review plus terminal hosted CI. These results SHALL NOT authorize
deployment, private identifier submission, live state mutation, or accepted
risk for an unclassified namespace.

ID: REQ-state-surface-authority-005
Source: craft-and-care testing-and-verification and security standards; design
future verification packet
Scope: v1-mandatory

#### Scenario: Denied generic writer cannot join a dedicated-route race

- **WHEN** a dedicated Home PUT races a generic dashboard or MCP write to the managed key
- **THEN** the generic operation is denied before state access and cannot change the row version
- **AND** dedicated-route CAS and rollback assertions execute against migrated PostgreSQL

#### Scenario: Review evidence does not imply operational authority

- **WHEN** implementation tests, independent review, and hosted CI pass
- **THEN** the repository change may proceed only under its separately granted implementation and merge authority
- **AND** no live state read/write, deployment, restart, or private value submission follows automatically
