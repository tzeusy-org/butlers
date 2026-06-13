## MODIFIED Requirements

### Requirement: Pending Action Schema with Why and Evidence
The approvals module SHALL extend `pending_actions` with two columns that the agent fills at action-creation time and the dashboard renders on the approval detail page.

#### Scenario: Schema columns
- **WHEN** the `pending_actions` table is migrated
- **THEN** column `why TEXT` is added (nullable)
- **AND** column `evidence JSONB DEFAULT '[]'::jsonb` is added (NOT NULL after backfill)
- **AND** legacy rows have `why = NULL` and `evidence = '[]'::jsonb`.

#### Scenario: Agent fills why and evidence
- **WHEN** an LLM session triggers a gated tool that creates a `pending_actions` row
- **THEN** the gate wrapper records:
  - `why` — a single serif paragraph (one sentence preferred, ≤ 50ch in rendering) explaining why human input is required. If the LLM omits this, the wrapper leaves `why = NULL` and logs a `WARNING`.
  - `evidence` — an array of mono strings: log excerpts, IDs, file paths, links. Empty array if not applicable.
- **AND** the wrapper enforces `len(why) ≤ 2000 chars` and `len(evidence) ≤ 50 items, each ≤ 500 chars`; oversize values are truncated with a `…` suffix and a `WARNING` log.

#### Scenario: UI tolerates legacy null
- **WHEN** the dashboard renders an approval detail with `why = NULL` or `evidence = []`
- **THEN** the missing section is replaced by a single serif-italic line "No rationale recorded." or "No evidence cited." respectively; the rest of the dossier renders normally.

### Requirement: Defer Re-Presentation
The approvals module SHALL support deferring a pending action's re-presentation by a bounded number of hours.

#### Scenario: Defer action lifecycle
- **WHEN** `POST /api/approvals/{id}/defer {hours}` is called with `1 ≤ hours ≤ 168`
- **THEN** the action's `expires_at` is set to `max(current_expires_at, now + hours)` (defer cannot shorten an existing expiry)
- **AND** the notification dispatcher's next-presentation timer for this action is reset to `now + hours`
- **AND** the action remains in `pending` state until manually approved/rejected, deferred again, or expired.

#### Scenario: Defer beyond max hours rejected
- **WHEN** the defer endpoint is called with `hours > 168` or `hours < 1`
- **THEN** the response is `422 Unprocessable Entity` with body `{error: "hours_out_of_range"}`
- **AND** no state change occurs.

## Source References
- PLAN.md §5 `/approvals` data shape (`why`, `evidence`, `proposed_action`) and §6 Phase 6.
- Existing module-approvals capability spec for gate wrapper, executor, and standing rule semantics.
- The `defer` verb is a notification-dispatcher hook; it does NOT bypass the gate or auto-execute.
