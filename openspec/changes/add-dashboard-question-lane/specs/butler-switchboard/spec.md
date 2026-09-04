## MODIFIED Requirements

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
- **AND** SHALL NOT silently fall back to routing the message to the `general` butler — that fallback is specific to non-dashboard channels
- **AND** a genuine question with no identifiable owner SHALL always resolve via `cannot_answer` (Lane D) rather than this generic silent path — the classification prompt SHALL NOT instruct a best-guess route or a fallback to `general` for an unowned question

#### Scenario: Route acknowledgement is terminal for the dead-letter net; downstream session completion is out of scope

- **WHEN** `route_to_butler` dispatches via `route.execute` and the target butler returns an `accepted` (or `ok`) status for a dashboard-originated message
- **THEN** the dead-letter net gate SHALL treat that acknowledgement as terminal success for the synchronous reply contract — the target butler has confirmed only that it accepted the dispatch, not that the spawned downstream session will run to completion
- **AND** if that downstream session subsequently crashes, hangs, or times out after acknowledgement, this contract SHALL NOT capture the failure to the dead-letter queue and SHALL NOT persist an additional in-thread reply on its behalf; the owner is left with whatever live-only signal (e.g. a widget-side `SESSION_TIMEOUT`) the caller surfaces independently
- **AND** this ack-terminal boundary is a deliberate, accepted scope decision for the synchronous reply contract, not an oversight — closing the last hop (e.g. a reply-watch timeout that persists an in-thread failure note when an acknowledged downstream session never completes) is a possible future extension, not a current requirement
