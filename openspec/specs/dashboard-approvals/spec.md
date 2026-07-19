## Purpose

The dashboard-approvals capability defines the API surface and dashboard UX for
the human-in-the-loop approvals queue: listing approval actions, viewing action
detail, deciding actions (approve/deny/defer), managing notification policy
(quiet hours), streaming lifecycle events, and surfacing autonomy promotion/
demotion suggestions.
## Requirements
### Requirement: Approvals action list API

The dashboard API SHALL expose `GET /api/approvals/actions` which returns a paginated list of approval actions.

The endpoint SHALL accept the following query parameters:
- `offset` (integer, optional, default 0) -- pagination offset
- `limit` (integer, optional, default 50) -- maximum number of actions to return
- `status` (string, optional) -- filter by action status: `pending`, `approved`, `rejected`, `expired`, or `executed`
- `tool_name` (string, optional) -- filter by the tool that requested the action
- `since` (ISO 8601 timestamp, optional) -- include only actions created on or after this timestamp
- `until` (ISO 8601 timestamp, optional) -- include only actions created on or before this timestamp

The response MUST be a `PaginatedResponse<ApprovalAction>` where each `ApprovalAction` object contains:
- `id` -- string UUID identifying the action
- `tool_name` -- string name of the tool requesting approval
- `butler` -- string name of the butler that owns the action
- `status` -- one of `"pending"`, `"approved"`, `"rejected"`, `"expired"`, `"executed"`
- `description` -- string human-readable description of the action
- `why` -- string | null serif paragraph explaining why human input is needed
- `evidence` -- string[] | null array of mono evidence lines
- `constraints` -- object mapping constraint names to their values (tool-specific structure)
- `created_at` -- ISO 8601 timestamp when the action was created
- `expires_at` -- ISO 8601 timestamp when the action will expire if not decided
- `decided_at` -- ISO 8601 timestamp when the action was approved/rejected (null if pending)
- `decided_by` -- string identifier of who decided (user, rule ID, auto-expired) (null if pending)
- `rule_id` -- string UUID of the rule that auto-approved (null if manual or not approved)
- `execution_count` -- integer count of times this action has been executed (0 if not executed)
- `target_contact` -- object (nullable) containing resolved target contact info: `id` (UUID), `name` (string), `roles` (string array). Null if the action does not target a specific contact.

#### Scenario: List with new fields populated

- **WHEN** `GET /api/approvals/actions` is called and the underlying `pending_actions` rows have `why` and `evidence` populated
- **THEN** the response includes the `why` paragraph and `evidence` array on each `ApprovalAction` row.

#### Scenario: List with legacy rows

- **WHEN** `GET /api/approvals/actions` is called and the underlying `pending_actions` rows have `why = NULL` and `evidence = []` (pre-migration data)
- **THEN** the response includes `why: null` and `evidence: []` on those rows; the rest of the row is unchanged.

#### Scenario: Fetch pending approval actions

- **WHEN** `GET /api/approvals/actions?status=pending` is called
- **THEN** the API MUST return all actions with `status = "pending"` sorted by `created_at` descending
- **AND** each action MUST include all required fields including `constraints` and `target_contact`
- **AND** the response status MUST be 200

#### Scenario: Target contact populated for notify actions

- **WHEN** a pending `notify` action has `contact_id='abc-123'` in its constraints
- **THEN** the `target_contact` field MUST include the contact's `id`, `name`, and `roles`

#### Scenario: Target contact null for non-contact actions

- **WHEN** a pending action has no contact_id in its constraints
- **THEN** the `target_contact` field MUST be `null`

#### Scenario: Filter actions by tool name

- **WHEN** `GET /api/approvals/actions?tool_name=notify` is called
- **THEN** the API MUST return only actions where `tool_name = "notify"`
- **AND** the results MUST include all statuses unless further filtered

#### Scenario: Filter actions by date range

- **WHEN** `GET /api/approvals/actions?since=2026-02-10T00:00:00Z&until=2026-02-17T23:59:59Z` is called
- **THEN** the API MUST return only actions with `created_at` between the specified timestamps (inclusive)

#### Scenario: Pagination with default limit

- **WHEN** `GET /api/approvals/actions` is called
- **THEN** the API MUST return at most 50 actions (default limit)
- **AND** the response MUST include a `meta` object with `total`, `offset`, and `limit` fields

#### Scenario: Pagination with custom offset

- **WHEN** `GET /api/approvals/actions?offset=50&limit=25` is called
- **THEN** the API MUST return actions 50-74 (25 actions starting at offset 50)

#### Scenario: No pending actions

- **WHEN** `GET /api/approvals/actions?status=pending` is called and no pending actions exist
- **THEN** the API MUST return an empty array
- **AND** the response status MUST be 200

---

### Requirement: Approvals Page in Dispatch Language

The dashboard SHALL render `/approvals` in the Dispatch design language as a replacement (not a duplicate) for the legacy approvals page. `/approvals` is the One Trust Console: the single surface for the pending decision queue, the decision dossier, and the standing autonomy ledger.

#### Scenario: Approvals page layout

- **WHEN** a user navigates to `/approvals`
- **THEN** the page renders, in vertical order:
  - **Page header**: title "Approvals", mono eyebrow "system · approvals", clock.
  - **Three-pane body**: left rail of pending approvals (rule-separated rows, ranked per the Queue ranking scenario below), center pane dossier of the selected approval, right pane Autonomy panel (always visible — see the Autonomy Panel requirement below).
  - **Dossier body**: `title` headline (sans 500, 22px), `why` serif paragraph (`max-width: 50ch`), `evidence` mono lines (rule-separated), `proposed_action` summary, primary `Approve` commit button, secondary `Deny` and `Defer` pill buttons.
  - **Policy section** below the body: quiet-hours editor (`start_hour`, `end_hour`, `timezone`).
  - **History section** at the bottom: last 30 decided approvals from `GET /api/approvals/history`; each row links to `/approvals/{id}` and opens the same dossier read-only.
- **AND** the page contains no Kanban-style columns, no charts, no cards.

#### Scenario: Legacy page deleted in same PR

- **WHEN** the new `ApprovalsPage` lands
- **THEN** the legacy approvals component (the prior `ApprovalsPage` content) is REMOVED in the same PR
- **AND** no parallel `/approvals/legacy` route exists.

#### Scenario: Target contact shown in approval dossier

- **WHEN** a pending `notify` action targets contact "Chloe" with `roles = []`
- **THEN** the dossier MUST display "Chloe" as the target contact
- **AND** the role badges MUST be empty (non-owner)

#### Scenario: Owner-targeted action shows owner badge

- **WHEN** a pending action targets the owner contact with `roles = ['owner']`
- **THEN** the dossier MUST display the owner's name with an "owner" role badge

#### Scenario: Expiring action countdown

- **WHEN** an action's expiration time is within 1 hour
- **THEN** the expiration countdown MUST display in a warning color (e.g., red or orange)

#### Scenario: Empty state when no actions

- **WHEN** a user navigates to `/approvals` with no pending or recent approvals
- **THEN** the page MUST display an empty state message (e.g., "No pending approvals")

### Requirement: Every Approval Has a URL

The dashboard SHALL expose `/approvals/:id` as a first-class route rendering the same `ApprovalsPage`, with the URL selecting the dossier.

#### Scenario: Deep link selects the named approval

- **WHEN** a user navigates directly to `/approvals/{id}`
- **THEN** the dossier pane fetches and displays that approval's detail via `GET /api/approvals/{id}`, regardless of arrival order in the pending queue
- **AND** this holds for both pending and already-decided approvals (a decided approval renders its read-only dossier).

#### Scenario: Selecting a rail item updates the URL

- **WHEN** a user clicks a pending approval in the left rail
- **THEN** the browser URL becomes `/approvals/{id}` for that approval.

#### Scenario: History rows deep-link into the dossier

- **WHEN** a user clicks a row in the History section
- **THEN** the browser navigates to `/approvals/{id}` for that decided approval and its (read-only) dossier renders.

#### Scenario: Deciding the explicitly-selected item advances the URL

- **WHEN** the approval currently named in the URL is approved, denied, or deferred
- **THEN** the URL advances to the next-ranked pending approval's `/approvals/{id}`, or to `/approvals` if none remain.

### Requirement: Queue Ranked by Expiry and Blast Radius

The pending rail SHALL be ordered by decision urgency, not arrival order.

#### Scenario: Expiring items rank ahead of items with no expiry

- **WHEN** the pending queue contains an approval expiring within the hour and an approval with no `expires_at` that arrived earlier
- **THEN** the expiring approval renders first in the rail.

#### Scenario: Blast radius breaks ties among similarly-urgent items

- **WHEN** two pending approvals have comparable expiry urgency
- **THEN** the approval with the higher blast-radius tool (e.g. an outbound `notify`/`send_*` call) ranks ahead of a lower-radius internal data write (e.g. an `assert`/`store` call).

### Requirement: Keyboard-Driven Triage with Undo

`/approvals` SHALL support fully keyboard-driven triage of the pending rail: `j`/`k` move roving focus (and selection) between rail items, and `a`/`d`/`x` schedule approve/deny/defer on the focused item. These bare-key shortcuts are inactive while focus is inside an input, textarea, or content-editable element, while a modifier key is held, or while a pending `g`-chord (see the global command-menu shortcuts) owns the next keystroke.

Because a rapid keystroke during triage is cheap to mis-press, a decision scheduled via `a`/`d`/`x` SHALL NOT call its approve/deny/defer endpoint immediately. It SHALL instead enter a per-item pending state, held for a fixed undo window, during which the item renders distinctly (dimmed, verb-labeled, e.g. "Approving…") in the rail and an undo toast is shown; only after the window elapses without an undo does the mutation fire. Clicking the pending rail item, or the toast's Undo action, cancels the scheduled decision before it reaches the backend. Mouse clicks on the dossier's own Approve/Deny/Defer buttons are unaffected by this window and continue to fire immediately (the existing one-click optimistic design).

#### Scenario: j/k roving focus moves the selection

- **WHEN** the pending rail has focus (or the page has keyboard focus outside an editable field) and the user presses `j`
- **THEN** selection (and the URL) advances to the next-ranked pending item
- **AND** pressing `k` moves selection to the previous-ranked item.

#### Scenario: a/d/x schedule a decision instead of firing immediately

- **WHEN** the user presses `a`, `d`, or `x` while a pending approval has keyboard focus
- **THEN** the approve/deny/defer endpoint is NOT called immediately
- **AND** the rail item renders a per-item pending state (dimmed, verb-labeled) and an undo toast appears
- **AND** the real mutation fires only after the undo window elapses without the decision being undone.

#### Scenario: Undo cancels a scheduled decision before it reaches the backend

- **WHEN** the user clicks the pending rail item, or the undo toast's action, before the undo window elapses
- **THEN** the scheduled decision is cancelled and no approve/deny/defer request is sent
- **AND** the item returns to its normal (non-pending) rendering.

#### Scenario: Keyboard shortcuts do not fire while typing

- **WHEN** an input, textarea, or content-editable element has focus (e.g. the inline deny-reason field)
- **THEN** `j`, `k`, `a`, `d`, and `x` keystrokes are treated as ordinary text input, not triage shortcuts.

### Requirement: Approved-but-Undispatched Renders Amber, Never Success-Green

An approval whose backend `status` is `"approved"` (approved but not yet dispatched — see the dispatched-vs-approved distinction in Approval Verbs) SHALL never render with the same success-green treatment as `"executed"`.

#### Scenario: Stalled approval reads as a distinct, actionable state

- **WHEN** an approval's status is `"approved"` (dispatch did not run)
- **THEN** the UI renders it with an amber/warning color family, labeled distinctly from a settled decision (e.g. "stalled")
- **AND** an executed action continues to render in its own distinct color, never sharing the amber "stalled" treatment.

### Requirement: Autonomy Panel

`/approvals` SHALL render an always-visible Autonomy panel from `GET /api/approvals/gated-tools`, replacing the standalone rules CRUD route. The endpoint returns every enabled configured gate, including an `active_rules` list that can be empty; each gate also identifies its owning butler, risk tier, and effective expiry.

#### Scenario: Standing rules panel replaces the orphaned rules route

- **WHEN** a user is on `/approvals`
- **THEN** the Autonomy panel is visible without further navigation
- **AND** no separate `/approvals/rules` route exists.

#### Scenario: Live use counts and inline revoke

- **WHEN** the Autonomy panel renders a standing rule
- **THEN** it displays the rule's current `use_count` (and `max_uses` when set)
- **AND** provides an inline revoke action (two-step confirm within the panel, not a native `window.confirm` dialog) that calls `POST /api/approvals/rules/{id}/revoke`.

#### Scenario: Configured zero-rule gate stays visible

- **WHEN** a configured gate has no active standing rules
- **THEN** the panel MUST display that tool with the status "always ask"
- **AND** it MUST NOT infer that unlisted tools are gated or that every action across the fleet requires manual approval.

#### Scenario: No configured gates

- **WHEN** no enabled approvals configuration declares a gated tool
- **THEN** the panel displays that no approval-gated tools are configured.

#### Scenario: Gate-rule source is degraded

- **WHEN** the gate baseline is returned with `meta.sources_degraded`
- **THEN** the panel MUST name the unavailable source
- **AND** MUST NOT label that source's tools "always ask" when their active-rule state is unknown.

### Requirement: Approvals Flat List API

The dashboard SHALL expose `GET /api/approvals?state=waiting|decided|all` as a flat-list view complementing the existing `GET /api/approvals/actions` paginated list.

#### Scenario: Filter by state

- **WHEN** `GET /api/approvals?state=waiting` is called
- **THEN** the response is `ApiResponse[ApprovalSummary[]]` containing only actions in `pending` state, ordered `created_at DESC`.
- **WHEN** `GET /api/approvals?state=decided` is called
- **THEN** the response contains actions in `approved | rejected | expired | executed` states.
- **WHEN** `GET /api/approvals?state=all` is called or `state` is omitted
- **THEN** all states are included.

### Requirement: Approval Detail API

The dashboard SHALL expose `GET /api/approvals/{id}` returning the full dossier for one approval.

#### Scenario: Detail response shape

- **WHEN** `GET /api/approvals/{id}` is called
- **THEN** the response is `ApiResponse[ApprovalDetail]` with fields `id`, `title`, `butler`, `created_at` (alias `ts`), `expires_at` (alias `expires`), `why` (string | null — serif paragraph), `evidence` (string[] | null — mono lines), `proposed_action` (object describing the tool call being approved), `session_id` (string | null — the originating session/trace, when known).
- **AND** when `why` or `evidence` is null (legacy row), the UI renders a serif-italic empty state for the missing section.
- **AND** when `session_id` is present, the dossier header links to `/sessions/{session_id}` so the owner can inspect the originating session/trace before deciding.

### Requirement: Approval Verbs

The dashboard SHALL expose explicit verb endpoints for approve, deny, and defer.

#### Scenario: Approve with optional edits

- **WHEN** `POST /api/approvals/{id}/approve {edits?: object}` is called
- **THEN** the action is approved with any supplied `edits` applied to its arguments
- **AND** `audit.append("approval.approve", target=action_id, note=json.dumps(edits))` is invoked
- **AND** the underlying tool is executed via the shared executor (existing module-approvals behavior).

#### Scenario: Deny with reason

- **WHEN** `POST /api/approvals/{id}/deny {reason?: str}` is called
- **THEN** the action transitions to `rejected`
- **AND** `audit.append("approval.deny", target=action_id, note=reason)` is invoked.

#### Scenario: Defer with bounded hours

- **WHEN** `POST /api/approvals/{id}/defer {hours: int}` is called
- **THEN** the call is rejected with `422` unless `1 ≤ hours ≤ 168`
- **AND** on success, the action's `expires_at` is extended by `hours` and the notification re-presentation timer is reset to `now + hours`
- **AND** `audit.append("approval.defer", target=action_id, note=str(hours))` is invoked.

### Requirement: Post-Approval Teaching Digest

After a successful approval, `/approvals` SHALL offer a short, inline opportunity to create a standing rule for the approved action. Approval itself has no implicit rule-creation side effect.

#### Scenario: Show a redacted proposed scope after approval

- **WHEN** `POST /api/approvals/{id}/approve` succeeds
- **THEN** the dashboard fetches `GET /api/approvals/rules/suggestions/{id}`
- **AND** displays "Approved. Always allow this shape?" with the redacted proposed constraints.

#### Scenario: A standing rule requires a second confirmation

- **WHEN** the owner selects "Always allow this shape"
- **THEN** the dashboard presents a distinct "Create standing rule" confirmation
- **AND** only that confirmation calls `POST /api/approvals/rules/from-action` with the action ID.

#### Scenario: Keep asking does not mutate autonomy

- **WHEN** the owner selects "Keep asking" or dismisses the digest
- **THEN** no standing-rule mutation is sent.

#### Scenario: Proposed scope is unavailable

- **WHEN** the rule-suggestion preview cannot be fetched
- **THEN** the dashboard reports that no rule was created
- **AND** does not offer a create action based on an unknown scope.

### Requirement: Approvals Policy (Quiet Hours)

The dashboard SHALL expose `GET/PUT /api/approvals/policy` to manage notification quiet hours.

#### Scenario: Read policy

- **WHEN** `GET /api/approvals/policy` is called
- **THEN** the response is `ApiResponse[ApprovalsPolicy]` with `quiet_start_hour: int` (0–23), `quiet_end_hour: int` (0–23), `timezone: str` (IANA).

#### Scenario: Update policy

- **WHEN** `PUT /api/approvals/policy` is called with the same shape
- **THEN** the singleton row is updated and `audit.append("approvals.policy")` is invoked.

#### Scenario: Quiet hours defer a routine owner-default notification

- **WHEN** the notification dispatcher handles a routine implicit-owner `send`
  or `insight` call with priority other than `high`
- **AND** the current local hour is within the inclusive policy window
- **THEN** it parks the full envelope in the originating schema's
  `deferred_notifications` table for the first whole hour after quiet hours
- **AND** it returns the established `deferred` result rather than silently
  dropping the page
- **AND** high-priority and explicit-target notifications retain their existing
  immediate behavior

#### Scenario: Approval-request pushes retain their dedicated behavior

- **WHEN** an approval gate emits an `approval_request` push during quiet hours
- **THEN** its existing decision-loop deferral behavior and pending-action
  expiry semantics remain unchanged
- **AND** it is not reclassified as a routine `send` or `insight` hold

### Requirement: Approvals Live Stream

The dashboard SHALL fan approval lifecycle events onto the unified fleet event bus (`WS /api/events/stream`) (the earlier dedicated `WS /api/approvals/stream` route was retired in bu-01r64.2 once the bus fully covered this traffic).

#### Scenario: Stream event shape

- **WHEN** an approval transitions state
- **THEN** an event `{type: "approval", data: {kind: "created"|"approved"|"rejected"|"deferred"|"executed"|"expired", approval_id, ...}}` is broadcast on `WS /api/events/stream`.

---

### Requirement: Promotion Suggestions API Endpoint

The dashboard API SHALL expose `GET /api/approvals/suggestions` which returns a list of autonomy promotion and demotion suggestions.

The endpoint SHALL accept the following query parameters:
- `status` (string, optional, default `pending`) -- filter by suggestion status: `pending`, `confirmed`, `dismissed`, `superseded`, or `all`
- `suggestion_type` (string, optional) -- filter by type: `promotion` or `demotion`
- `limit` (integer, optional, default 20) -- maximum number of suggestions to return
- `offset` (integer, optional, default 0) -- pagination offset

The response MUST be a `PaginatedResponse<AutonomySuggestion>` where each `AutonomySuggestion` object contains:
- `id` -- string UUID identifying the suggestion
- `action_id` -- optional string UUID of the originating approval action, when retained
- `suggestion_type` -- `"promotion"` or `"demotion"`
- `pattern_fingerprint` -- string hash identifying the action pattern
- `tool_name` -- string name of the tool
- `representative_args` -- object with the exact tool arguments this suggestion covers
- `scope_description` -- string human-readable description of what the proposed rule would auto-approve
- `status` -- one of `"pending"`, `"confirmed"`, `"dismissed"`, `"superseded"`
- `approval_count_at_creation` -- integer number of approvals that triggered this suggestion
- `created_at` -- ISO 8601 timestamp
- `decided_at` -- ISO 8601 timestamp (null if pending)
- `decided_by` -- string identifier (null if pending)
- `resulting_rule_id` -- string UUID of rule created on confirmation (null otherwise)
- `velocity` -- object (nullable) containing `avg_seconds`, `sample_count`, `fast_approval` from the velocity tracker

#### Scenario: Fetch pending promotion suggestions

- **WHEN** `GET /api/approvals/suggestions?status=pending&suggestion_type=promotion` is called
- **THEN** the API MUST return all pending promotion suggestions sorted by `created_at DESC`
- **AND** each suggestion MUST include `scope_description` and `velocity` data
- **AND** the response status MUST be 200

#### Scenario: Fetch pending demotion suggestions

- **WHEN** `GET /api/approvals/suggestions?status=pending&suggestion_type=demotion` is called
- **THEN** the API MUST return all pending demotion suggestions
- **AND** each suggestion MUST include the error details from the failed execution in metadata

#### Scenario: No pending suggestions

- **WHEN** `GET /api/approvals/suggestions?status=pending` is called and none exist
- **THEN** the API MUST return an empty array with response status 200

### Requirement: Suggestion Confirmation API Endpoint

The dashboard API SHALL expose `POST /api/approvals/suggestions/{suggestionId}/confirm` to confirm a promotion or demotion suggestion.

#### Scenario: Confirm a promotion suggestion via API

- **WHEN** `POST /api/approvals/suggestions/{suggestionId}/confirm` is called with authenticated user context
- **THEN** the API MUST invoke `confirm_promotion_suggestion` on the approvals module
- **AND** the response MUST include the created `rule_id` on success
- **AND** the response status MUST be 200

#### Scenario: Confirm without authentication

- **WHEN** `POST /api/approvals/suggestions/{suggestionId}/confirm` is called without authentication
- **THEN** the response status MUST be 401

### Requirement: Suggestion Dismissal API Endpoint

The dashboard API SHALL expose `POST /api/approvals/suggestions/{suggestionId}/dismiss` to dismiss a promotion or demotion suggestion. The request body MAY include an optional `reason` string.

#### Scenario: Dismiss a suggestion via API

- **WHEN** `POST /api/approvals/suggestions/{suggestionId}/dismiss` is called with `{"reason": "Not needed"}` and authenticated user context
- **THEN** the API MUST invoke `dismiss_promotion_suggestion` on the approvals module
- **AND** the response status MUST be 200

#### Scenario: Dismiss without reason

- **WHEN** `POST /api/approvals/suggestions/{suggestionId}/dismiss` is called with no body
- **THEN** the dismissal MUST proceed with `reason` as `null`

### Requirement: Autonomy Suggestions Dashboard Section

The approvals dashboard page at `/approvals` SHALL include an "Autonomy Suggestions" section displayed above the two-pane approvals body (the pending-approvals rail and dossier) when pending suggestions exist. The prior "actions table" layout referenced here has graduated to the Dispatch two-pane dossier, so the section sits between the page header and that body.

#### Scenario: Promotion suggestion card displayed

- **WHEN** pending promotion suggestions exist
- **THEN** the dashboard MUST display a card for each suggestion containing:
  - The tool name
  - The human-readable scope description (e.g., "Auto-approve send_telegram when chat_id = 'mom_123' AND text = 'Good morning'")
  - The number of times this exact action was manually approved
  - Approval velocity indicator (fast/normal)
  - "Confirm rule" and "Dismiss" action buttons
- **AND** the card MUST visually emphasize that the rule scope is exact-match only

#### Scenario: Demotion suggestion card displayed

- **WHEN** pending demotion suggestions exist
- **THEN** the dashboard MUST display a warning card for each demotion containing:
  - The tool name and rule description
  - The execution error summary
  - "Revoke rule" and "Keep rule" action buttons
- **AND** the card MUST use a warning/alert visual style

#### Scenario: Suggestion links back to its source approval

- **WHEN** an autonomy suggestion includes `action_id`
- **THEN** its card MUST offer a link to `/approvals/{action_id}` so the owner can review the originating dossier.

#### Scenario: No pending suggestions hides section

- **WHEN** no pending suggestions exist
- **THEN** the autonomy suggestions section MUST NOT be rendered

#### Scenario: Confirm suggestion from card

- **WHEN** a user clicks "Confirm rule" on a promotion suggestion card
- **THEN** the dashboard MUST call `POST /api/approvals/suggestions/{id}/confirm`
- **AND** the card MUST be removed from the suggestions section on success
- **AND** a success toast MUST indicate the new standing rule was created

#### Scenario: Dismiss suggestion from card

- **WHEN** a user clicks "Dismiss" on a promotion suggestion card
- **THEN** the dashboard MAY show an optional reason input
- **AND** MUST call `POST /api/approvals/suggestions/{id}/dismiss`
- **AND** the card MUST be removed from the suggestions section on success

### Requirement: Rule Promotion Suggestions API Endpoint

The dashboard API SHALL expose `GET /api/switchboard/rule-promotion-suggestions`, returning a paginated list of switchboard ingestion-rule promotion and demotion suggestions. This is a distinct endpoint namespace from `GET /api/approvals/suggestions` (autonomy tool-call suggestions) — the two suggestion families track different underlying tables (`switchboard.rule_promotion_suggestions` vs `autonomy_suggestions`) and are not merged into one response shape, though both render through the dashboard's approvals-surface visual language.

The endpoint SHALL accept query parameters `status` (default `pending_review`), `is_clearly_automated` (optional boolean filter), `limit` (default 20), `offset` (default 0).

Each returned suggestion object MUST include: `id`, `sender_key`, `source_channel`, `proposed_rule_type`, `proposed_condition`, `proposed_action`, `evidence_count`, `first_evidence_at`, `last_evidence_at`, `is_clearly_automated`, `status`, `created_rule_id`, `created_at`, `decided_at`, `decided_by`.

#### Scenario: Fetch pending rule promotion suggestions

- **WHEN** `GET /api/switchboard/rule-promotion-suggestions?status=pending_review` is called
- **THEN** the API MUST return all pending suggestions sorted by `evidence_count DESC, last_evidence_at DESC`
- **AND** the response status MUST be 200

#### Scenario: No pending suggestions

- **WHEN** `GET /api/switchboard/rule-promotion-suggestions?status=pending_review` is called and none exist
- **THEN** the API MUST return an empty array with response status 200

### Requirement: Rule Promotion Suggestion Confirm/Bulk-Confirm/Dismiss Endpoints

The dashboard API SHALL expose:
- `POST /api/switchboard/rule-promotion-suggestions/{id}/confirm` — confirms a single suggestion, creating the corresponding `ingestion_rules` row.
- `POST /api/switchboard/rule-promotion-suggestions/bulk-confirm` — accepts a list of suggestion ids and confirms each independently, reporting per-id success/failure rather than failing the whole batch on one error. Intended for the batched automated-sender confirm affordance; the API MUST NOT skip the confirm requirement for any id in the batch (see `switchboard-rule-promotion` spec, "Owner-Confirmed Promotion").
- `POST /api/switchboard/rule-promotion-suggestions/{id}/dismiss` — accepts an optional `{"reason": string}` body, sets `cooldown_until`, transitions to `dismissed`.

All three endpoints require authenticated human actor context.

#### Scenario: Confirm a single suggestion

- **WHEN** `POST /api/switchboard/rule-promotion-suggestions/{id}/confirm` is called with authenticated context on a `pending_review` suggestion
- **THEN** the response MUST include the created `rule_id`
- **AND** the response status MUST be 200

#### Scenario: Bulk-confirm reports per-item results

- **WHEN** `POST /api/switchboard/rule-promotion-suggestions/bulk-confirm` is called with 5 suggestion ids, one of which is already `dismissed`
- **THEN** the response MUST indicate 4 successful confirmations and 1 per-item failure, not a single all-or-nothing error
- **AND** the response status MUST be 200 for the batch call itself (individual failures are reported in the body, not via HTTP status)

#### Scenario: Dismiss with reason

- **WHEN** `POST /api/switchboard/rule-promotion-suggestions/{id}/dismiss` is called with `{"reason": "Sender's routing target changed"}`
- **THEN** the suggestion MUST transition to `dismissed` with the reason recorded

### Requirement: Rule Promotion Suggestions Dashboard Section

The approvals dashboard page SHALL include a "Rule Promotion" section, visually consistent with the existing Autonomy Suggestions section but rendering `switchboard.rule_promotion_suggestions` data, displayed when pending suggestions exist.

`route_to` suggestions MUST render as individual cards showing: the sender identity (`sender_key`), proposed target butler, evidence count, and first/last evidence dates, with "Confirm" and "Dismiss" actions per card.

`is_clearly_automated = TRUE` suggestions with `proposed_action` in (`skip`, `metadata_only`) MUST render grouped, with a single "Confirm all N" batched action calling the bulk-confirm endpoint, alongside the ability to expand and dismiss individual senders from the group before confirming the rest.

Demotion suggestions (rules flagged via spot-check drift) MUST render with a warning/alert visual style distinct from promotion cards, showing the rule's current scope description and the recent spot-check disagreement rate, with "Revoke rule" and "Keep rule" actions.

#### Scenario: Route-to suggestion card displayed individually

- **WHEN** a pending `route_to` suggestion exists
- **THEN** the dashboard MUST display it as its own card, not grouped with other suggestions

#### Scenario: Automated senders grouped with batch confirm

- **WHEN** 9 pending suggestions exist, all `is_clearly_automated=TRUE` with `proposed_action='skip'`
- **THEN** the dashboard MUST display them grouped under a single "Confirm all 9 automated senders" action

#### Scenario: Confirming from the dashboard calls the API

- **WHEN** a user clicks "Confirm" on an individual rule-promotion suggestion card
- **THEN** the dashboard MUST call `POST /api/switchboard/rule-promotion-suggestions/{id}/confirm`
- **AND** the card MUST be removed from the section on success with a success toast naming the new rule

#### Scenario: No pending suggestions hides the section

- **WHEN** no pending rule-promotion or demotion suggestions exist
- **THEN** the Rule Promotion section MUST NOT be rendered

### Requirement: Rule Promotion Metrics Endpoint and Tile

The dashboard SHALL expose a rule-promotion metrics endpoint `GET /api/switchboard/rule-promotion-stats` returning aggregate counts over all time: promotion-suggestion lifecycle counts (`suggestions_pending`, `suggestions_confirmed`, `suggestions_dismissed`, `suggestions_superseded`), live promoted rules (`promoted_rules_active`, meaning `ingestion_rules` with `created_by='promotion'`, `enabled`, not soft-deleted), events those rules routed without an LLM session (`promoted_rule_matches`, meaning `routing_verdict_log` rows with `verdict_source='rule'` matched to a promoted rule), an `llm_sessions_avoided_estimate`, the demotion drift signal (`demotion_pending`, meaning pending `suggestion_kind='demotion'` suggestions), and the spot-check sample count backing the agreement scores (`promoted_rule_spot_checks`).

`llm_sessions_avoided_estimate` SHALL equal `promoted_rule_matches`: one event routed by a promoted rule is one spawned-session LLM round-trip removed. It MUST be labelled an estimate in the UI (it counts matches since promotion, not a counterfactual replay).

The endpoint SHALL compute each block with an independent sub-query and follow the degraded-envelope convention: a failed sub-query leaves its fields at 0 AND adds its source name (`suggestion_counts`, `promoted_rules`, or `verdict_metrics`) to `meta.sources_degraded`, so a failed query never renders as a truthful zero.

The approvals dashboard SHALL render a rule-promotion metrics tile from this endpoint when there is any promotion history to measure, or whenever a source is degraded. A degraded block SHALL render an inline degraded note naming the unavailable source rather than a fabricated zero.

#### Scenario: Metrics reported for promoted rules

- **WHEN** promoted rules have routed events recorded in `routing_verdict_log` with `verdict_source='rule'`
- **THEN** `GET /api/switchboard/rule-promotion-stats` returns `promoted_rule_matches` equal to that count
- **AND** `llm_sessions_avoided_estimate` equals `promoted_rule_matches`

#### Scenario: A failed sub-query is flagged, not zeroed silently

- **WHEN** the verdict-log sub-query raises
- **THEN** the response still returns 200 with the other blocks computed
- **AND** `meta.sources_degraded` contains `verdict_metrics`
- **AND** the tile renders a degraded note for the savings block instead of showing zero sessions avoided

#### Scenario: Sessions-avoided is labelled an estimate

- **WHEN** the metrics tile renders the sessions-avoided figure
- **THEN** the tile MUST label it an estimate rather than an exact measured count

## Source References

- PLAN.md §5 `/approvals` API surface and §6 Phase 6 implementation order.
- Visual reference: the `ApprovalsPage` redesign prototype (graduated; now shipped in `frontend/`).
- Reuses `audit.append()` from dashboard-audit-log on every mutation.
