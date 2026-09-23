## ADDED Requirements

### Requirement: [TARGET-STATE] Receiver-derived daemon observation
The control plane SHALL enumerate exact expected daemon identities and endpoints from Git-owned roster configuration, probe each on a bounded cadence, and record only receiver-timed observations for the currently registered boot epoch. On every daemon boot, a narrow database registration SHALL allocate a monotonically increasing, durable boot epoch before the daemon advertises readiness. The daemon response SHALL include its exact name, boot UUID and allocated epoch, route contract range, and whether it currently accepts `route.execute` work. Arbitrary caller-provided addresses and daemon-authored timestamps SHALL confer no liveness authority.

ID: REQ-butler-control-plane-liveness-001
Source: heart-and-soul/vision.md Rules 4-5; RFC 0001 startup and RFC 0008 network boundary; docs/reviews/2026-09-23-liveness-control-plane-reliability-packet.md §5.1
Scope: v1-mandatory

#### Scenario: Expected ready daemon advances observation
- **WHEN** the observer reaches the configured endpoint and receives a valid matching identity and acceptance response within its deadline
- **THEN** it records a fresh healthy observation with the database server's time only when the returned epoch equals the latest registered epoch
- **AND** a later complete observation can establish continued freshness without a daemon POST to the dashboard API

#### Scenario: Successor registration fences an older boot before probes race
- **WHEN** a newer boot registers for the same Git-roster daemon while an earlier process or probe remains alive
- **THEN** the database allocates a strictly higher durable epoch and returns it to the successor for its internal identity response
- **AND** an old-epoch response cannot renew liveness even if that probe holds a higher observer sequence or arrives after the successor's response

#### Scenario: Registration failure or rollback cannot revive an older process
- **WHEN** boot registration fails, restarts after an interrupted transaction, or code rollback lacks the current epoch contract
- **THEN** the daemon does not advertise route acceptance until it has a committed current epoch
- **AND** rollback preserves the latest epoch and rejects older or absent epochs rather than making a predecessor routable

#### Scenario: Untrusted identity fails closed
- **WHEN** a response names the wrong daemon or generation, has an incompatible route contract, includes any daemon-authored timestamp or other unexpected field, is malformed, or comes from a different endpoint
- **THEN** it cannot advance healthy liveness or route compatibility
- **AND** the mismatch remains distinguishable in content-blind diagnostic evidence

#### Scenario: Observer failure cannot clear an incident
- **WHEN** an observer pass fails, times out, or covers only part of the expected fleet
- **THEN** missing healthy responses become unavailable or observer-unknown according to the last complete observation and freshness boundary
- **AND** the pass cannot resolve an active fleet condition or preserve freshness indefinitely

### Requirement: [TARGET-STATE] Separate routability dimensions
The registry SHALL retain observed health (`healthy`, `stale`, `unavailable`, `observer_unknown`), administrative policy (`active`, `paused`, `quarantined`, `review_required`), and route compatibility as separate dimensions. Routability SHALL require a fresh observation matching the latest durable boot epoch, an accepting compatible daemon, and active administrative policy; observation alone SHALL never revoke policy.

ID: REQ-butler-control-plane-liveness-002
Source: heart-and-soul/vision.md Rules 4-5; RFC 0003 routing; docs/reviews/2026-09-23-liveness-control-plane-reliability-packet.md §4 Owner-set and liveness-derived quarantine
Scope: v1-mandatory

#### Scenario: Owner quarantine survives every automatic writer
- **WHEN** the owner quarantines a daemon and a later probe, registration, route success, or restart reports health
- **THEN** administrative policy remains quarantined and the daemon remains ineligible
- **AND** only an explicit authorized owner action may restore active policy

#### Scenario: Fresh but incompatible daemon remains ineligible
- **WHEN** a daemon is healthy but its route contract or capability set does not satisfy the target route
- **THEN** the target remains ineligible with a compatibility reason
- **AND** its observed health remains healthy rather than being rewritten as unavailable

#### Scenario: Policy and observation are independently visible
- **WHEN** the owner inspects a fleet row during pause, quarantine, stale observation, or observer failure
- **THEN** the read model exposes the separate observation, policy, compatibility, and effective eligibility with content-blind reasons
- **AND** it does not present a derived stale state as an owner quarantine

### Requirement: [TARGET-STATE] Provenance-safe quarantine migration
Before separated policy becomes authoritative, the system SHALL classify each existing quarantine by recorded provenance. Proven TTL-derived quarantine SHALL become observation state, proven operator quarantine SHALL become administrative policy, and ambiguous provenance SHALL remain ineligible with `review_required` until an explicit owner decision. Migration and rollback SHALL preserve the original evidence and never silently activate an uncertain target.

ID: REQ-butler-control-plane-liveness-003
Source: heart-and-soul/vision.md Rule 5; docs/reviews/2026-09-23-liveness-control-plane-reliability-packet.md §5.1 and §6 Phase 1
Scope: v1-mandatory

#### Scenario: Historical automatic quarantine is not made owner policy
- **WHEN** a legacy row has sufficient evidence that TTL expiry alone caused quarantine
- **THEN** migration retains the expiry evidence under observed health and does not invent an owner policy action

#### Scenario: Ambiguous legacy quarantine remains closed
- **WHEN** a legacy row cannot be attributed confidently to automatic expiry or an operator action
- **THEN** it becomes `review_required` and remains unroutable
- **AND** its original metadata is retained for a content-blind owner review

#### Scenario: Repeated migration is stable
- **WHEN** the migration or rollback procedure is rerun after interruption
- **THEN** it does not clear policy, duplicate provenance records, or turn an ambiguous target active

### Requirement: [TARGET-STATE] Bounded stale-route recheck
Before rejecting an otherwise eligible stale target, Switchboard SHALL make at most one bounded receiver-derived probe of that target. A valid current-generation response SHALL refresh observation and allow the route; a failed probe SHALL return the existing typed `not_attempted` transport outcome with a transient target-unavailable reason and SHALL NOT call the target.

ID: REQ-butler-control-plane-liveness-004
Source: RFC 0003 routing; docs/reviews/2026-09-23-liveness-control-plane-reliability-packet.md §5.1; [Observed] roster/switchboard/tools/routing/transport.py
Scope: v1-mandatory

#### Scenario: Stale healthy daemon recovers without restart
- **WHEN** an otherwise eligible target is stale but its exact configured endpoint returns a valid ready current-generation response within the recheck deadline
- **THEN** Switchboard refreshes receiver-timed observation and attempts the original route once

#### Scenario: No-effect refusal is typed
- **WHEN** the bounded probe cannot prove current acceptance readiness
- **THEN** Switchboard does not call `route.execute` and reports canonical `outcome=not_attempted` with retryability determined by the transient failure
- **AND** it does not label the target quarantined or claim delivery succeeded

#### Scenario: Administrative policy cannot be bypassed
- **WHEN** a target is paused, quarantined, under review, or incompatible
- **THEN** no stale-path probe restores route eligibility or overrides that reason

### Requirement: [TARGET-STATE] Independent fleet condition producer
A separately supervised control-plane controller SHALL observe fleet status and QA patrol age without depending on QA's own scheduler or a routable QA registry row. It SHALL create one stable fleet-level condition for one or more affected expected daemons, attach per-daemon impact evidence, and resolve only after a complete healthy snapshot under the existing infrastructure-condition lifecycle. At cutover, redundant per-butler liveness conditions SHALL retain their impact evidence and remain linked but shall not independently page or start duplicate QA investigations; recovery resolution still requires a complete healthy observation of each affected daemon.

ID: REQ-butler-control-plane-liveness-005
Source: heart-and-soul/vision.md:51-53,120-137; RFC 0015 patrol; openspec/changes/define-infrastructure-reliability-lifecycle/specs/infrastructure-reliability/spec.md §Episode lifecycle
Scope: v1-mandatory

#### Scenario: Common control failure yields one episode
- **WHEN** one observer failure or shared control-plane fault makes multiple expected daemons stale
- **THEN** one fleet condition records the common source and per-daemon affected set
- **AND** QA does not open one independent investigation per daemon for the same episode

#### Scenario: Missing QA patrol is observed independently
- **WHEN** no complete QA patrol is recorded within twice its configured cadence
- **THEN** the controller records an overdue QA patrol condition without requiring a QA patrol to run

#### Scenario: Partial recovery retains active condition
- **WHEN** only some expected daemons recover or a controller snapshot is incomplete
- **THEN** the active fleet condition remains open with updated impact evidence
- **AND** no missing member is inferred healthy

#### Scenario: Legacy per-butler condition remains truthful through cutover
- **WHEN** one or more active per-butler heartbeat-stale conditions predate the fleet-condition cutover
- **THEN** the controller links their impact to the fleet condition and stops duplicate per-butler paging or investigation
- **AND** it does not mark any predecessor recovered until a complete observation proves that specific daemon healthy

### Requirement: [TARGET-STATE] Heartbeat POST retirement
After receiver-derived observation is authoritative, the dashboard SHALL reject or remove the old daemon `POST /api/switchboard/heartbeat` mutation and daemons SHALL cease sending it. It SHALL never become anonymous, accept the dashboard owner key from a daemon, or reuse unrelated approval or runtime-probe credentials.

ID: REQ-butler-control-plane-liveness-006
Source: heart-and-soul/security.md:161-195; openspec/changes/specify-host-authorized-dashboard-enrollment/specs/dashboard-owner-auth/spec.md §Central owner boundary; docs/reviews/2026-09-23-liveness-control-plane-reliability-packet.md §4
Scope: v1-mandatory

#### Scenario: Legacy writer has no liveness authority
- **WHEN** a daemon or other caller invokes the legacy heartbeat POST after cutover
- **THEN** the request cannot update fleet observation, administrative policy, or route compatibility

#### Scenario: Owner auth remains fail closed
- **WHEN** an unauthenticated caller targets protected dashboard APIs
- **THEN** the central owner-auth boundary continues to deny protected access
- **AND** no blanket daemon or heartbeat path exemption is introduced

### Requirement: [TARGET-STATE] Durable independent owner attention for fleet and QA failure
The independently supervised control-plane producer SHALL create one content-blind runtime-attention outbox episode for each active fleet-control or QA-patrol-overdue condition episode that reaches its first owner-attention threshold. With the database and controller available, that threshold and durable append SHALL occur within ten minutes of the first failing fleet observation or observed overdue QA patrol. A qualifying QA patrol SHALL have a completed successful status and evidence that every enabled discovery source completed; `running`, `error`, `skipped_overlap`, and synthetic `suppressed` rows SHALL NOT renew patrol freshness. The append SHALL use a fixed server-derived condition identity and narrowly authorized producer operation; no QA patrol, routable QA daemon, model session, or browser request is needed to create it. Switchboard SHALL deliver it through the existing fenced at-most-once runtime-attention worker. Later condition escalation SHALL remain bounded by the infrastructure-condition lifecycle and SHALL NOT duplicate the outbox episode or automatically resend an uncertain transport effect.

ID: REQ-butler-control-plane-liveness-007
Source: heart-and-soul/vision.md:51-53,120-137; openspec/changes/define-infrastructure-reliability-lifecycle/specs/infrastructure-reliability/spec.md §Bounded lifecycle escalation; openspec/changes/harden-runtime-auth-and-breaker-attention/specs/runtime-attention-outbox/spec.md §Switchboard-Owned At-Most-Once Attention Delivery; docs/reviews/2026-09-23-liveness-control-plane-reliability-packet.md §5.2 and §7
Scope: v1-mandatory

#### Scenario: Correlated fleet expiry produces one owner alert episode
- **WHEN** a fleet-control condition stays active through its producer-owned initial attention grace
- **THEN** a fixed producer appends exactly one pending outbox episode keyed to that condition episode
- **AND** the safe payload carries only the fixed fleet-failure classification and no daemon names, endpoints, event content, credentials, or raw errors

#### Scenario: Missing QA patrol alerts without QA running
- **WHEN** the independently observed QA-patrol-overdue condition stays active through its initial attention grace
- **THEN** the control-plane producer appends one pending outbox episode for that condition even if the QA scheduler or registry is stale
- **AND** repeated controller scans or concurrent producers return the same episode rather than creating more messages

#### Scenario: Incomplete or synthetic patrol does not hide overdue QA
- **WHEN** the newest patrol is `running`, `error`, `skipped_overlap`, synthetic `suppressed`, or reports a failed or missing enabled discovery source
- **THEN** it does not advance the last qualifying QA patrol time or resolve an overdue condition
- **AND** only a completed `clean` or `findings_dispatched` patrol with all enabled sources successful can renew that time

#### Scenario: Interrupted append is repaired without a duplicate
- **WHEN** a condition's first attention transition commits but its outbox episode is absent after a controller or database interruption
- **THEN** the supervised controller retries the fixed idempotent append within the bounded attention window
- **AND** the unique condition-episode key prevents a second episode after a concurrent or recovered retry

#### Scenario: Delivery outcome remains truthful
- **WHEN** Switchboard claims a fleet or QA attention episode and external transport is confirmed, definitively rejected, or uncertain
- **THEN** the existing fenced outbox records `sent`, `failed`, or `uncertain` respectively and exposes that status on the owner-facing condition
- **AND** `sending` or `uncertain` is never presented as delivered, and neither an uncertain claim nor a dashboard refresh triggers an automatic resend

#### Scenario: Delivery worker is unavailable while condition persists
- **WHEN** Switchboard cannot claim or deliver the pending episode because its worker, lease, or target is unavailable
- **THEN** the condition and pending or failed attention status remain durable and visibly unavailable to the owner
- **AND** proven pre-transport failures alone may follow bounded outbox backoff without minting another episode

#### Scenario: Bounded escalation does not multiply pages
- **WHEN** the fleet or QA condition continues through L2, L3, or L3 repeat due times
- **THEN** the condition ledger advances its bounded escalation and retains the same linked attention episode and its delivery truth
- **AND** it does not emit one alert per affected daemon, create an automatic successor, or provision an external monitor

#### Scenario: Outbox retention cannot re-page an active condition
- **WHEN** an attention outbox row ages out while its linked fleet or QA condition remains active
- **THEN** durable condition evidence retains the emitted episode identity and last verified delivery category
- **AND** reconciliation does not treat the retained condition as an unpaged new episode

### Requirement: [TARGET-STATE] Shared bounded probe authority across processes
The Dashboard periodic observer and Switchboard stale-route recovery SHALL use the same exact-roster response verifier and database-reserved per-daemon probe sequence. Dashboard SHALL own the periodic pass; Switchboard SHALL initiate exactly one on-demand probe before refusing an otherwise eligible stale route using its own narrowly authorized control-plane observation operation. Both writers SHALL update the same receiver-derived registry record conditionally on the latest durable boot epoch and probe sequence, within a bounded deadline. Neither writer SHALL accept caller-supplied endpoints or gain dashboard owner-auth authority.

ID: REQ-butler-control-plane-liveness-008
Source: heart-and-soul/vision.md Rules 3-5; RFC 0003 routing; docs/reviews/2026-09-23-liveness-control-plane-reliability-packet.md §5.1
Scope: v1-mandatory

#### Scenario: Switchboard rechecks a stale target from its own process
- **WHEN** Switchboard encounters an otherwise eligible stale route target
- **THEN** it reserves a sequence in the database, probes that target's exact Git-roster endpoint with the shared bounded verifier, and conditionally records its receiver-timed result
- **AND** it does not call the dashboard API, wait for its periodic loop, or present an owner session or key

#### Scenario: Periodic and on-demand probes race
- **WHEN** Dashboard and Switchboard probe the same target concurrently
- **THEN** only a result matching the latest registered boot epoch and a still-current reserved sequence may advance observation
- **AND** an older result cannot overwrite a newer one; a current failed result marks health unavailable without changing policy

#### Scenario: Probe operation cannot become broad registry write authority
- **WHEN** either process requests a probe sequence or records a result
- **THEN** its effective database role is limited to fixed reserve-and-record operations for Git-roster identities and cannot directly update administrative policy or invent an endpoint
- **AND** failed verification, DB refusal, or deadline expiry leaves the target unavailable with a typed no-attempt route result
