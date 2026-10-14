## MODIFIED Requirements

### Requirement: Cross-Container MCP Endpoint Resolution

Switchboard's outbound MCP calls to registered agents (brokered capability forwarding, notification `deliver()` and the service-plane relay) and every other caller that runs outside `butlers-up` SHALL resolve a target agent's registered MCP endpoint to a host reachable from the CALLER's own container, not only from `butlers-up` (the container every domain daemon self-registers itself into via `http://localhost:<port>`). Switchboard itself runs in its own container, `butlers-control` (REQ-butler-switchboard-006). When the `BUTLERS_HOST` environment variable is set to a value other than `localhost` (as on `butlers-control`, and on the `dashboard-api` / `dashboard-api-hotreload` containers, which host processes such as `butlers.jobs.secrets_lifecycle` that are NOT part of `butlers-up`), an exact `localhost` host in the resolved endpoint SHALL be rewritten to that value before the MCP connection is attempted. When `BUTLERS_HOST` is unset or equals `localhost`, resolution SHALL be unchanged (a no-op).

Source: RFC 0038 D1 and D7
Scope: v1-mandatory

#### Scenario: In-container caller reaches the registered endpoint unchanged
- **WHEN** a process running inside `butlers-up` resolves a sibling's registered endpoint
- **AND** `BUTLERS_HOST` is unset
- **THEN** the MCP connection targets the sibling's self-registered `http://localhost:<port>/mcp` endpoint unchanged

#### Scenario: Cross-container caller is rewritten to the container-DNS host
- **WHEN** Switchboard in `butlers-control` forwards a capability call, delivers a notification or relays a service-plane call to a domain daemon, or a process in the `dashboard-api` container (for example `secrets_lifecycle`'s scheduled scan) delivers a notification, with `BUTLERS_HOST=butlers-up` set on that container
- **THEN** the MCP connection targets `http://butlers-up:<port>/mcp`, not `http://localhost:<port>/mcp`
- **AND** the call SHALL succeed rather than failing to connect

### Requirement: Wellness Source Channel and Google Health Provider Registration

The Switchboard SHALL accept `wellness/google_health` as a valid ingestion source.

Source: RFC 0038 D1, D2 and D7
Scope: v1-mandatory

#### Scenario: SourceChannel registration

- **WHEN** the `SourceChannel` literal type is defined in `roster/switchboard/tools/routing/contracts.py`
- **THEN** it SHALL include `"wellness"` as a valid channel value
- **AND** `"wellness"` SHALL be the canonical channel for any wearable / health-device integration, regardless of device brand or API surface

#### Scenario: SourceProvider registration

- **WHEN** the `SourceProvider` literal type is defined
- **THEN** it SHALL include `"google_health"` as a valid provider value

#### Scenario: Channel-provider pair validation

- **WHEN** an ingest envelope arrives with `source.channel = "wellness"`
- **THEN** the Switchboard SHALL validate that `source.provider` is `"google_health"` OR `"home_assistant"` (the allowed providers for this channel)
- **AND** the pair SHALL be registered in `_ALLOWED_PROVIDERS_BY_CHANNEL` as `{"wellness": frozenset({"google_health", "home_assistant"})}`
- **AND** envelopes with `source.channel = "wellness"` and any other provider SHALL be rejected with a validation error

#### Scenario: Home Assistant wellness envelope accepted

- **WHEN** an ingest.v1 envelope arrives with `source.channel = "wellness"` and `source.provider = "home_assistant"`
- **THEN** the envelope SHALL pass channel/provider validation
- **AND** SHALL be delivered to the Health butler's deterministic wellness ingest by the same wellness channel binding as `google_health` envelopes, not by an ingestion rule
- **AND** no goal SHALL be admitted and no reasoning session SHALL start for it

#### Scenario: Google Health wellness envelope unaffected

- **WHEN** an ingest.v1 envelope arrives with `source.channel = "wellness"` and `source.provider = "google_health"`
- **THEN** validation and Health ingest delivery SHALL be identical to the Home Assistant arm

#### Scenario: Unregistered provider still rejected

- **WHEN** an ingest.v1 envelope arrives with `source.channel = "wellness"` and a provider other than `"google_health"` or `"home_assistant"`
- **THEN** the envelope SHALL be rejected with the `invalid_source_provider` validation error

### Requirement: Dedicated Routing to Health Butler

The Switchboard SHALL deliver every accepted `wellness/google_health` envelope to the Health butler's deterministic `wellness_ingest_envelope` ingest through the service plane. The binding SHALL be by channel, not by an ingestion rule; the delivery SHALL NOT use `route.execute`, model classification, goal admission or a reasoning session, and the ingest tool SHALL remain non-LLM-presentable.

Source: RFC 0038 D1, D2 and D7
Scope: v1-mandatory

#### Scenario: Direct routing

- **WHEN** an accepted wellness envelope passes deduplication
- **THEN** the Switchboard SHALL deliver it to the Health butler's `wellness_ingest_envelope` service-plane ingest
- **AND** SHALL NOT deliver it to any other butler
- **AND** SHALL NOT admit a goal, start a reasoning session or call `route.execute` for it
- **AND** a historic `route_to:health` ingestion rule SHALL be neither required nor consulted for this delivery

## REMOVED Requirements

### Requirement: Domain Butler Registry

**Reason**: RFC 0038 D1 removes user-message classification and route dispatch with no successor, so the registry is no longer a classification candidate set and its lifestyle, fanout, disambiguation and misroute scenarios SHALL no longer hold.

**Migration**: The registry, staffer registration and notify delivery to Messenger survive in "Agent Registry for Capability Forwarding" (`REQ-butler-switchboard-005`). Owner and third-party messages become goals admitted directly from the envelope (`REQ-goal-orchestration-008`); a lead reaches a domain only through capabilities in its grant (`REQ-capability-authorization-004`).

### Requirement: Misroute Correction Re-dispatch

**Reason**: RFC 0038 D1 and the RFC 0003 disposition remove route dispatch; with no routing decision there is no misroute to correct and no `correct_route` re-dispatch.

**Migration**: None for re-dispatch. When a goal outcome is wrong, the owner starts a new goal or Stops the current one (`REQ-capability-authorization-011`); a lead that chose the wrong domain calls the right capability within the same goal (`REQ-goal-orchestration-001`).

### Requirement: Explicit Chronicler Routing Boundary

**Reason**: RFC 0038 D1 removes classification and route dispatch, so Switchboard no longer routes any request, retrospective or otherwise, to Chronicler.

**Migration**: A lead answers retrospective questions through Chronicler's read capabilities and guidance (`REQ-goal-orchestration-004`); passive events keep reaching Chronicler only through its projection jobs, which RFC 0038 D2 leaves unchanged.

### Requirement: Dashboard Chat-Widget Classification Lanes

**Reason**: RFC 0038 D1 removes Switchboard classification, `route_to_butler`, `route.execute` dispatch, sticky `routed_butler` pinning and the dashboard policy bypass; a dashboard turn is an owner message that admission turns directly into a goal.

**Migration**: Dashboard turns become owner-origin goals (`REQ-goal-orchestration-008`, `REQ-capability-authorization-003`) with honest goal outcomes (`REQ-goal-orchestration-006`). The owner-facing turn contract that the lanes carried (in-thread failure replies, bug-report filing, the read-only answer lane and replay-eligible dead letters) is deferred to the dashboard conversation design change.

## ADDED Requirements

### Requirement: Agent Registry for Capability Forwarding

Switchboard SHALL maintain a registry of every agent (butlers and staffers) with its name, type, endpoint and liveness, and SHALL use it only to assemble the capability catalog, forward brokered capability calls, deliver notifications and relay service-plane calls. The registry SHALL NOT be a candidate set for message classification or route dispatch, and registration SHALL grant no capability to any goal. Notification delivery SHALL reach Messenger's service-plane delivery endpoint (today `route.execute` on Messenger), which survives the deletion of `route.execute`'s session-dispatch role as a deterministic endpoint that is not LLM-presentable, not on the session listener, and called only by Switchboard `deliver()`.

ID: REQ-butler-switchboard-005
Source: RFC 0038 D1, D4 and D7
Scope: v1-mandatory

#### Scenario: Staffer registration includes type
- **WHEN** a staffer registers with the Switchboard at startup
- **THEN** the registration payload SHALL include `type = "staffer"`
- **AND** the Switchboard's registry SHALL store this type alongside the agent's name, endpoint and liveness state
- **AND** the eligibility sweep SHALL continue to track staffer liveness, because notification delivery and service-plane relays depend on it

#### Scenario: Notify delivery reaches Messenger
- **WHEN** a butler's `notify()` asks Switchboard to deliver a `notify.v1` envelope
- **THEN** Switchboard SHALL deliver it to Messenger's service-plane delivery endpoint (today `route.execute` on Messenger) resolved from the registry
- **AND** the delivery SHALL admit no goal and start no reasoning session

#### Scenario: Registered agents are capability owners, not routing targets
- **WHEN** an ingest envelope is admitted as a goal
- **THEN** Switchboard SHALL select no registered agent as a routing target
- **AND** the goal's lead SHALL reach a registered agent only through capabilities in its grant

### Requirement: Switchboard Runs as the Control Plane in Its Own Process

Switchboard SHALL run as the control plane in its own process and container, `butlers-control`, separate from the domain daemons in `butlers-up` and from the session host. It SHALL host ingress, goal admission, the goal ledger, the goal runner, the capability broker, and approval and insight brokerage, and SHALL NOT classify messages or dispatch routes. It SHALL expose a service listener for service-plane callers and a session listener that serves only the broker's goal endpoint; the session listener SHALL be the only Switchboard port reachable from the session host.

ID: REQ-butler-switchboard-006
Source: RFC 0038 D1, D3 and D7
Scope: v1-mandatory

#### Scenario: Domain processes share no address space with the broker
- **WHEN** the `butlers-up` container starts with every domain daemon
- **THEN** it SHALL run no Switchboard handler, broker or goal runner
- **AND** no domain handler SHALL share a process with the broker's signing key

#### Scenario: The session listener serves only the goal endpoint
- **WHEN** a session-host process sends a request to the session listener for anything other than the broker's goal endpoint
- **THEN** Switchboard SHALL refuse it
- **AND** ingest, service-plane tools, the service-plane relay and dashboard management SHALL NOT be served on the session listener

### Requirement: Ingest Envelopes Feed Goal Admission

Switchboard SHALL keep ingest envelope validation, deduplication and the durable buffer, and SHALL submit each accepted envelope that is neither withheld by a deterministic ingestion rule nor bound to a domain's deterministic channel ingest directly to goal admission, without a model classification or route-dispatch step. A duplicate submission of an envelope SHALL return the goal already admitted for it.

ID: REQ-butler-switchboard-007
Source: RFC 0038 D1 and D5; RFC 0003 disposition
Scope: v1-mandatory

#### Scenario: A duplicate envelope returns the existing goal
- **WHEN** a connector resubmits an envelope with the same idempotency key after its goal was admitted
- **THEN** admission SHALL return the existing goal
- **AND** no second goal SHALL be admitted and no second lead SHALL start

#### Scenario: Buffered envelopes survive a control-plane restart
- **WHEN** `butlers-control` restarts while accepted envelopes are in the durable buffer and not yet admitted
- **THEN** each buffered envelope SHALL be admitted exactly once after the restart
- **AND** none SHALL be lost or classified

### Requirement: Service-Plane Relay

Switchboard SHALL provide a service-plane relay on its service listener through which a registered daemon's deterministic code calls another agent's non-LLM-presentable service-plane tool, replacing the deleted generic `route` tool for that traffic. The relay SHALL NOT be LLM-presentable or reachable from the session listener, SHALL refuse any target tool that is LLM-presentable, SHALL admit no goal and start no reasoning session, and SHALL NOT treat a caller-supplied source identity as authority.

ID: REQ-butler-switchboard-008
Source: RFC 0038 D1 and D7
Scope: v1-mandatory

#### Scenario: A QA finding is relayed without a goal
- **WHEN** a butler's self-healing module relays an error report to the QA staffer
- **THEN** Switchboard SHALL forward it to QA's `report_finding` service-plane tool through the relay
- **AND** no goal SHALL be admitted and no reasoning session SHALL start

#### Scenario: A model-reachable target is refused
- **WHEN** a relay request names a target tool that is LLM-presentable
- **THEN** Switchboard SHALL refuse it without contacting the target
- **AND** the caller SHALL receive a typed refusal, because a model-reachable call requires a broker assertion (`REQ-capability-authorization-007`)
