## ADDED Requirements

### Requirement: Source admission and one hold authority

There SHALL be one authoritative custody-hold model. Any butler MAY initiate a bounded proposal via Switchboard MCP, but ordinary hold writes SHALL execute only on Switchboard-owned admitted service paths; no model-facing tool argument, DTO, origin name, source_entity_id or route endpoint string SHALL mint authority. Current owner-authenticated dashboard/host control SHALL be verified at its owning boundary and forwarded as a protected command, not an actor field. Forced RLS SHALL exclude peer runtime hold mutation, release, DELETE and generation reset despite bootstrap public re-grants. A bounded content-blind read definer SHALL expose eligibility without granting peer private-schema access. Existing public-table accepted risks SHALL remain unchanged. Full supported independent-process custody admission SHALL remain mandatory: the cohosted Linux companion does not fulfil it. Every supported process SHALL use the complete proposed P-CUSTODY-1/P1 current-principal/source-owned protocol, including protected host enrollment, live DB backend incarnation, writer binding, canonical owning-source digest, destination/operation challenge and final current COMMITTING checks. These exact bounded P1c feature interfaces/EXEC remain proposed engineering under existing trusted startup authority, never installed facts or a generic missing-code owner gate. A cohost-only topology reduction is unselected and cannot count as completion. P1c SHALL use the complete constructor-fixed source-owning adapter and actual dedicated backend/startup enrollment under the existing trusted host/daemon/SET ROLE/MCP premise. No external caller or ordinary same-role unbound backend SHALL choose its source family, bind an arbitrary nonce or self-enroll. Live DB backend_start/incarnation, actual writer acquisition generation and explicit UNBIND, canonical own-row digest, destination/operation online challenge, registered guard and final current owner/control/target checks SHALL be mandatory across all supported topologies. P1c SHALL NOT claim hostile same-UID parent-memory/filesystem/FD, compromised trusted connecting-admin-login or hostile-host isolation; current permission-bypass/env/mode0400 supplies none. This limitation SHALL NOT waive any original effective-role/MCP/owner-auth/hold-generation/provider/recovery behavior.

ID: REQ-endpoint-custody-holds-002
Source: original writer/table clauses; RFC0006/schema isolation and security.md Trust Premise; [Observed] route.execute/current owner_auth boundary; D1/D2 and preserving E-A4 with exact proposed P1 interface delta
Scope: v1-mandatory

#### Scenario: Peer DML does not forge custody

- **WHEN** a General/Finance runtime role attempts hold/release/delete after bootstrap replay
- **THEN** the lifecycle does not change; a legitimate admitted Switchboard/owner command has a positive durable result

#### Scenario: Caller strings do not authenticate

- **WHEN** a registered generic TCP call supplies a trusted endpoint name, source entity or security-answer DTO
- **THEN** it produces no hold, release, question decision, privileged receipt or provider attempt

#### Scenario: Cohost peer admission is separate

- **WHEN** a source-scoped local companion accepts a genuine cohosted daemon PID
- **THEN** it still requires independently source-owned command/row/target/operation/generation eligibility; a child or unsupported independent process is refused
- **AND** this local cohost-only negative never excludes a genuine supported independent process from the complete P-CUSTODY-1 positive

#### Scenario: Dashboard and host remain different

- **WHEN** a browser submits a valid current session or the host performs an explicit control act
- **THEN** only the admitted operation executes; ordinary API-role SQL cannot regain host or unrestricted hold authority

#### Scenario: Supported independent processes participate with real source authority

- **WHEN** independently running supported daemons perform an eligible custody-bound operation through the adopted process-principal/source protocol
- **THEN** the genuine current-principal positive is admitted and a forged/foreign/old-incarnation call is refused; absent protocol remains UNDELIVERED and cannot count as whole-fleet success
- **AND** actual constructor-fixed source-owning adapter/startup/backend/writer/source/registered-guard positives cover independently launched source/target/API/connectors, run/up and actual ordinary Compose/Kubernetes entrypoints; forged DTO/arbitrary nonce/unbound effective-role/current-source negatives accompany them without a kernel-isolation claim

#### Scenario: Host enrollment is separate from role and begin challenge

- **WHEN** a same-role child or restricted API login creates an anchor-begin proposal or copies a host/role/PID/application name
- **THEN** it gains no enrolled source or mint scope; the genuine independently launched host-owned process enrolls through the exact protected host interface and actual live backend incarnation

#### Scenario: Backend restart cannot revive an old grant

- **WHEN** an enrolled source disconnects and a new backend/PID or process incarnation starts
- **THEN** new source-owned host enrollment and challenge are required; old call/connection/source contexts refuse and a fresh enrolled positive works

#### Scenario: Registered online challenge binds the actual source and destination

- **WHEN** separate Switchboard and Messenger processes execute a real admitted source call
- **THEN** the source-owned canonical row/mint and online challenge through current private connections precede the actual registered function; altered source/body/target set/version/generation/audience/operation or public DTO refuses before privileged instrumentation

### Requirement: Callback and approved execution fences

The current core_233 owner definer and all owner-only consumers SHALL exclude active holds without weakening malformed/inactive/merged/deleted/collision or channel-normalization rules. Telegram approval callbacks from a held identity SHALL be acknowledgement-only with no approve/reject mutation. Already queued/approved execution SHALL recheck source and egress eligibility at its actual executor/provider admission boundary; a standing rule or older approval cannot bypass an active custody hold. Current primary-only callback policy and uniform primary/secondary outbound bypass remain otherwise unchanged. A provider-start operation admitted before the hold commit SHALL retain its actual confirmed/ambiguous result; the hold cannot retroactively undo it or assert no send.

ID: REQ-endpoint-custody-holds-006
Source: original DB-definer/signed-callback refusal acceptance; RFC0017 and RFC0021 One-tap decision; module-approvals Defense-in-Depth; REQ-approval-delivery-intent-recovery-004/-009/-011
Scope: v1-mandatory

#### Scenario: Held callback does not reach decision

- **WHEN** an otherwise valid signed callback comes from a held primary identity
- **THEN** it is acknowledged without decision-route invocation; a genuine unheld callback still works

#### Scenario: Prior approval does not reopen custody

- **WHEN** an approved held-account send reaches the provider admission boundary
- **THEN** no new provider start occurs until a separately eligible future admission

#### Scenario: Provider already started

- **WHEN** a hold commits after an operation crossed its fenced provider-start marker
- **THEN** the operation remains confirmed or ambiguous according to its real outcome and is never described as cancelled before send

