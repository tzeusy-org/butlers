# Kubernetes Restore Drill (proposed)

PROPOSED, NOT ADOPTED. Requirements below are candidate behavior only; D1-D11 and
status.md retain the exact adoption and operational gates. No baseline spec is modified.

## ADDED Requirements

### Requirement: Proposal authority and continuing current topology

This proposed capability SHALL remain unadopted until exact independent review and owner acceptance, and SHALL preserve chart exclusion and the current Compose-only executor until implementation and operational prerequisites are proven.

ID: REQ-kubernetes-restore-drill-001
Source: bu-viat6h.4; this change design.md D1-D11; REQ-deployment-hardening-007; REQ-database-security-006
Scope: v1-mandatory

#### Scenario: Source publication does not activate a port

- **WHEN** this change is published or strictly validated
- **THEN** it remains a proposal, with no Kubernetes code, chart, privilege, credential or operational activation

#### Scenario: Current execution remains available

- **WHEN** the proposal has not been exactly accepted and verified
- **THEN** the existing Compose --with-restore-drill path and protected mechanisms remain unchanged

#### Scenario: Held replacement remains held

- **WHEN** an ephemeral-Postgres or public-audit replacement is suggested
- **THEN** the held .14 owner gate and all survivor requirements remain intact and no replacement is inferred

### Requirement: Authenticated created-generation identity

A future Kubernetes executor SHALL authenticate exact namespace, controller, Job, Pod, container-restart, node and boot generations using reviewed proof authority, and SHALL reject caller assertions or reused generation evidence before credential use.

ID: REQ-kubernetes-restore-drill-002
Source: bu-viat6h.4; this change design.md D1-D11; REQ-deployment-hardening-007; REQ-database-security-006
Scope: v1-mandatory

#### Scenario: Pod restart requires new proof

- **WHEN** a container restarts within the same Pod UID
- **THEN** old evidence is refused and a fresh authenticated generation must be fenced before credential release

#### Scenario: Names cannot substitute for identity

- **WHEN** a Pod or namespace name is reused with a new UID
- **THEN** the old binding is rejected before secret use or scratch work

#### Scenario: Boot source is explicit

- **WHEN** node boot identity is resolved
- **THEN** Downward API is not claimed to supply it and node-reported metadata is distinguished from actual authenticated kernel proof

#### Scenario: Forged evidence is refused

- **WHEN** labels, environment, copied markers or caller verification flags claim authority
- **THEN** none can mint or substitute a capability and unsupported identity stays fail-closed

### Requirement: Applied network boundary precedes recovery authority

A future executor SHALL hold both actual created executor and relay workload containers stopped through a trusted runtime/CNI start barrier until both generation-bound peer, endpoint, own-node and host-route fences are applied, SHALL separately validate recovery authority before credential use, or an exact owner-adopted named weakening SHALL govern each missing guarantee.

ID: REQ-kubernetes-restore-drill-003
Source: bu-viat6h.4; this change design.md D1-D11; REQ-deployment-hardening-007; REQ-database-security-006
Scope: v1-mandatory

#### Scenario: Unapplied policy is insufficient

- **WHEN** a NetworkPolicy resource exists without demonstrated applied dataplane
- **THEN** credential release is refused under P1 and W-start remains an explicit unadopted loss

#### Scenario: Own-node paths have an explicit disposition

- **WHEN** generic policy admits node or host routes
- **THEN** equivalence is not claimed and W-host requires exact acceptance or an actual reviewed node fence

#### Scenario: Policy union cannot reopen a route

- **WHEN** another policy or service rewriting permits a forbidden destination
- **THEN** activation fails its exact boundary proof rather than counting the restrictive template alone

#### Scenario: Established flow is fenced on revocation

- **WHEN** generation or enforcement changes during an active relay session
- **THEN** new admission stops and owned flow absence is proven before recovery or replacement

#### Scenario: Both created workloads are held before the joint fence

- **WHEN** P1 creates the exact executor and relay containers on the selected node
- **THEN** the trusted runtime/CNI gate holds both in actual Created/not-started states and admits neither workload entrypoint until it verifies both installed generation-bound fences

#### Scenario: Missing or failed pre-start prerequisite refuses both services

- **WHEN** the handler, peer, placement, stopped-state witness or applied fence is missing, uncertain or exceeds the proposed 30-second wait
- **THEN** neither service starts or receives materialized recovery authority, owned creation is cleaned up within its bounds and successor admission stays closed until absence is proven

#### Scenario: Start replay or restart cannot bypass the barrier

- **WHEN** a consumed admission is replayed or the helper, kubelet, node or container generation restarts
- **THEN** starts remain denied until fresh creation and both-fence proof, without marker reuse or a fail-open timeout

#### Scenario: Partial start is fenced and recovered

- **WHEN** one authorized start succeeds and the other fails
- **THEN** admission closes, the applied fences remain required, exposure is revoked and owned process, relay-flow and SQL absence plus scratch recovery precede any replacement

#### Scenario: Delayed password is not a startup equivalent

- **WHEN** an alternative permits a password-free executor or uncredentialed relay to run before its actual fence
- **THEN** it is the explicit unadopted W-start loss requiring exact owner acceptance, and cannot be reported as P1 equivalence

### Requirement: Credential and protected result isolation survives the port

A future port SHALL retain the purpose-bound executor-only recovery credential, ordinary NOCREATEDB roles, trusted bootstrap provenance, exclusive private result authority and constrained atomic audit projection.

ID: REQ-kubernetes-restore-drill-004
Source: bu-viat6h.4; this change design.md D1-D11; REQ-deployment-hardening-007; REQ-database-security-006
Scope: v1-mandatory

#### Scenario: Ordinary caller cannot gain recovery authority

- **WHEN** a dashboard, butler, connector or shared migration credential requests scratch creation
- **THEN** creation is refused without SET ROLE escape, shared secret fallback or grant widening

#### Scenario: New materializer authority is explicit

- **WHEN** P1 requires late secret availability
- **THEN** its custody and privilege delta stays unadopted until exact review and owner acceptance

#### Scenario: Bootstrap staging cannot be blessed

- **WHEN** an ordinary caller precreates compatible protected objects or no-op installers
- **THEN** trusted bootstrap provenance refuses the state before ownership or interface handoff

#### Scenario: Audit cannot manufacture restore truth

- **WHEN** a public audit row is forged or its projection trigger rejects
- **THEN** it cannot create private pass or due state and rejection rolls back the authoritative result transaction

#### Scenario: Private credential remains private

- **WHEN** executor configuration or evidence is produced
- **THEN** only the executor runtime receives the private file and no credential value reaches relay, shared environment, logs, argv, proof or API

### Requirement: Relay and transport identity retain exact scope

A future executor SHALL use a distinct uncredentialed relay with exact canonical endpoint and port, preserve hostname TLS identity and CA validation, and SHALL refuse unsupported address-family or translation paths.

ID: REQ-kubernetes-restore-drill-005
Source: bu-viat6h.4; this change design.md D1-D11; REQ-deployment-hardening-007; REQ-database-security-006
Scope: v1-mandatory

#### Scenario: Same pod relay is insufficient

- **WHEN** executor and relay share one network namespace
- **THEN** the design is rejected as equivalent to the current separate credentialed and uncredentialed boundaries

#### Scenario: Endpoint spelling is not reinterpreted

- **WHEN** an endpoint or port is noncanonical, numeric as TLS identity, whitespace-bearing or in a forbidden route class
- **THEN** it is refused before secret use or DNS reinterpretation

#### Scenario: TLS identity remains the relay hostname

- **WHEN** verify-ca or verify-full is selected
- **THEN** the dedicated valid CA root and hostname identity remain required with no direct IPv4 executor escape

#### Scenario: Peer or family change revokes proof

- **WHEN** DNS, relay generation, address family or service translation changes
- **THEN** the old capability is invalidated and unsupported paths cannot be silently allowed

#### Scenario: Relay overload remains bounded

- **WHEN** a relay is saturated or a connection stalls
- **THEN** admission closes before upstream dial and finite connect, idle, total and cancellation-safe close bounds remain enforced

### Requirement: Scratch lifecycle stays fixed and bounded

A future executor SHALL perform bounded scratch-only precleanup, creation, restore, verification and postcleanup, SHALL preserve stated backup coverage, and SHALL classify cleanup failure as failure.

ID: REQ-kubernetes-restore-drill-006
Source: bu-viat6h.4; this change design.md D1-D11; REQ-deployment-hardening-007; REQ-database-security-006
Scope: v1-mandatory

#### Scenario: Live target is never used

- **WHEN** a drill enters any data-changing lifecycle stage
- **THEN** only the fixed scratch target is mutated and the live database remains untouched

#### Scenario: Every stage has an actual bound

- **WHEN** a client call, child process or finalizer stalls
- **THEN** the proposed application and outer watchdogs produce a named non-pass with reserved owned cleanup instead of an unbounded wait

#### Scenario: Cleanup failure cannot pass

- **WHEN** restore verification succeeds but scratch cleanup fails
- **THEN** the terminal attempt is failed and scratch presence remains observable

#### Scenario: Absent backup is a no-result

- **WHEN** no usable backup exists
- **THEN** no artificial passed or failed result advances the cadence

#### Scenario: Backup scope cannot silently shrink

- **WHEN** a coverage omission or artifact content/identity changes
- **THEN** the existing coverage owner and verification contract remain required rather than certifying a narrower restore

#### Scenario: Available backup follows the complete scratch sequence

- **WHEN** an exclusively admitted drill has a usable backup and authorized recovery prerequisites
- **THEN** it performs bounded scratch precleanup, create, real restore, non-system-data verification and postcleanup, using the maintenance connection only for scratch create/drop and reporting cleanup failure as non-pass

### Requirement: Exclusive lifecycle ownership covers every caller

A future Kubernetes path SHALL admit at most one fixed-scratch lifecycle across scheduled, manual, retry, release and surviving Compose callers, and SHALL not infer old-writer absence from Job settings or lease expiry.

ID: REQ-kubernetes-restore-drill-007
Source: bu-viat6h.4; this change design.md D1-D11; REQ-deployment-hardening-007; REQ-database-security-006
Scope: v1-mandatory

#### Scenario: Manual and scheduled triggers cannot overlap

- **WHEN** two admitted callers target the same scratch scope
- **THEN** one exclusive lifecycle owner wins before cleanup or creation and the other performs no scratch work

#### Scenario: Guard loss does not authorize a successor

- **WHEN** the guard connection or lease is lost while another SQL connection may run
- **THEN** new admission stays closed until old process, relay flow and database connection absence is proven

#### Scenario: Duplicate terminal delivery is reconciled

- **WHEN** a trigger or result retry follows a possible or committed terminal write
- **THEN** protected separate readback reconciles the same authenticated attempt without duplicate scratch execution or fabricated lineage

#### Scenario: Orphan recovery requires proof

- **WHEN** an old Job or relay remains after a crash
- **THEN** owned termination and exclusive scratch cleanup precede any fresh generation, without time-only admission

### Requirement: Outcome truth and privacy survive failures

A future port SHALL keep protected failed, passed, missing, recovered and persistence-unknown truth and existing due, API and attention ownership, and SHALL retain content-blind diagnostics.

ID: REQ-kubernetes-restore-drill-008
Source: bu-viat6h.4; this change design.md D1-D11; REQ-deployment-hardening-007; REQ-database-security-006
Scope: v1-mandatory

#### Scenario: Persistence failure is not a pass

- **WHEN** protected result finalization fails or its commit is uncertain
- **THEN** the attempt stays failed or unknown as applicable until protected readback, never a Job-success or public-audit substitute

#### Scenario: Failure age and retry stay with their owner

- **WHEN** a failed drill is retried or later recovers
- **THEN** the existing recovery/API/attention contract is preserved without hidden cadence changes or duplicate notifications

#### Scenario: Sensitive evidence is refused

- **WHEN** proof or failure telemetry contains credentials, backup content or free-form sensitive fields
- **THEN** those fields are excluded from durable/API evidence and current writer sanitization remains authoritative

#### Scenario: Result-aware cadence remains a protected target

- **WHEN** a future port consumes no result, a pass or a failure from the protected authority
- **THEN** its due target remains immediate, seven days or 24 hours respectively under the existing recovery owner, and a source gap or legacy weekly-failure clause cannot certify completion or authorize public-audit timing

### Requirement: Rollback retains protected recovery and history

A future rollback SHALL quiesce only its owned path, preserve current Compose recovery, backup artifacts and protected result history, and SHALL not restore live data or widen ordinary authority.

ID: REQ-kubernetes-restore-drill-009
Source: bu-viat6h.4; this change design.md D1-D11; REQ-deployment-hardening-007; REQ-database-security-006
Scope: v1-mandatory

#### Scenario: Active rollback is quiesced

- **WHEN** rollback begins while an attempt is active
- **THEN** admission stops and owned termination plus scratch cleanup is proven before a replacement path can run

#### Scenario: History survives rollback

- **WHEN** the proposed Kubernetes representation or implementation is reverted
- **THEN** private result evidence, failures and backups remain and no destructive downgrade or audit-authority replacement is hidden

#### Scenario: Privilege repair stays managed

- **WHEN** a recovery privilege prerequisite is absent
- **THEN** the existing managed-bootstrap owner remains required without ad hoc ALTER ROLE, shared CREATEDB or manual scratch precreation

### Requirement: Exact review and actual evidence precede adoption and activation

The proposal SHALL bind complete source and scenario coverage to independent resulting-source review, exact owner adoption and distinct future PostgreSQL and authorized Kubernetes evidence before activation.

ID: REQ-kubernetes-restore-drill-010
Source: bu-viat6h.4; this change design.md D1-D11; REQ-deployment-hardening-007; REQ-database-security-006
Scope: v1-mandatory

#### Scenario: Review names the resulting source

- **WHEN** the candidate is offered for owner acceptance
- **THEN** independent review names the exact SHA and tree, complete obligations and individually selected authority or weakening deltas

#### Scenario: Changed source cannot inherit acceptance

- **WHEN** a reviewed or adopted head changes
- **THEN** the new exact source needs appropriate renewed review and acceptance rather than inheriting an old artifact receipt

#### Scenario: Source checks do not prove runtime equivalence

- **WHEN** strict validation, guards or a draft PR passes
- **THEN** the report limits those results to source and leaves actual PG, CNI, node-route, secret and deployment evidence unearned

#### Scenario: Production and foreign owners stay separate

- **WHEN** a future port or source handoff is proposed
- **THEN** production UNKNOWN, r6wzr Option C, .5/.6 gaps and restore, secrets, privilege, tracker and held retirement owners remain unchanged

#### Scenario: Foreign attention clauses retain their own authority

- **WHEN** coverage includes attention source availability, healthy-source summary or list/filter clauses
- **THEN** each retains its exact REQ-core-notify-008 scenario and actual reader-owner disposition rather than an unrelated relay or proposal-authority scenario

#### Scenario: Inherited conflicts remain explicit without foreign amendment

- **WHEN** a retained clause conflicts over public result authority, weekly failed retry or unconditional cleanup success
- **THEN** the literal and hash remain intact with its exact governing conflict and existing owner-resolution gate, without treating both claims as simultaneous mandatory equivalents or adopting a weakening

#### Scenario: Carrier and reference checks have limited verdicts

- **WHEN** literal/hash population and exact source/candidate reference checks pass
- **THEN** those receipts establish carrier integrity and address completeness only, while independent resulting-source semantic review and all actual behavior proof remain separate pending gates
