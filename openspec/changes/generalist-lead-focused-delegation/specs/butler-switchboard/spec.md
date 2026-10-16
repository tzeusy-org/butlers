## MODIFIED Requirements

### Requirement: Cross-Container MCP Endpoint Resolution

`route()` (and everything built on it, including `switchboard.notification.deliver.deliver()`) SHALL resolve a target butler's registered MCP endpoint to a host reachable from the CALLER's own container, not only from `butlers-up` (the container every butler daemon self-registers itself into via `http://localhost:<port>`). When the `BUTLERS_HOST` environment variable is set to a value other than `localhost` (Docker Compose sets this on the `dashboard-api` / `dashboard-api-hotreload` containers, which host processes — e.g. `butlers.jobs.secrets_lifecycle` — that are NOT part of `butlers-up`), an exact `localhost` host in the resolved endpoint SHALL be rewritten to that value before the MCP connection is attempted. When `BUTLERS_HOST` is unset or equals `localhost` (the case inside `butlers-up` itself), resolution SHALL be unchanged (a no-op). When `SWITCHBOARD_MCP_URL` is configured, an exact registered `localhost:41100` Switchboard endpoint SHALL use that HTTP(S) URL's network destination instead of `BUTLERS_HOST`, preserving the registered path and query. This applies to dashboard, notify, relay and runtime MCP callers; domain endpoints SHALL continue to use `BUTLERS_HOST`. An invalid configured Switchboard URL SHALL refuse resolution.

Configuration loading SHALL apply this canonical Switchboard destination resolution to an explicit `[butler.switchboard].url` on `localhost:41100` before notify/connect, heartbeat reconnect, fact-authority admission/recovery and runtime source-endpoint consumers receive the config. It SHALL preserve the configured path/query, explicit remote or noncanonical endpoints, and unsplit localhost behavior when cross-container settings are absent.

#### Scenario: In-container caller reaches the registered endpoint unchanged
- **WHEN** a butler daemon running inside `butlers-up` calls `route()` to reach a sibling butler
- **AND** `BUTLERS_HOST` is unset
- **THEN** the MCP connection targets the sibling's self-registered `http://localhost:<port>/mcp` endpoint unchanged

#### Scenario: Cross-container caller is rewritten to the container-DNS host
- **WHEN** a process running in the `dashboard-api` container (e.g. `secrets_lifecycle`'s scheduled scan) calls `route()` to reach a butler
- **AND** `BUTLERS_HOST=butlers-up` is set (as Docker Compose configures on this container)
- **THEN** the MCP connection targets `http://butlers-up:<port>/mcp`, not `http://localhost:<port>/mcp`
- **AND** the delivery SHALL succeed rather than failing to connect

#### Scenario: A separated Switchboard remains reachable from domain and dashboard callers
- **WHEN** a caller resolves `http://localhost:41100/mcp` with `SWITCHBOARD_MCP_URL=http://butlers-control:41100/sse` and `BUTLERS_HOST=butlers-up`
- **THEN** its destination SHALL be `http://butlers-control:41100/mcp`
- **AND** a registered domain endpoint on port 41104 SHALL resolve to `butlers-up:41104` and remote registered hosts SHALL remain unchanged

In ISOLATED_CANDIDATE and SWITCHED_RELEASE the same endpoint-resolution contract SHALL apply to brokered capability forwarding and the service-plane relay in `butlers-control`, preserving registered-agent reachability after the separate cutover of classification and route dispatch. KEY_ABSENT_STAGE SHALL retain the current route and delivery callers above.

Source: RFC 0038 D1 and D7
Scope: v1-mandatory

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

In ISOLATED_CANDIDATE and SWITCHED_RELEASE, Switchboard SHALL run as the control plane in its own process and container, `butlers-control`, separate from the domain daemons in `butlers-up` and from the session host. It SHALL host ingress, goal admission, the goal ledger, the goal runner, the capability broker, and approval and insight brokerage, and SHALL NOT classify messages or dispatch routes. It SHALL expose a service listener for service-plane callers and a session listener that serves only the broker's goal endpoint; the session listener SHALL be the only Switchboard port reachable from the session host. KEY_ABSENT_STAGE SHALL instead run today's Switchboard service separately in butlers-control, preserve current classification, routing, notify and deliver, provision/mount/resolve no GOAL private key, and expose only a session-port GET /health that returns fixed unavailable (503); all goal calls and other session routes SHALL be refused. Its existing service listener and service health SHALL keep serving current callers. Only the documented operator/test health caller and the session-host network identity SHALL reach the stage session port. In ISOLATED_CANDIDATE and SWITCHED_RELEASE, the session listener SHALL serve only the broker goal endpoint to the session-host network identity and SHALL refuse GET /health and every other non-goal route; operational liveness SHALL use the service listener. A missing/mismatched key, ledger, catalog or custody precondition SHALL refuse broker calls as unavailable while deterministic service traffic continues. ISOLATED_CANDIDATE SHALL have no live ingress, schedules or dashboard wiring, no reachable legacy classification/route/spawner launcher, and goal admission SHALL be exercised only by a closed trusted test harness. SWITCHED_RELEASE SHALL wire actual ingress and declared obligations to goals, delete the old paths, enable domain verification and archive the complete change only after the one-release owner gates. Both profiles SHALL include the actual retained D18/adapter/helper/descendant custody boundary before any private-key mount. Stage evidence SHALL NOT close the original seven-criterion process-split task; stage current-routing and candidate valid-key evidence SHALL be separately identified, adopted and refreshed after affected source/profile changes. Effective profile SHALL be an immutable server startup/deployment selection, never caller or model input; stage selection with private material SHALL refuse startup/activation. Stage workload liveness/readiness SHALL use the service listener independently of deliberately unavailable session health, and candidate signer readiness SHALL require custody separately from service liveness.

KEY_ABSENT_STAGE SHALL preserve the already adopted fleet maximum of five simultaneous sessions through a deployment partition: exactly one control process with capacity one and one domain process with capacity four. Reservations SHALL be positive and their aggregate SHALL NOT exceed five. Baked and hotreload launchers SHALL select one process per role; activation and rollback SHALL stop the prior topology before starting its replacement. Unsplit production SHALL retain capacity five. This partition SHALL NOT change the process-local spawner semaphore or establish live aggregate-cap evidence by source inspection alone.

ID: REQ-butler-switchboard-006
Source: RFC 0038 D1, D3 and D7
Scope: v1-mandatory

#### Scenario: Domain processes share no address space with the broker
- **WHEN** the `butlers-up` container starts with every domain daemon
- **THEN** it SHALL run no Switchboard handler, broker or goal runner
- **AND** no domain handler SHALL share a process with the broker's signing key

#### Scenario: The session listener serves only the goal endpoint
- **WHEN** in ISOLATED_CANDIDATE or SWITCHED_RELEASE a session-host process sends a request to the session listener for anything other than the broker's goal endpoint
- **THEN** Switchboard SHALL refuse it
- **AND** ingest, service-plane tools, the service-plane relay and dashboard management SHALL NOT be served on the session listener

#### Scenario: A topology stage preserves current service traffic without a private signer
- **WHEN** KEY_ABSENT_STAGE is deployed for current callers
- **THEN** current classification, routing, notify and deliver retain their existing behavior and no GOAL private key is provisioned, mounted or resolvable
- **AND** GET /health on the separate session port returns unavailable (503), goal and other session calls are refused, and service health and traffic remain available
- **AND** neither valid-key acceptance nor cutover is recorded

#### Scenario: Valid-key activation does not wait for the production switch
- **WHEN** the adopted staging amendment and complete launcher/concurrent-custody proofs allow an ISOLATED_CANDIDATE dev activation
- **THEN** only that separate candidate may mount its control-only private key and exercise goal admission through the closed test harness
- **AND** its broker endpoint is reachable only from the session host, GET /health and non-goal routes are refused, and no legacy classifier or local untrusted launcher is reachable
- **AND** live ingress, schedules and dashboard remain on the current release and all production owner holds remain unfulfilled

#### Scenario: A stage receipt cannot close the complete process-split outcome
- **WHEN** only KEY_ABSENT_STAGE evidence is available for the original process-split task
- **THEN** all seven original criteria remain final obligations and the original task stays incomplete
- **AND** current-routing receipts carry exact source/configuration identities and affected candidate changes require fresh compatibility and custody evidence

#### Scenario: Session reachability never grants service or model-function authority
- **WHEN** a session guest in either candidate or switched profile tries a service route, health route, model-function launch or direct domain call
- **THEN** network and endpoint boundaries refuse it before any handler or launch
- **AND** a missing or mismatched GOAL key does not stop the existing deterministic service listener

### Requirement: Ingest Envelopes Feed Goal Admission

In ISOLATED_CANDIDATE and SWITCHED_RELEASE, Switchboard SHALL keep ingest envelope validation, deduplication and the durable buffer, and SHALL submit each accepted envelope that is neither withheld by a deterministic ingestion rule nor bound to a domain's deterministic channel ingest directly to goal admission, without a model classification or route-dispatch step. A duplicate submission of an envelope SHALL return the goal already admitted for it. In those profiles, each connector SHALL declare whether its channel carries messages from people or is a device or telemetry feed (for example `home_assistant`, `owntracks`, `gaming` and ActivityWatch). A feed envelope SHALL NOT be admitted as an owner or third-party goal: Switchboard delivers it to the owning domain's deterministic channel ingest on the service plane, which records it in that domain's own schema, and it SHALL wake reasoning only when it matches a subscription declared in git, which is admitted with origin `obligation` and that subscription's capability set (`REQ-capability-authorization-003`). Feed fields a person other than the owner can author SHALL be stamped untrusted. KEY_ABSENT_STAGE SHALL retain today's validated/deduplicated/durable-buffer classify-and-route behavior and SHALL NOT wire live traffic to goal admission. In ISOLATED_CANDIDATE, only the closed trusted test harness SHALL submit accepted test envelopes to goal admission; no live connector, schedule or dashboard caller SHALL submit them. SWITCHED_RELEASE SHALL apply this direct-goal contract to actual accepted ingress after the owner-authorized switch. Identity, origin, feed, exposure, idempotency and service-plane outcomes SHALL NOT be weakened by profile selection.

ID: REQ-butler-switchboard-007
Source: RFC 0038 D1 and D5; RFC 0003 disposition
Scope: v1-mandatory

#### Scenario: A duplicate envelope returns the existing goal
- **WHEN** in ISOLATED_CANDIDATE or SWITCHED_RELEASE, a connector resubmits an envelope with the same idempotency key after its goal was admitted
- **THEN** admission SHALL return the existing goal
- **AND** no second goal SHALL be admitted and no second lead SHALL start

#### Scenario: A feed envelope matches no subscription
- **WHEN** in ISOLATED_CANDIDATE or SWITCHED_RELEASE, a Home Assistant state change that matches no declared subscription is accepted
- **THEN** Home's deterministic channel ingest records it in Home's schema and no goal is admitted

#### Scenario: Buffered envelopes survive a control-plane restart
- **WHEN** in ISOLATED_CANDIDATE or SWITCHED_RELEASE, `butlers-control` restarts while accepted envelopes are in the durable buffer and not yet admitted
- **THEN** each buffered envelope SHALL be admitted exactly once after the restart
- **AND** none SHALL be lost or classified

#### Scenario: Stage ingress does not prematurely admit goals
- **WHEN** a current connector envelope reaches KEY_ABSENT_STAGE
- **THEN** the current validation, dedupe, durable buffer and routing behavior run unchanged and no live goal admission occurs
- **AND** a closed candidate test harness exercises the final admission semantics separately

### Requirement: Service-Plane Relay

Switchboard SHALL provide a service-plane relay on its service listener through which a registered daemon's deterministic code calls another agent's non-LLM-presentable service-plane tool, replacing the deleted generic `route` tool for that traffic. The relay SHALL NOT be LLM-presentable or reachable from the session listener, SHALL refuse any target tool that is LLM-presentable, SHALL admit no goal and start no reasoning session, and SHALL NOT treat a caller-supplied source identity as authority.

The relay SHALL be a set of named endpoints, not a generic target-and-payload forwarder: each endpoint SHALL fix its payload schema, its one target service-plane tool and an allowlist of calling daemons (for example domain-event fan-out, the QA error relay, module runtime wiring, and Travel's read of Health's medication snapshot). Switchboard SHALL authenticate the calling daemon's service identity on every request and SHALL refuse, without contacting any target, an unauthenticated caller, a caller not on that endpoint's allowlist, a payload that fails the endpoint's schema, or a request for an endpoint that does not exist.

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

#### Scenario: An unlisted or unauthenticated caller is refused
- **WHEN** a request reaches the medication-snapshot endpoint from a daemon other than Travel, or from a caller whose service identity does not authenticate
- **THEN** Switchboard SHALL refuse it without contacting Health
- **AND** a `source_butler` field in the payload SHALL NOT change the decision
