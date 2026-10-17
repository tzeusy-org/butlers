# Draft candidate: owner-thread rolling summary

Status: NOT ADOPTED. These are recognized candidate deltas for review. No authoring,
merge, artifact completion or .15 reference activates future generation or adoption.

## MODIFIED Requirements

### Requirement: Continue Conversation

Sending a follow-up message in an existing conversation SHALL preserve the thread context.

Once this exact candidate is independently reviewed, owner-adopted and integrated, follow-up context SHALL use the private rolling summary and bounded recent raw-tail contract below. This is proposed future behavior; authoring or source landing alone SHALL NOT enable generation, storage or consumption. Existing wrong-butler, archived conversation, raw message identity, durable Stop/retry and streaming behavior SHALL remain unchanged. For this future private-summary path, the prior "Send follow-up message" normalized_text conversation-context condition is expressly qualified: that durable field carries the admitted bounded raw-tail/omission context, while the useful older derivative reaches only the complete private runtime composition defined below. The preserved prior scenario does not authorize a derivative copy in normalized_text.

ID: REQ-dashboard-conversations-016
Source: dashboard-conversations § Continue Conversation; heart-and-soul/security.md § Session Sandboxing; design.md Decisions 1-8
Scope: proposed-owner-gated

#### Scenario: Send follow-up message

- **WHEN** `POST /api/butlers/{name}/conversations/{conversation_id}/messages` is called with `{ "message": "Follow up question" }` and an optional `page_context`
- **THEN** a user message row is inserted in `public.dashboard_messages`
- **AND** the message is submitted to the Switchboard's `ingest` MCP tool as an `ingest.v1` envelope with the same `endpoint_identity` as the original conversation and `event.external_thread_id = {conversation_id}`
- **AND** the envelope's `payload.normalized_text` includes prior conversation context (last N messages as summarized context, configurable, default last 5 exchange pairs)
- **AND** the response is streamed back via SSE
- **AND** `updated_at` and `message_count` on the conversation are updated

#### Scenario: Continue archived conversation

- **WHEN** a message is sent to a conversation with `status = 'archived'`
- **THEN** the conversation status is changed to `active` before processing
- **AND** the message is processed normally

#### Scenario: Continue conversation for wrong butler

- **WHEN** `POST /api/butlers/{name}/conversations/{conversation_id}/messages` is called but the conversation belongs to a different butler
- **THEN** a 404 response with `code: "CONVERSATION_NOT_FOUND"` is returned

### Requirement: Dashboard Ingestion Envelope Construction

Dashboard conversations SHALL construct `ingest.v1` envelopes that flow through the standard Switchboard ingestion pipeline, submitted to the Switchboard's `ingest` MCP tool. RFC 0003 §"ingest.v1 Envelope Format" defines `dashboard` / `internal` as direct owner-dashboard ingress: the dashboard API, rather than a connector startup probe, SHALL assign `dashboard:web:{conversation_id}` as the endpoint identity.

Once this exact candidate is adopted and every custody/placement integration gate below is satisfied, the durable ingest.v1 normalized_text SHALL contain only permitted bounded raw history, omission framing and the current raw message. It SHALL NOT contain private derivative text, provenance or a dereferenceable summary token. A private server-owned attempt binding outside exported envelope fields SHALL select the exact summary for late resolution into an eligible runtime invocation as encoded untrusted historical data. That overlay SHALL NOT become payload.raw.message, page_context, current owner intent, evidence, sender identity or routing control. Existing wire fields, routing pins, originally-captured page_context and stable message_id replay semantics remain unchanged. No public summary API/schema or caller-asserted reference is introduced.

ID: REQ-dashboard-conversations-017
Source: dashboard-conversations § Dashboard Ingestion Envelope Construction; heart-and-soul/security.md § Session Sandboxing; design.md Decisions 1-8
Scope: proposed-owner-gated

#### Scenario: Envelope structure for dashboard messages

- **WHEN** a dashboard message is submitted for ingestion
- **THEN** the envelope SHALL have:
  - `schema_version`: `"ingest.v1"`
  - `source.channel`: `"dashboard"`
  - `source.provider`: `"internal"`
  - `source.endpoint_identity`: `"dashboard:web:{conversation_id}"`
  - `event.external_event_id`: `"{message_id}"`, where dashboard UI provides one immutable client-generated ID for a new user message and reuses it for retry and Stop
  - `event.external_thread_id`: `"{conversation_id}"`
  - `event.observed_at`: current timestamp
  - `sender.identity`: `"dashboard:operator"`
  - `payload.normalized_text`: the user's message content (with conversation context for follow-ups)
  - `payload.raw`: `{"source": "dashboard", "conversation_id": "...", "message_id": "...", "message": "...", "page_context": {...}}` — `page_context` is present only when the client supplied one
  - `control.policy_tier`: `"interactive"`
  - `control.ingestion_tier`: `"full"`
  - `control.pinned_target`: present per the routing-pin scenarios below

#### Scenario: Dashboard messages bypass discretion

- **WHEN** a dashboard message is ingested by the Switchboard
- **THEN** the `"dashboard"` channel SHALL NOT be subject to discretion evaluation (operator messages are always intentional)

#### Scenario: Per-butler conversation envelope carries a routing pin

- **WHEN** a message is submitted via `POST /api/butlers/{name}/conversations` (or a follow-up on an existing per-butler conversation) and `{name}` is a routable domain butler (not the Switchboard staffer)
- **THEN** the constructed envelope SHALL set `control.pinned_target` to `{name}`
- **AND** the Switchboard SHALL route the message to `{name}` deterministically, without LLM classification choosing a different target

#### Scenario: Switchboard-addressed conversations are unpinned until routed

- **WHEN** a message is submitted via `POST /api/butlers/switchboard/conversations` (the dashboard chat widget's classification-routed conversation) and the conversation has no `routed_butler` yet
- **THEN** the constructed envelope SHALL NOT set `control.pinned_target` — the Switchboard staffer is never a registered, routable target
- **AND** the message proceeds through Switchboard's ordinary classify -> route pipeline

#### Scenario: Sticky follow-up pinning for classification-routed conversations

- **WHEN** a follow-up message is submitted via `POST /api/butlers/switchboard/conversations/{conversation_id}/messages` and the conversation already has a `routed_butler` set (from an earlier successful `route_to` decision)
- **THEN** the constructed envelope SHALL set `control.pinned_target` to `routed_butler`, bypassing classification entirely
- **AND** a conversation whose `routed_butler` is still NULL (not yet routed, or a bug-lane report with no domain-butler target) continues through classification as in the "unpinned until routed" scenario above

#### Scenario: Optional page context on dashboard messages

- **WHEN** a dashboard message is submitted with a `page_context` object (`route`, `query_params`, optional `entity_ref`, optional `visible_resource` {`kind`, `id`, `filters`, `window`}, optional `visible_summary`) on the request body
- **THEN** the API SHALL strip any query-param key containing a secret-ish marker (`token`, `key`, `secret`, `password`, `authorization`) before persisting or forwarding it, regardless of what the client sent
- **AND** the API SHALL reject a `visible_resource.kind` outside the closed registry vocabulary
- **AND** a payload exceeding the size budget SHALL be truncated (dropping `visible_resource.filters`, then `query_params`, then trimming `visible_summary`, in that order) with `truncated=true` set, never silently dropped or rejected outright
- **AND** the persisted user message row SHALL store the (possibly redacted/truncated) `page_context` plus a `captured_at` timestamp
- **AND** the envelope's `payload.raw.page_context` SHALL carry that object unchanged, grounding the statement for the routed butler
- **AND** when no `page_context` is provided, `payload.raw` SHALL NOT contain a `page_context` key

#### Scenario: A retry reuses the originally-captured page context

- **WHEN** a dashboard message is retried with the same client-generated `message_id` (`message_create_idempotent`'s conflict path)
- **THEN** the API SHALL forward the `page_context` stored on the original write, not a `page_context` on the retry request body, into the ingest envelope
- **AND** no new capture SHALL occur for the retried message

## ADDED Requirements

### Requirement: Private Owner-Thread Summary Input and Provenance

The future summary generator SHALL accept only a server-authorized, same-canonical-owner, same-final-thread snapshot of persisted dashboard user-visible or assistant-visible message content, after trusted visibility and secret-exclusion admission. An API body, message text, butler name or nullable owner field SHALL NOT establish that authority. Unknown ownership, binding, visibility or content admissibility SHALL make the summary unavailable without widening access. Hidden/system prompts, raw tool arguments/results, tool_calls, provider-session material, page_context (including visible_summary), credentials/secrets and other threads SHALL never enter generation, cached text or the historical fallback projection. The current user turn is outside the prior-history snapshot. This candidate does not generate or consume Telegram/cross-channel summaries.

The generator SHALL use eligible raw history, never a previous model summary, as its input. A complete prefix is ordered by server-persisted (created_at, message_id), with deterministic UUID tie-breaking and a digest of the exact membership, content and eligibility revisions through summary_through_message_id. A late/backdated insert or source mutation invalidates that prefix identity. No history may be silently omitted to fit generation limits. The proposed finite V1 limits are 1000 prior source messages and 64000 Unicode code points of eligible content per generation, one in-flight generation per owner/thread and a 60-second generation timeout. Exceeding a limit takes the marked raw-tail fallback; it does not summarize only a convenient subset while claiming a complete prefix. Generation is off the user-turn hot path through a separately configured existing ephemeral-model execution lane; it grants no new tools, provider credentials or budget authority.

A private committed record SHALL contain canonical owner/thread, summary_through_message_id, source-prefix digest and revision, summary_version, generator identity/version, model identity/version, exclusion-policy version and created_at in UTC. summary_version is a server-issued monotonic generation identity; schema/exclusion/generator/model versions are separate explicit fields. The record is untrusted derivative text, not fact, evidence, an owner command, steering memory, a citation or an approval. Raw history remains authoritative. No summary text or claimed provenance may alter current-turn identity/intent, routing, citations or tool/config/risk/approval bounds; an answer/action needing evidence must use the authorized raw source and existing gates.

ID: REQ-dashboard-conversations-018
Source: dashboard-conversations § Continue Conversation and Dashboard Ingestion Envelope Construction; heart-and-soul/vision.md Rule 1; security.md § Threat Boundaries; design.md Decisions 1-8
Scope: proposed-owner-gated

#### Scenario: Eligible old history is summarized rather than silently dropped

- **WHEN** an authorized thread has older eligible dashboard history outside the recent tail and the bounded generator succeeds
- **THEN** it commits a bounded derivative of that exact raw prefix, retaining useful earlier context for subsequent dashboard turns
- **AND** it does not merely add a truncation marker while continuing to discard all earlier context


#### Scenario: Wrong or missing owner-thread authority prevents summary access

- **WHEN** a request or stored record has a wrong owner/thread, unknown owner binding, foreign-thread through-message or caller-asserted authority
- **THEN** no foreign snapshot, cached text or generation is read or used
- **AND** the current authorized turn may use only its independently authorized local fallback; no guessed owner or cross-thread search repairs the mismatch


#### Scenario: Hidden and non-message sources never enter summary inputs

- **WHEN** a persisted record also contains prompts, tool_calls, raw tool arguments/results, page_context, provider handles or hidden internal content
- **THEN** the generator projection selects only separately admitted visible message content and excludes those fields and sources
- **AND** a visible_summary page-context field is still page_context and is excluded


#### Scenario: Visible credential material and unproven eligibility fail closed

- **WHEN** visible history includes classified or suspected secret/credential material, or trusted admissibility cannot be established
- **THEN** that material is not passed to the generator or historical fallback projection
- **AND** the summary is unavailable if a complete admitted prefix cannot be proved; there is no LLM-based proof of secret absence, credential-store lookup or silent source omission
- **AND** raw persisted history itself is not rewritten or claimed sanitized by this candidate


#### Scenario: Provenance binds an exact generation and source boundary

- **WHEN** a generated candidate is admitted for persistence
- **THEN** all mandatory provenance fields identify the exact canonical thread and through-message membership plus configured versions and UTC creation time
- **AND** the through-message belongs to the raw prefix and precedes the current turn; content alone is never a provenance proof


#### Scenario: Equal timestamps and a late earlier insert cannot falsify the prefix

- **WHEN** two messages have equal created_at or a later insert sorts before the captured boundary
- **THEN** canonical UUID tie-breaking is deterministic and the exact membership digest detects any changed earlier prefix
- **AND** message ID is a reference, not an assumed sortable UUID7 clock or proof of arrival order


#### Scenario: Derivative instructions do not gain authority

- **WHEN** summary text asserts an instruction, permission, tool result, owner fact or citation
- **THEN** it remains quoted or equivalently encoded as untrusted historical data under trusted server framing
- **AND** it cannot close its framing, become a system/developer message, rewrite payload.raw.message or supply decision/actor/identity/citation authority
- **AND** normal domain tool/config/risk/approval/audit boundaries still execute independently of summary text


#### Scenario: Raw history remains the only summarization source of truth

- **WHEN** a prior summary exists or an answer conflicts with that summary
- **THEN** a new generation reads the authorized raw prefix and an evidence-bearing answer resolves against raw source
- **AND** previous derivative text is not recursively fed as a visible-source substitute, promoted into memory or used to delete raw history


### Requirement: Fenced Summary Publication and Replay

The future summary writer SHALL compare-and-set against the exact expected private-summary revision and source-prefix digest/revision after generation, in one durable publication transaction. Its idempotency identity SHALL be (thread, summary_through_message_id, summary_version), bound to canonical owner and immutable source/config/output digests. Same-key identical replay returns its recorded result without new generation or write; same-key different provenance/content is a conflict, never last-write-wins. Stale or non-monotonic generations SHALL not replace a newer summary.

A consumer SHALL validate ownership, supported versions, source-prefix membership and integrity, plus a complete admitted raw-tail interval after the summary boundary in one consistent snapshot. A source edit, deletion, hiding, owner/binding change or backdated insertion invalidates the affected derivative before later consumption. Appends strictly after an unchanged prefix may use the existing summary only if every eligible post-boundary message is represented in the bounded tail; an uncovered middle interval requires a new generation or marked fallback. A stale worker, restart or retry cannot resurrect invalidated text. Current ingress message_id idempotence, Stop, provider-resume and ambiguity contracts remain independent and unchanged.

ID: REQ-dashboard-conversations-019
Source: dashboard-conversations § Continue Conversation and Dashboard Ingestion Envelope Construction; heart-and-soul/vision.md Rule 1; security.md § Threat Boundaries; design.md Decisions 1-8
Scope: proposed-owner-gated

#### Scenario: Identical summary publication retry returns the original receipt

- **WHEN** publication retries with the same tuple and exact owner/source/config/output digests
- **THEN** the recorded receipt is returned without a second generation, payload row or revision increment
- **AND** a superseded generation cannot become active again through replay


#### Scenario: Same tuple with changed content is rejected

- **WHEN** the same publication identity is supplied with different source, provenance, output or owner digest
- **THEN** publication reports conflict and leaves the accepted record unchanged
- **AND** it does not allocate a new version on behalf of that retry


#### Scenario: Stale generation cannot overwrite the winner

- **WHEN** two workers captured the same expected revision but one has already committed a newer current summary
- **THEN** the later stale CAS is rejected without replacing or rolling back the winner
- **AND** its uncommitted generated text and source snapshot are discarded


#### Scenario: Source mutation during generation rejects publication

- **WHEN** covered content, eligibility, source membership, owner or binding changes before the generator commits
- **THEN** the transaction detects the changed source identity and rejects publication
- **AND** it does not silently choose a different through-message or publish against the current snapshot


#### Scenario: Newer messages are represented without an uncovered interval

- **WHEN** new messages arrive strictly after a committed unchanged prefix
- **THEN** the consumer includes the complete eligible post-boundary tail when it fits and otherwise uses marked fallback pending a new generation
- **AND** a prefix plus only the last few tail messages may not masquerade as complete continuity


#### Scenario: Tampered or unsupported cached summary falls back safely

- **WHEN** stored summary provenance/integrity is absent, inconsistent, unsupported or not tied to this owner/thread snapshot
- **THEN** the consumer does not inject that summary and uses the bounded marked fallback when its raw input is authorized
- **AND** no best-effort cached-body reuse repairs trust validation failure


#### Scenario: Invalidation and process restart cannot resurrect content

- **WHEN** a source edit/delete/hide invalidates a derivative while a worker or consumer restarts
- **THEN** durable invalidation/revision fencing prevents old results from being republished or consumed
- **AND** ordinary replay of an accepted dashboard turn remains its original turn, not a summary-triggered new ingress


#### Scenario: Final thread placement is an implementation prerequisite

- **WHEN** bu-0ynlk.15 has not been independently reviewed, exactly adopted, landed and integrated with authoritative owner/binding/ordering semantics
- **THEN** this candidate authorizes no summary storage or consumption on its proposed schema
- **AND** there is no interim dashboard_conversations summary column, .15 migration allocation or implicit adoption/release of that foreign task


### Requirement: Bounded Summary Context Fallback and Custody

The future dashboard context builder and eligible runtime resolver SHALL provide a valid rolling summary plus bounded recent authorized raw tail to the runtime, with private provenance/untrusted framing and explicit truncation/omission whenever eligible historical content is absent or clipped. The durable/exported projection SHALL exclude derivative text and provenance; only the private late-bound runtime overlay includes them. Successful summarization must preserve earlier-prefix continuity as a real bounded derivative; the implementation SHALL NOT claim this contract is met by a marker-only fallback. Failed, unavailable, stale, oversized, secret-ineligible or timed-out generation SHALL fail open only to the authorized bounded raw tail, never to hidden/foreign data or a fabricated summary. Missing authorization permits no history lookup; the current independently authorized message is preserved with an honest no-history marker.

The proposed V1 context budget SHALL be at most 4000 Unicode code points excluding the current message: summary text at most 1600, encoded raw-tail section (including roles/labels) at most 2000, and trusted framing/markers at most 400. Recent context keeps the current configurable last-N window, default 5 exchange pairs interpreted as at most 10 eligible messages. Per-message historical content is capped at 500 code points using the suffix, with clipping disclosed; older whole tail entries are dropped only with a deterministic omitted-count/boundary marker. The builder must reserve framing before selection and may not slice an already serialized preamble. These limits and clipping direction are proposed adoption choices, not silently activated configuration. The raw current message and stored page_context continue through their existing independent contracts unchanged.

The private payload SHALL be attached to the final .15 owner-thread identity as protected internal state, not its proposed public/client summary field. The one-current-durable-payload claim includes every transport, session, adapter and storage holder listed by Private Summary Transit and Custody; private-row cleanup alone is insufficient. Revocable transient overlays may exist only under that custody barrier and cannot become durable copies or replay state. It SHALL not be readable/writable by arbitrary butler public-table grants, serialized through SELECT-star projection, returned in conversation/thread models or lists/search/SSE, logged in telemetry/errors/audit, or exposed through MCP recall/tools unless separately specified and exactly adopted. Keep only one current full summary payload per thread; replacement discards superseded full text and generation input/output buffers. Minimal content-blind replay/provenance receipts follow the raw thread lifetime, with durable invalidation fencing rather than retained deleted text. Source deletion/hiding removes affected derivative payloads and pending generation buffers before they can be consumed again; final thread deletion removes every attached payload, receipt and job reference. Archive is not deletion and creates no new raw-history retention policy. No additional retention TTL, client feature or cross-channel consumption is authorized by this candidate.

ID: REQ-dashboard-conversations-020
Source: dashboard-conversations § Continue Conversation and Dashboard Ingestion Envelope Construction; heart-and-soul/vision.md Rule 1; security.md § Threat Boundaries; design.md Decisions 1-8
Scope: proposed-owner-gated

#### Scenario: Valid summary and recent tail preserve earlier continuity

- **WHEN** a valid exact prefix summary and complete admitted recent tail are available
- **THEN** the eligible runtime receives the labelled untrusted earlier summary and ordered later raw messages within the reserved budget through the private late-bound overlay
- **AND** durable normalized_text, stored session prompt and generic client projections contain no derivative text or private provenance
- **AND** the raw current message is appended separately without being summarized or truncated


#### Scenario: Generation failure uses an honest marked bounded tail

- **WHEN** summary generation/read/validation fails, times out, is unavailable or exceeds budget
- **THEN** the turn continues with only authorized bounded raw history and an explicit summary-unavailable/truncated marker
- **AND** the marker states that older history is not represented rather than implying a complete recollection


#### Scenario: Individual clipping and tail omission are explicit

- **WHEN** a historical message exceeds 500 code points or the selected tail exceeds its reserved budget
- **THEN** the builder selects deterministic suffixes/drops older complete entries and records the omitted/clipped count or boundary
- **AND** role/framing boundaries are never lost by taking the last 4000 characters of a serialized preamble


#### Scenario: Generation input budget is not a silent subset summary

- **WHEN** the complete admitted prefix exceeds 1000 messages or 64000 content code points
- **THEN** no partial prefix is labelled complete and the generator uses the explicit budget-failure fallback
- **AND** the original rolling-summary outcome remains a real success path for admissible bounded prefixes


#### Scenario: No prior history does not fabricate a summary

- **WHEN** a new authorized conversation has no prior history
- **THEN** its current message uses the established envelope with no fabricated summary
- **AND** no generation or new summary row is required solely to satisfy a marker contract


#### Scenario: Client and tool surfaces do not expose the private derivative

- **WHEN** conversation/thread list, get, search, message model, SSE or existing recall tooling serializes data
- **THEN** the private summary text and its generator/provenance metadata are absent
- **AND** bu-0ynlk.15 draft client summary expectation is not permission to project this private payload; any exposure requires separate exact contract/adoption


#### Scenario: Replacement does not retain superseded full text

- **WHEN** a newer valid generation replaces the active payload
- **THEN** only the one current full payload remains and obsolete generation buffers/full derivative text are discarded
- **AND** content-blind receipts cannot carry deleted text or be used to reinject it; no new retention extension is inferred


#### Scenario: Source and final thread deletion dispose of derivatives

- **WHEN** covered visible content is deleted/hidden or its final thread is removed
- **THEN** covered summaries and pending generated payloads are invalidated and discarded under durable fencing, and final thread deletion removes attached receipts/jobs
- **AND** raw history is not deleted merely because a summary succeeded; existing raw retention policy is unchanged


#### Scenario: Archive preserves existing history semantics

- **WHEN** a thread is archived and later continued
- **THEN** existing archive/unarchive behavior remains and usable private state is subject to the same source/trust checks
- **AND** archive adds no TTL or deletion side effect and is not permission to keep invalidated summaries


#### Scenario: Dashboard-only scope survives final cross-channel binding

- **WHEN** the final owner thread also has Telegram or another channel binding
- **THEN** this dashboard candidate does not generate/consume a Telegram summary or automatically include cross-channel-origin messages
- **AND** a new cross-channel input/consumer policy requires separate exact adoption rather than inheriting .15 handoff authority



### Requirement: Private Summary Transit and Custody

The future summary path SHALL have exactly one governed current durable payload per final owner-thread. Every other persistent holder SHALL contain only independently permitted raw/base content or private content-blind binding/receipt metadata, never derivative text or protected excerpts. Private row-only cleanup is insufficient. The closed inventory in custody-map.md covers generator buffers; dashboard envelopes; both message_inbox text/raw-envelope fields; public ingestion metadata; classification/routing/tool/decomposition records; durable route inbox/outbox/retry/dead-letter state; session prompt/system receipt/output/tools/errors; process logs/telemetry/audit; memory episodes/search/recall; runtime temporary files/transcripts/provider-resume/cache state; and backing replicas/WAL/backups/restores. A new or unclassified holder SHALL make overlay use unavailable until its owning contract/disposal/read paths are reconciled, not create an implicit retention class.

The private attempt binding SHALL bind canonical owner/thread, dashboard message/turn identity, canonical accepted request/ingestion-event identity, one server-issued runtime attempt, exact generation/source/version digests, custody epoch and revocable lease. It stays in the final private custody domain, never payload.raw, normalized_text, page_context, request_context, trace_context, attachments or a client field. Caller/model origin or generation fields are not authority. Trusted acceptance and attempt admission precede resolution; an acceptance timeout/unknown outcome cannot mint a second context/turn. Cross-daemon resolution uses owning authenticated infrastructure/MCP and normal registered configuration, not foreign-domain SQL or a general LLM summary-read tool. Final .15 owner/binding/origin representation and exact internal resolver/transport are owned prerequisites, not existing capabilities claimed here.

An eligible consumer SHALL first persist its safe base prompt and exact derivative-free system receipt, then resolve the supported binding under the same-owner/thread source/custody epoch and a revocable use lease. It injects a bounded encoded untrusted user-context overlay for that invocation only, without replacing raw current intent or stored base prompt. Before adapter invocation, a private content-blind composition/use receipt SHALL bind the complete intended user input: base_prompt_digest, private_overlay_digest, effective_user_prompt_digest, exact UTF-8/codepoint counts, deterministic composition_version and ordered untrusted framing, linked to the exact persisted effective-system receipt and the canonical owner/thread/message/request/attempt/generation/source/custody-epoch/lease/runtime-mode identity. The actual adapter SHALL receive that complete admitted base-plus-overlay-plus-current-message composition in the recorded order; a stored base prompt, digest or omission marker alone is not the intended complete invocation. This receipt contains no prompt/derivative text or dereferenceable public capability, stays in private custody, and cannot reconstruct retired text. Existing exact effective-system prompt bytes/digest/provenance remain the actual supplied system component, not a substitute for this user-input receipt. Authorized classification and routed domain invocations obey the same rule: neither forwards full summary text in route.execute input/context, model-authored route tool arguments, classify/decomposition results or durable queue state. Each independently revalidates its exact binding. Raw routing identity and normal tool/config/risk/approval/audit remain authoritative. Missing/stale/unsupported binding/mode uses authorized marked raw-tail fallback, with no dereference, automatic generation, legacy copy-bearing execution or claimed rolling-summary success.

Replacement, source edit/delete/hide, owner/binding change, late prefix insertion and final-thread deletion SHALL atomically revoke/advance the custody epoch before new publication, resolution or copy-bearing externally visible results pass. In-flight generator/runtime/transport users are cancelled and fenced; full transient buffers/captures are cleared and late completion/ack cannot revive or emit them. Custody completion requires every registered holder fenced and disposed; new resolution is forbidden while cleanup/cancellation outcome is unknown. Existing finite invocation deadlines bound waiting; crash/lost ack leaves durable revocation and unavailable summary use, never best-effort old text. Raw deletion remains its owning operation and this feature must not undo/extend raw retention. Final thread disposal removes private payload, bindings, receipts and jobs; unrelated ingress/session rows retain only independently permitted raw/base evidence under unchanged retention.

Backing replicas/WAL/backup snapshots or older restores SHALL not reactivate superseded/deleted payload. If a holder cannot remove full text at the barrier, it may retain only unreadable ciphertext after irreversible retirement of the per-generation decryption capability; restore cannot restore a retired capability or bypass durable invalidation. Exact owning storage/key/disposal/restore design and executing verification are required before summary use; current PostgreSQL deletion or backup encryption is not assumed sufficient. This proposed physical-custody obligation grants no additional readable copy lifetime or client exposure.

Replay resolves only the originally recorded exact generation/attempt if still valid. A retired reference returns unavailable without regeneration, same-attempt rebinding to a newer payload or restoration of old text. A separately authorized retry attempt may capture fresh validated context while preserving original message_id/page_context and normal Stop/ingress ambiguity rules. A summary reference/lease supplies neither execution authority nor paid-generation trigger. Public payload/session/search/export/SSE/tool doors never dereference it. Model output/tools/process captures are potential copy holders: protected payload copies must be omitted before persistence/egress; ordinary answers/actions need independent raw-source admission and normal gates. Unknown copy/admission proof withholds that copy-bearing result and follows the normal authorized fallback, rather than accepting model assurances or treating derivative text as raw truth. Independently persisted raw content retains its source identity even if its wording coincides; a model label cannot launder a private copy into that class.

Generation/use SHALL be admitted only for an exactly configured finite runtime/provider mode whose memory/file/transcript/cache/resume/process-capture behavior meets this inventory. Summary-bearing attempts cannot reuse provider-native or persistent CLI state containing prior private context; compatible stateless/non-retaining delivery must be verified. Failure/failover chooses only another verified eligible mode or raw-tail fallback. Clearing a local buffer cannot erase provider-retained text; unknown retention/disposal makes that mode ineligible. Current provider/deployment compliance is UNKNOWN and remains a separately owned verification gate. Real useful private-prefix-plus-tail invocation is mandatory for implementation success; eligibility failures are fallback, never marker-only fulfillment.

ID: REQ-dashboard-conversations-021
Source: R1 verified ingress/session copy seams; original clauses 1–8; security.md partial model trust; RFC 0003/0007 source identity and API custody
Scope: proposed-owner-gated

#### Scenario: Accepted ingress stores no private summary copy

- **WHEN** a turn with valid private history enters the adopted ingress path
- **THEN** message_inbox.normalized_text and raw_payload.payload.normalized_text contain only permitted durable projection, without private text/provenance
- **AND** raw current message/page_context, request_id, dedupe and routing pins remain unchanged

#### Scenario: Private late resolution provides real earlier context

- **WHEN** an eligible authorized attempt has a valid source-bound private lease
- **THEN** its actual invocation receives the complete admitted base, useful older summary, recent tail and current message in the recorded untrusted framing/order
- **AND** the private composition/use receipt matches full effective user-input bytes/digest/counts, exact system-receipt linkage and source/generation/attempt/epoch/lease identity
- **AND** the derivative is absent from durable ingress/route/session/process/client copies; a digest or marker-only invocation is not success

#### Scenario: Forged or unavailable private binding cannot grant access

- **WHEN** a caller/model/restarted worker supplies foreign/stale/unknown binding or pretends a marker is authority
- **THEN** no private/foreign payload is read and only authorized marked raw-tail fallback is used
- **AND** no duplicate turn, automatic generation or legacy copy path repairs failure

#### Scenario: Routing and queue recovery preserve references without text

- **WHEN** classification, route acceptance, retry/dead-letter or crash recovery serializes a summary-enabled turn
- **THEN** only permitted raw/base content and protected content-blind binding lifecycle may be retained
- **AND** model route/context/tool output cannot launder derivative into durable state; each invocation revalidates its lease

#### Scenario: Replacement retires old bindings across every holder

- **WHEN** a newer generation wins private publication CAS
- **THEN** old epoch/bindings are revoked before new consumption and all old full/transient copies are disposed
- **AND** inbox/session/queue evidence has no old derivative; old replay cannot resolve it

#### Scenario: Concurrent source deletion fences an active invocation

- **WHEN** covered raw content is deleted/hidden while generator/runtime/result writer uses an old epoch
- **THEN** durable revocation rejects new publication/resolution/result admission, cancels active users and disposes captures
- **AND** cleanup/lost-ack ambiguity stays unavailable, never completed custody or restoration of text

#### Scenario: Final thread deletion crosses queue and storage holders

- **WHEN** the final thread is deleted with an accepted or retrying turn
- **THEN** private payload/bindings/receipts/jobs are disposed and queued/in-flight references fenced before custody completion
- **AND** unrelated permitted raw/session evidence keeps existing retention without a derivative copy

#### Scenario: Restoring old storage cannot resurrect retired summaries

- **WHEN** replica/WAL/backup or older snapshot contains superseded/deleted protected bytes
- **THEN** revocation plus disposal or irreversible capability retirement keeps them unreadable/non-resolvable
- **AND** restore cannot restore a retired key; missing proof keeps summary use unavailable

#### Scenario: Unverified runtime retention selects honest fallback

- **WHEN** adapter/provider retention outside adopted disposal or capture/cleanup behavior is unknown
- **THEN** it receives no private overlay; the authorized turn uses marked raw tail or another independently verified eligible mode
- **AND** flags/auth labels/local buffer cleanup do not substitute for disposal proof

#### Scenario: Captured or echoed derivative is not ordinary source truth

- **WHEN** output/tools/stderr/error/memory/projection carries private payload or admission is unproven
- **THEN** the copy is not persisted, emitted as a client payload or promoted into memory/evidence/approval
- **AND** ordinary raw-source-backed responses and existing action/approval/audit gates remain independent; no prompt-only promise admits it

## Source References

- Non-Negotiable Rule 1: owner sovereignty and explicit derivative retention choice.
- Non-Negotiable Rules 3 and 4: MCP-mediated access and intelligence in ephemeral sessions.
- heart-and-soul/security.md: prompt inputs, partial model trust and server authority.
- RFC 0003: ingest.v1 identity/routing stays unchanged.
- RFC 0007: existing dashboard API/response projections stay unchanged.
- bu-0ynlk.16 original design clauses 1–8; bu-0ynlk.15 remains unadopted placement.
