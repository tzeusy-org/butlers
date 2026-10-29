## MODIFIED Requirements

### Requirement: Dashboard Ingestion Envelope Construction

**Before the RFC 0038 cutover, dashboard conversations SHALL follow this serving ingress contract:**

Dashboard conversations SHALL construct `ingest.v1` envelopes that flow through
the standard Switchboard ingestion pipeline, submitted to the Switchboard's
`ingest` MCP tool. RFC 0003 §"ingest.v1 Envelope Format" SHALL recognize
`dashboard` / `internal` as the canonical pair for this direct owner-dashboard
ingress; it is not connector provenance. The dashboard API, not a connector
startup probe, SHALL assign `dashboard:web:{conversation_id}` as the endpoint
identity.

**After the RFC 0038 cutover (goal-admission successor):**

Dashboard conversations SHALL construct server-authenticated, immutable
`ingest.v1` envelopes at the dashboard API and submit them through Switchboard's
standard ingestion boundary. RFC 0003 §"ingest.v1 Envelope Format" recognizes
`dashboard` / `internal` as the canonical direct owner-dashboard pair; it is not
connector provenance. The dashboard API, not a connector startup probe or
client assertion, SHALL assign `dashboard:web:{conversation_id}` as endpoint
identity. After the RFC 0038 cutover, dashboard reasoning ingress SHALL admit
an owner goal, never classify or dispatch a target-butler session. Request-ID
lineage SHALL follow that goal and its invocations; pre-cutover records remain
readable. D19's `dashboard-one-mind-goal-surface` owns dashboard bodies and
continuity; this requirement grants no new target, capability or resume
permission. Legacy serving clauses below apply to current live traffic before
the owner-authorized switch, not an isolated candidate. ISOLATED_CANDIDATE
SHALL keep live dashboard ingress disconnected, as the adopted control-plane
profile contract requires.

ID: REQ-dashboard-conversations-001
Source: RFC 0003 § ingest.v1 Envelope Format; dashboard-conversations § Dashboard Ingestion Envelope Construction; design.md Decision 1 and Goal-admission conversion; RFC 0038 D1, D2, D19
Scope: v1-mandatory

#### Scenario: Envelope structure for dashboard messages

- **WHEN** an authenticated owner dashboard message is submitted for ingestion
- **THEN** the envelope SHALL have:
  - `schema_version`: `"ingest.v1"`
  - `source.channel`: `"dashboard"`
  - `source.provider`: `"internal"`
  - `source.endpoint_identity`: `"dashboard:web:{conversation_id}"`
  - `event.external_event_id`: `"{message_id}"`, where `message_id` is
    client-generated for a new user message and reused for a retry of that
    message
  - `event.external_thread_id`: `"{conversation_id}"`
  - `event.observed_at`: current timestamp
  - `sender.identity`: `"dashboard:operator"`
  - `payload.normalized_text`: the user's message content (with conversation
    context for follow-ups)
  - `payload.raw`: `{"source": "dashboard", "conversation_id": "...",
    "message_id": "...", "message": "...", "page_context": {...}}` —
    `page_context` is present only when the client supplied one
  - `control.policy_tier`: `"interactive"`
  - `control.ingestion_tier`: `"full"`
  - `control.pinned_target`: present only before cutover per the phased
    routing-pin scenarios below; it SHALL NOT select a post-cutover target
    session or confer goal authority

#### Scenario: Dashboard messages bypass discretion

**Before the RFC 0038 cutover (legacy serving contract):**

- **WHEN** a dashboard message is ingested by the Switchboard
- **THEN** the `"dashboard"` channel SHALL NOT be subject to discretion
  evaluation (operator messages are always intentional)

**After the RFC 0038 cutover (successor contract):**

- **WHEN** an authenticated owner dashboard message needs reasoning
- **THEN** it SHALL enter ordinary goal admission with its authenticated origin,
  exposure and capability constraints
- **AND** the `dashboard` channel label SHALL NOT create a policy bypass,
  authenticate a caller or enlarge goal authority.

#### Scenario: Per-butler conversation envelope carries a routing pin

**Before the RFC 0038 cutover (legacy contract):**

- **WHEN** a message is submitted via `POST /api/butlers/{name}/conversations`
  (or a follow-up on an existing per-butler conversation) and `{name}` is a
  routable domain butler (not the Switchboard staffer)
- **THEN** the constructed envelope SHALL set `control.pinned_target` to
  `{name}`
- **AND** the Switchboard SHALL route the message to `{name}` deterministically,
  without LLM classification choosing a different target

**After the RFC 0038 cutover (successor contract):**

- **WHEN** the RFC 0038 cutover has occurred and dashboard ingress refers to a
  domain region
- **THEN** Switchboard SHALL admit the message as an owner goal rather than
  route it to that domain's session
- **AND** no legacy pin SHALL create a session, grant capabilities or enlarge
  goal authority; D19 owns the successor dashboard body.

#### Scenario: Switchboard-addressed conversations are unpinned until routed

**Before the RFC 0038 cutover (legacy contract):**

- **WHEN** a message is submitted via `POST
  /api/butlers/switchboard/conversations` (the dashboard chat widget's
  classification-routed conversation) and the conversation has no
  `routed_butler` yet
- **THEN** the constructed envelope SHALL NOT set `control.pinned_target` — the
  Switchboard staffer is never a registered, routable target
- **AND** the message proceeds through Switchboard's ordinary classify -> route
  pipeline

**After the RFC 0038 cutover (successor contract):**

- **WHEN** a dashboard message enters Switchboard after the RFC 0038 cutover
- **THEN** it SHALL use goal admission, without a classify -> route stage or
  a Switchboard/domain target session
- **AND** immutable direct-ingress provenance SHALL remain distinct from the
  admitted goal's origin and invocation lineage.

#### Scenario: Classification-routed follow-up is sticky

**Before the RFC 0038 cutover (legacy contract):**

- **WHEN** a follow-up message is submitted via `POST
  /api/butlers/switchboard/conversations/{conversation_id}/messages` and the
  conversation already has a `routed_butler` set (from an earlier successful
  `route_to` decision)
- **THEN** the constructed envelope SHALL set `control.pinned_target` to
  `routed_butler`, bypassing classification entirely
- **AND** a conversation whose `routed_butler` is still NULL (not yet routed, or
  a bug-lane report with no domain-butler target) continues through
  classification as in the "unpinned until routed" scenario above

**After the RFC 0038 cutover (successor contract):**

- **WHEN** a dashboard follow-up is submitted after the RFC 0038 cutover
- **THEN** its original conversation/message identity and authenticated source
  SHALL be preserved through goal admission
- **AND** a stored `routed_butler` SHALL NOT dispatch or resume a domain session;
  D19 owns successor continuity under D9's unchanged no-resume rule.

#### Scenario: Optional page context is preserved

- **WHEN** a dashboard message is submitted with a `page_context` object
  (`route`, `query_params`, optional `entity_ref`) on the request body
- **THEN** the envelope's `payload.raw.page_context` SHALL carry the persisted,
  privacy-filtered object unchanged, grounding the statement for the admitted
  goal after cutover (the routed butler before cutover)
- **AND** when no `page_context` is provided, `payload.raw` SHALL NOT contain a
  `page_context` key

#### Scenario: Sticky follow-up pinning for classification-routed conversations

**Before the RFC 0038 cutover (legacy contract):**

- **WHEN** a follow-up message is submitted via `POST /api/butlers/switchboard/conversations/{conversation_id}/messages` and the conversation already has a `routed_butler` set (from an earlier successful `route_to` decision)
- **THEN** the constructed envelope SHALL set `control.pinned_target` to `routed_butler`, bypassing classification entirely
- **AND** a conversation whose `routed_butler` is still NULL (not yet routed, or a bug-lane report with no domain-butler target) continues through classification as in the "unpinned until routed" scenario above

**After the RFC 0038 cutover (successor contract):**

- **WHEN** a dashboard follow-up refers to a pre-cutover classification-routed
  conversation after the RFC 0038 cutover
- **THEN** its stored route SHALL remain historical provenance, not admission,
  capability or session-resume authority
- **AND** reasoning ingress SHALL use goal admission with original
  conversation/message and retry identity preserved; D19 owns continuity.

#### Scenario: Optional page context on dashboard messages

- **WHEN** a dashboard message is submitted with a `page_context` object (`route`, `query_params`, optional `entity_ref`, optional `visible_resource` {`kind`, `id`, `filters`, `window`}, optional `visible_summary`) on the request body
- **THEN** the API SHALL strip any query-param key containing a secret-ish marker (`token`, `key`, `secret`, `password`, `authorization`) before persisting or forwarding it, regardless of what the client sent
- **AND** the API SHALL reject a `visible_resource.kind` outside the closed registry vocabulary
- **AND** a payload exceeding the size budget SHALL be truncated (dropping `visible_resource.filters`, then `query_params`, then trimming `visible_summary`, in that order) with `truncated=true` set, never silently dropped or rejected outright
- **AND** the persisted user message row SHALL store the (possibly redacted/truncated) `page_context` plus a `captured_at` timestamp
- **AND** the envelope's `payload.raw.page_context` SHALL carry that object unchanged, grounding the statement for the admitted goal after cutover (the routed butler before cutover)
- **AND** when no `page_context` is provided, `payload.raw` SHALL NOT contain a `page_context` key

#### Scenario: A retry reuses the originally-captured page context

- **WHEN** a dashboard message is retried with the same client-generated `message_id` (`message_create_idempotent`'s conflict path)
- **THEN** the API SHALL forward the `page_context` stored on the original write, not a `page_context` on the retry request body, into the ingest envelope
- **AND** no new capture SHALL occur for the retried message
- **AND** after cutover, a duplicate submission of the same envelope SHALL
  return the goal already admitted from it rather than admit another goal
