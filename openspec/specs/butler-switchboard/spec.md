## Purpose

Defines the Switchboard's routing contract: which agents are eligible for user-message classification, how domain butlers receive routed messages, how misroutes are corrected and re-dispatched, and how passive source channels (including the wellness wearable/health-device channel) are validated and routed without LLM classification.

## Requirements

### Requirement: Domain Butler Registry

The Switchboard SHALL maintain a registry of all agents (butlers and staffers) and SHALL only route user messages to butler-typed agents. Staffer-typed agents are excluded from user-message classification but remain reachable for butler-to-staffer routing.

#### Scenario: Staffers excluded from user-message classification
- **WHEN** the Switchboard classifies an incoming user message
- **THEN** it SHALL only consider agents with `type = "butler"` as routing candidates
- **AND** agents with `type = "staffer"` SHALL be excluded from the candidate set
- **AND** this exclusion applies to the classification/routing layer only — staffers remain reachable via other mechanisms

#### Scenario: Butler-to-staffer routing preserved
- **WHEN** a butler invokes `notify()` targeting a staffer (e.g., messenger for outbound delivery)
- **THEN** the Switchboard SHALL route the request to the staffer as it does today
- **AND** this routing path is not affected by the user-message classification exclusion

#### Scenario: Staffer registration includes type
- **WHEN** a staffer registers with the Switchboard at startup
- **THEN** the registration payload SHALL include `type = "staffer"`
- **AND** the Switchboard's registry SHALL store this type field alongside the agent's name, port, and liveness state
- **AND** the eligibility sweep SHALL continue to track staffer liveness (staffers are infrastructure-critical and their liveness matters for butler-to-staffer routing)

#### Scenario: Lifestyle domain classification
- **WHEN** the Switchboard classifies an incoming message
- **AND** the message content relates to music, listening, playlists, entertainment (movies, TV, books, games, podcasts), food preferences, favorite restaurants, cuisines, recipes, hobbies, personal interests, leisure activities, or daily routines
- **THEN** the Switchboard SHALL route the message to the `lifestyle` butler at `http://localhost:41109`

#### Scenario: Multi-butler fanout with lifestyle overlap
- **WHEN** a message contains both lifestyle and health signals (e.g., "I've been stress-eating Thai food all week")
- **THEN** the Switchboard SHALL route to both `lifestyle` (food preference: Thai) and `health` (stress eating pattern)
- **AND** each butler SHALL extract domain-relevant facts independently

#### Scenario: Lifestyle vs General disambiguation
- **WHEN** a message could be classified as either lifestyle or general
- **AND** the message relates to taste, preferences, entertainment, or routines
- **THEN** the Switchboard SHALL prefer routing to `lifestyle` over `general`
- **AND** `general` SHALL only receive messages that do not fit any domain butler's scope

#### Scenario: Misroute re-dispatch restricted to butlers
- **WHEN** `correct_route` is called with a `correct_butler` target
- **THEN** the target SHALL be validated as a butler-typed agent (not a staffer)
- **AND** if the target is a staffer, the tool SHALL return `status=failed` with a summary explaining that user messages cannot be re-dispatched to staffers

### Requirement: Misroute Correction Re-dispatch
The Switchboard SHALL expose a `correct_route` MCP tool that accepts a misroute correction request from any butler and re-dispatches the original message to the correct target butler. This tool is called by downstream butlers' `correct` tool when handling `misroute` correction type.

#### Scenario: Successful misroute re-dispatch
- **WHEN** `correct_route` is called with `request_id` (the original ingestion event's request_id), `correct_butler` (the intended target), and `correction_reason` (why the original routing was wrong)
- **THEN** the Switchboard SHALL look up the original ingestion event by `request_id`, construct a new route dispatch to `correct_butler` with the original message content, dispatch it, and return the re-dispatch outcome
- **AND** the original `message_inbox` record SHALL be annotated with `correction_status=rerouted` and `corrected_to_butler` in its metadata

#### Scenario: Re-dispatch with expired ingestion event
- **WHEN** `correct_route` is called with a `request_id` whose ingestion event has been dropped from `message_inbox` (past 1-month retention)
- **THEN** the tool SHALL return `status=failed` with a summary explaining that the original message is no longer available for re-dispatch
- **AND** the summary SHALL suggest the user re-send the message to the correct butler directly

#### Scenario: Re-dispatch to unregistered butler rejected
- **WHEN** `correct_route` is called with a `correct_butler` that is not in the Switchboard's butler registry
- **THEN** the tool SHALL return `status=failed` with a summary listing the available butlers

#### Scenario: Re-dispatch preserves original request context
- **WHEN** a misroute correction re-dispatches a message
- **THEN** the re-dispatched request SHALL carry the original `request_id` and source context (source_channel, source_sender_identity)
- **AND** the re-dispatch SHALL add `correction_id` to the request metadata to link back to the correction audit trail

#### Scenario: Re-dispatch returns new session ID for traceability
- **WHEN** `correct_route` successfully re-dispatches a message to the correct butler
- **THEN** the return value SHALL include `new_session_id` (the UUID of the session created by the re-dispatch on the correct butler)
- **AND** the calling butler's `correct` tool SHALL propagate this `new_session_id` in its own `correction_details` and `summary`

#### Scenario: Original routing outcome updated
- **WHEN** a misroute correction is successfully re-dispatched
- **THEN** the Switchboard's lifecycle record for the original request SHALL be updated to reflect the correction: original routing marked as `corrected`, new routing recorded alongside

### Requirement: Cross-Container MCP Endpoint Resolution

`route()` (and everything built on it, including `switchboard.notification.deliver.deliver()`) SHALL resolve a target butler's registered MCP endpoint to a host reachable from the CALLER's own container, not only from `butlers-up` (the container every butler daemon self-registers itself into via `http://localhost:<port>`). When the `BUTLERS_HOST` environment variable is set to a value other than `localhost` (Docker Compose sets this on the `dashboard-api` / `dashboard-api-hotreload` containers, which host processes — e.g. `butlers.jobs.secrets_lifecycle` — that are NOT part of `butlers-up`), an exact `localhost` host in the resolved endpoint SHALL be rewritten to that value before the MCP connection is attempted. When `BUTLERS_HOST` is unset or equals `localhost` (the case inside `butlers-up` itself), resolution SHALL be unchanged (a no-op).

#### Scenario: In-container caller reaches the registered endpoint unchanged
- **WHEN** a butler daemon running inside `butlers-up` calls `route()` to reach a sibling butler
- **AND** `BUTLERS_HOST` is unset
- **THEN** the MCP connection targets the sibling's self-registered `http://localhost:<port>/mcp` endpoint unchanged

#### Scenario: Cross-container caller is rewritten to the container-DNS host
- **WHEN** a process running in the `dashboard-api` container (e.g. `secrets_lifecycle`'s scheduled scan) calls `route()` to reach a butler
- **AND** `BUTLERS_HOST=butlers-up` is set (as Docker Compose configures on this container)
- **THEN** the MCP connection targets `http://butlers-up:<port>/mcp`, not `http://localhost:<port>/mcp`
- **AND** the delivery SHALL succeed rather than failing to connect

### Requirement: Wellness Source Channel and Google Health Provider Registration

The Switchboard SHALL accept `wellness/google_health` as a valid ingestion source.

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
- **AND** SHALL be routed by the existing `source_channel = "wellness"` rule (`route_to:health`) over the policy-bypass path with no LLM session spawned

#### Scenario: Google Health wellness envelope unaffected

- **WHEN** an ingest.v1 envelope arrives with `source.channel = "wellness"` and `source.provider = "google_health"`
- **THEN** validation and routing behavior SHALL be identical to before the Home Assistant promotion

#### Scenario: Unregistered provider still rejected

- **WHEN** an ingest.v1 envelope arrives with `source.channel = "wellness"` and a provider other than `"google_health"` or `"home_assistant"`
- **THEN** the envelope SHALL be rejected with the `invalid_source_provider` validation error

### Requirement: Wellness Ingest Event Shape

Wellness ingest envelopes SHALL follow the canonical `ingest.v1` schema with Google-Health-specific field semantics.

#### Scenario: Wellness envelope acceptance

- **WHEN** a wellness ingest envelope arrives at the Switchboard
- **THEN** it SHALL be accepted if it conforms to `ingest.v1` with:
  - `source.channel = "wellness"`
  - `source.provider = "google_health"`
  - `source.endpoint_identity` matching pattern `"google_health:user:<google_user_id>"`
  - `sender.identity` set to the owner's Google user ID (canonically the account email today), resolved to the owner entity via the `relationship.entity_facts` `has-email` triple pre-registered during pairing (the canonical ingress resolver reads `relationship.entity_facts` only; `public.contact_info` is vestigial)
  - `control.idempotency_key` matching pattern `"google_health:<resource>:<record_id>"`

#### Scenario: Identity resolution does not create temporary contacts

- **WHEN** the Switchboard processes a wellness envelope's `sender.identity`
- **THEN** it SHALL resolve the owner via the pre-registered `relationship.entity_facts` `has-email` triple
- **AND** SHALL NOT invoke `create_temp_contact()`
- **AND** SHALL NOT emit disambiguation candidates

#### Scenario: Wellness envelopes do not route as interactive messages

- **WHEN** a wellness envelope is accepted
- **THEN** it SHALL NOT be treated as an interactive channel
- **AND** no reply pathway SHALL be established
- **AND** the envelope SHALL be routed directly to the Health butler via the ingest handler registration for `wellness/google_health`

### Requirement: Dedicated Routing to Health Butler

The Switchboard SHALL route all `wellness/google_health` envelopes to the Health butler.

#### Scenario: Direct routing

- **WHEN** an accepted wellness envelope passes deduplication
- **THEN** the Switchboard SHALL dispatch it to the Health butler
- **AND** SHALL NOT dispatch to any other butler
- **AND** wellness envelopes are non-interactive and bypass the user-message classification stage entirely, following the precedent established for `gaming/steam`, `location/owntracks`, and `spotify/spotify`

### Requirement: Explicit Chronicler Routing Boundary

Switchboard SHALL route explicit retrospective time-review requests to Chronicler
and SHALL NOT route passive source events to Chronicler solely because they are
timestamped.

#### Scenario: Retrospective time-review request routes to Chronicler

- **WHEN** the user asks "what did I do yesterday afternoon?"
- **THEN** Switchboard SHALL classify the request as a Chronicler-owned retrospective time-review intent
- **AND** route the request to Chronicler

#### Scenario: Music recommendation remains Lifestyle

- **WHEN** the user asks "recommend music based on what I listened to last week"
- **THEN** Switchboard SHALL route the request to Lifestyle, not Chronicler
- **AND** Lifestyle MAY use its own domain evidence for taste and recommendation work

#### Scenario: Time-accounting music question routes to Chronicler

- **WHEN** the user asks "how much time did I spend listening to music last week?"
- **THEN** Switchboard SHALL route the request to Chronicler
- **AND** Chronicler SHALL answer from projected temporal records

#### Scenario: Scheduling request does not route to Chronicler

- **WHEN** the user asks "schedule a meeting tomorrow"
- **THEN** Switchboard SHALL NOT route the request to Chronicler
- **AND** the request SHALL route to the appropriate calendar/general scheduling owner

#### Scenario: Passive timestamped event not routed to Chronicler

- **WHEN** a passive source event such as Spotify playback, Steam activity, OwnTracks location, email, or chat metadata enters the system
- **THEN** Switchboard SHALL NOT route it to Chronicler solely because it contains time evidence
- **AND** Chronicler SHALL consume compatible evidence later through projection jobs

### Requirement: Dashboard Chat-Widget Classification Lanes

The Switchboard SHALL classify `dashboard` source-channel messages (the
owner's floating chat widget) into one of four lanes instead of always
calling `route_to_butler`: Lane A (data statement/correction) or Lane B
(bug/system report). Bug/system reports SHALL NEVER be routed to a domain
butler. The remaining two lanes are Lane C (action request) and Lane D
(question); Lane D SHALL be answered either via `answer_question` (a
domain butler answers from its own tools, or the system-scope fallback
dead-letters until Concierge system-scope tools exist) or via
`cannot_answer` (dead-letters directly); neither Lane D tool SHALL ever
route to a domain butler for an ordinary write, and `cannot_answer` SHALL
NEVER file a QA bug report.

#### Scenario: Lane A — data statement routes with deterministic confirm-loop context

- **WHEN** a dashboard message is classified as a data statement or correction
- **THEN** the classification session SHALL call `route_to_butler` exactly as for any other channel
- **AND** the routed envelope's `input.context` SHALL deterministically carry the conversation's `conversation_id`, its `page_context` (if any), and instructions to interpret the statement, apply it, and confirm via `conversation_reply` — appended in code regardless of what the classification session itself wrote into `context` or `prompt`

#### Scenario: Lane A — first successful route stamps sticky routed_butler

- **WHEN** `route_to_butler` for a dashboard-originated message receives an `accepted` status from the target butler
- **THEN** the Switchboard SHALL stamp `routed_butler` on the conversation (best-effort; a stamping failure SHALL NOT fail the route call)

#### Scenario: Pinned/sticky policy bypass also carries the deterministic context block

- **WHEN** a dashboard-originated message is dispatched via the ingestion policy bypass (`control.pinned_target` from a per-butler conversation, or sticky `routed_butler` follow-up pinning) rather than through the classification session's `route_to_butler` call
- **THEN** the bypass SHALL load the same `conversation_id`/`page_context` dashboard turn context and inject the identical deterministic confirm-loop instruction block into the routed envelope's `input.context`
- **AND** a dashboard turn with no resolvable `conversation_id` (e.g. the dashboard context could not be loaded) SHALL dispatch with no context block, exactly as a non-dashboard policy bypass does

#### Scenario: Lane B — bug/system report is filed to QA, never routed to a domain butler

- **WHEN** a dashboard message is classified as a bug or system report (e.g. "the concentration chart is empty for child-of")
- **THEN** the classification session SHALL call `file_bug_report` instead of `route_to_butler`
- **AND** `file_bug_report` SHALL compute a canonical fingerprint and relay a finding to the QA staffer via the internal `route()` function targeting `report_finding` (the same plumbing QA canary injection uses)
- **AND** the message SHALL NOT be routed to any domain butler via `route_to_butler`
- **AND** the tool SHALL post a `conversation_reply` acknowledgment containing the case reference (the fingerprint's first 12 characters), whether or not the QA relay itself succeeded

#### Scenario: Lane D — a domain question dispatches through the answer-block spine, never the confirm-loop block

- **WHEN** a dashboard message is classified as a genuine question with an identifiable domain owner (e.g. "how much did I spend on groceries this month?")
- **THEN** the classification session SHALL call `answer_question(scope="domain", question, target)` naming the owning butler as `target`
- **AND** `answer_question` SHALL dispatch through the same `route.execute` spine `route_to_butler` uses (via the shared `_dispatch_dashboard_target` helper), but SHALL inject a read-only answer-block instruction (`_build_dashboard_answer_block`) into `input.context` instead of the confirm-loop block: the routed session MUST answer only from its own tools, cite what it consulted via `conversation_reply`'s `sources` list when grounded, or give an honest decline (never fabricate a citation) when it cannot ground the answer
- **AND** a successful acknowledged dispatch SHALL flow through the same routed/acked/failed bookkeeping `route_to_butler` populates, but SHALL leave `routed_butler` unset so every follow-up re-enters four-lane classification and cannot bypass the answer lane's read-only/citation contract or inherit a stale domain target

#### Scenario: Lane D tools require dashboard conversation context

- **WHEN** `answer_question` or `cannot_answer` is called without a valid dashboard `conversation_id`
- **THEN** the tool SHALL fail closed with `reason="dashboard_context_required"`
- **AND** it SHALL NOT route a butler, capture a dead letter, or create a conversation reply

#### Scenario: Lane D — a system-scope question falls back to the honest-decline dead-letter path

- **WHEN** the classification session calls `answer_question(scope="system", question)` and no Concierge system-scope answering tool is available
- **THEN** `answer_question` SHALL NOT route to any domain butler
- **AND** it SHALL dead-letter the request with `failure_category="unanswerable"` and persist an in-thread `conversation_reply` naming what was checked, via the same shared path `cannot_answer` uses

#### Scenario: Lane D — cannot_answer dead-letters directly and never files a bug report or domain route

- **WHEN** the classification session determines a question cannot be answered from any available tool or owner (e.g. no butler owns the domain, or the owning butler's tools do not cover the question) and calls `cannot_answer(question_summary, scope_checked, reason)`
- **THEN** the tool SHALL capture the request to the dead-letter queue (`source_table="message_inbox"`, `failure_category="unanswerable"`)
- **AND** SHALL persist an in-thread `conversation_reply` naming exactly what was checked (`scope_checked`) and the `reason`
- **AND** SHALL NOT call `file_bug_report` and SHALL NOT route to any domain butler via `route_to_butler`

#### Scenario: Lane exclusivity is enforced at the tool layer, not just the classification prompt

- **WHEN** a dashboard classification session calls `file_bug_report`, `answer_question`, or `cannot_answer`, and then calls `route_to_butler` for the same session (regardless of what the classification prompt instructs)
- **THEN** `route_to_butler` SHALL refuse to dispatch to a domain butler and SHALL return a structured refusal (`status: "refused"`, `reason: "dashboard_lane_conflict"`) instead of invoking `route.execute`
- **AND** the refusal SHALL be logged at WARNING with the conversation id and the attempted target butler
- **WHEN** a dashboard classification session calls `route_to_butler` and then calls `answer_question` or `cannot_answer` for the same session, or calls `answer_question`/`cannot_answer` a second time in the same session (including a repeat of itself)
- **THEN** the second call SHALL be refused with the same structured refusal (`status: "refused"`, `reason: "dashboard_lane_conflict"`) — `answer_question` and `cannot_answer` are strict single-shot per turn, unlike `file_bug_report`
- **WHEN** a dashboard classification session calls `route_to_butler` and then calls `file_bug_report` for the same session
- **THEN** `file_bug_report` SHALL still file the bug report (bug reports are terminal and are never suppressed)
- **AND** the co-occurrence SHALL be logged at WARNING with the conversation id and outcome-qualified targets, and SHALL be surfaced in `file_bug_report`'s own result (`dashboard_lane_conflict`) and in the pipeline's `RoutingResult.route_result` rather than being hidden by tool-call extraction that stops at the first matching call
- **AND** acknowledged domain-butler dispatches SHALL appear only in `co_occurring_dispatched_targets`; failed or refused calls with no acknowledgement in the classification session SHALL appear only in `co_occurring_attempted_only_targets`; the two lists SHALL be mutually exclusive and the ambiguous `co_occurring_route_targets` field SHALL NOT be emitted
- **AND** this exclusivity guard SHALL be scoped to dashboard-source sessions only (a `dashboard_context` carrying a `conversation_id`) — non-dashboard Switchboard flows (e.g. domain-butler-initiated `route_to_butler` calls, QA canary injection) SHALL be unaffected

#### Scenario: Unroutable dashboard message dead-letters and notifies the owner

- **WHEN** a dashboard message's classification session calls none of `route_to_butler`, `file_bug_report`, `answer_question`, or `cannot_answer` (e.g. an ambiguous or unclassifiable message), the classification spawn raises an exception, or `route_to_butler` was attempted but no target acknowledged the route because every `route.execute` dispatch failed
- **THEN** the Switchboard SHALL capture the request to the dead-letter queue (`source_table="message_inbox"`)
- **AND** SHALL persist an in-thread `conversation_reply` telling the owner a lane decision could not be made, referencing the dead-letter case id
- **AND** a zero-acknowledgement route attempt SHALL produce typed `route_result.status="unroutable"` and a non-null `routing_error`, never a routed result or routed log claim
- **AND** the resulting dead letter SHALL be marked replay-eligible for an explicit owner retry
- **AND** SHALL NOT silently fall back to routing the message to the `general` butler — that fallback is specific to non-dashboard channels
- **AND** a genuine question with no identifiable owner SHALL always resolve via `cannot_answer` (Lane D) rather than this generic silent path — the classification prompt SHALL NOT instruct a best-guess route or a fallback to `general` for an unowned question

#### Scenario: Route acknowledgement is terminal for the dead-letter net; downstream session completion is out of scope

- **WHEN** `route_to_butler` dispatches via `route.execute` and the target butler returns an `accepted` (or `ok`) status for a dashboard-originated message
- **THEN** the dead-letter net gate SHALL treat that acknowledgement as terminal success for the synchronous reply contract — the target butler has confirmed only that it accepted the dispatch, not that the spawned downstream session will run to completion
- **AND** if that downstream session subsequently crashes, hangs, or times out after acknowledgement, this contract SHALL NOT capture the failure to the dead-letter queue and SHALL NOT persist an additional in-thread reply on its behalf; the owner is left with whatever live-only signal (e.g. a widget-side `SESSION_TIMEOUT`) the caller surfaces independently
- **AND** this ack-terminal boundary is a deliberate, accepted scope decision for the synchronous reply contract, not an oversight — closing the last hop (e.g. a reply-watch timeout that persists an in-thread failure note when an acknowledged downstream session never completes) is a possible future extension, not a current requirement
