# Design — Calendar mutation audit-trail read + undo

## Context

The calendar module already journals every workspace mutation into
`calendar_action_log` (DDL in migration `core_003`): `idempotency_key`,
`request_id`, `action_type`, `action_status` (`pending`/`applied`/`failed`/`noop`),
`action_payload` (JSONB), `action_result` (JSONB), `error`, `created_at`,
`applied_at`. The only reader today is `_load_projection_action`, which powers
idempotent replay in `_prepare_workspace_mutation`. The table is otherwise
write-only — there is no HTTP surface, so the agent's writes are invisible to the
owner.

Authorship provenance already exists on a sibling table: `calendar_events` gained
`source_butler` (NOT NULL) and `source_session_id` (nullable) in migration
`core_076`. `calendar_action_log.event_id` FKs `calendar_events(id)`, so an audit
row can join to that provenance.

On update, the handler already fetches the pre-mutation `existing_event` from the
provider before patching (`calendar.py` ~3127). That pre-image is exactly what an
inverse needs — but `action_result` currently stores only the post-mutation
outcome, so it is discarded.

## Decisions

### D1 — Store pre-state in `action_result`, not a new column

`action_result` is already `JSONB`. Capturing the pre-mutation event state under a
stable key (e.g. `pre_state`) inside `action_result` makes the inverse
reconstructable with **no migration**. Rejected alternative: a dedicated
`pre_state` column — it would require a `core_*` migration for data the JSONB
column already accommodates, against the single-owner "prefer cruft cleanup over
compat" doctrine.

The update path reuses the already-fetched `existing_event` (zero extra provider
calls). The delete path adds one pre-image fetch before deletion (the only new
provider round-trip introduced by this change, and only on delete).

### D2 — Undo dispatches the existing tools, not a new MCP tool

The undo endpoint synthesizes an inverse and dispatches it through the **existing**
`calendar_create_event` / `calendar_update_event` / `calendar_delete_event` MCP
tools, with a freshly generated `request_id`:

- inverse of `workspace_user_update` → `calendar_update_event` restoring
  `pre_state`
- inverse of `workspace_user_delete` → `calendar_create_event` from `pre_state`
- inverse of `workspace_user_create` → `calendar_delete_event` against the created
  event id

This keeps undo idempotent (its own `request_id`), keeps it inside the existing
audited/permission-gated write path, and leaves the spec's normative "16 MCP tools
total" pin untouched. Rejected alternative: a `calendar_undo_event` MCP tool —
unnecessary surface area and a spec-pin change for behavior fully expressible via
the existing tools.

### D3 — Fail-fast undo, never guess

Undo refuses to act unless it can reconstruct a faithful inverse:

1. **Unknown `action_id`** → 404.
2. **Not `applied`** (`pending`/`failed`/`noop`) → 409 naming the status; only an
   applied mutation has an effect to reverse.
3. **`applied` but `pre_state` missing/expired** (logged before capture, or the
   event no longer exists to restore against) → 422 with diagnostic context
   (action_id, action_type, reason).
4. **Already undone** (an inverse for this action was already dispatched and
   recorded) → 409.

On a single-owner calendar, silently materializing a guessed inverse could write a
wrong event or a wrong restore, so the contract is "fail fast with diagnostics"
over "best-effort guess." This mirrors the quick-add change's degraded contract
(no fabricated event).

### D4 — Audit read fans out over calendar-owning butlers only

The audit-read query follows the existing `query_calendar_workspace` read-model
boundary: fan out across `butlers_with_module("calendar")`, join
`calendar_action_log` to `calendar_events` for `source_butler` /
`source_session_id`, order `created_at DESC`, bound by `limit`/`cursor`. This
respects the inter-butler MCP-only doctrine (the dashboard read-model is the
sanctioned cross-butler read surface, not direct cross-schema access from a
butler).

## Risks / Trade-offs

- **Pre-state only for go-forward rows.** Actions logged before D1 ships have no
  `pre_state`; undo on them fails fast (422) rather than guessing. Acceptable:
  undo is a new affordance, not a retroactive guarantee.
- **Delete adds one provider fetch.** Capturing the delete pre-image costs one
  `get_event` before deletion. Bounded to the delete path and required for
  reversibility.
- **Undo of an externally-changed event.** If the user edited the event on Google
  after the logged mutation, restoring `pre_state` overwrites that edit. This is
  the same authoritativeness model the workspace already uses for butler-owned
  events; the audit row's timestamps let the UI warn before undo.

## Test Strategy

- Unit: update logs `pre_state`; delete logs `pre_state`; create/noop/failed omit
  it; idempotent-replay contract unchanged.
- Unit: audit feed orders newest-first, surfaces `source_butler` /
  `source_session_id`, falls back when no joined event, empty/absent table →
  HTTP 200 empty list.
- Unit: undo update/delete/create happy paths dispatch the correct inverse tool
  with a fresh `request_id` and are recorded; non-applied → 409; missing pre-state
  → 422 with diagnostics; unknown id → 404; already-undone → 409.
- Integration (fake provider): logged update → undo restores pre-state on the home
  calendar; logged delete → undo recreates the event.
