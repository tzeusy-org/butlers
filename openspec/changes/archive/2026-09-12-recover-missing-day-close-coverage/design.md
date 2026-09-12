## Context

The briefing availability ledger distinguishes a successfully read missing
coverage witness from an unavailable coverage query. The UI currently renders
Regenerate only for stale cached prose. The refresh HTTP handler contains the
right admission logic but depends on an in-process dispatch callback; the real
dashboard API and Chronicler daemon are separate processes, so that callback is
not wired. The stored scheduled prompt also includes its once-daily `notify`
step, which is correct for cron and wrong for historical recovery.

This change supersedes only the stale-only regeneration constraint recorded in
the completed but still unarchived `bind-day-close-cache-timezone` design. Its
exact `(date, timezone)` identity, legacy-cache, locking, and no-automatic-
backfill decisions remain authoritative.

## Goals / Non-Goals

**Goals:**

- Make the existing explicit LLM-bearing recovery action work for stale cache
  and proven missing-witness states.
- Keep availability failures fail-closed and retain exact selected tuple state
  during pending, success, navigation, and failure.
- Put dispatch and Chronicler-owned writes in the running Chronicler daemon.
- Enforce manual-refresh silence independently of model prompt compliance.
- Keep MCP and HTTP results content-blind.

**Non-Goals:**

- No automatic or bundled historical backfill.
- No regeneration for failed/unproven coverage reads, `no_data`, `degraded`,
  unknown, content-without-staleness, today, or future days.
- No change to the normal scheduled day-close notification.
- No database schema, cache identity, admission, retention, or prose change.

## Decisions

### Gate the UI on the typed availability ledger

An unavailable briefing is recoverable only when `coverage_floor` and
`coverage_witness` are both `available` and no ledger entry is `unavailable`.
Missing ledger data fails closed for rolling deployments. This uses the
backend's typed evidence rather than inferring an outage from copy or an empty
payload.

### Execute through a Chronicler-only core control

The dashboard calls a Chronicler-only core MCP tool. `ToolContext` binds that
tool to the exact Chronicler daemon, pool, and dispatcher. A module-level
spawner hook was rejected because modules may not touch core infrastructure and
the hook slot is process-global across concurrently hosted daemons.

The control owns timezone/date validation, settled-day enforcement, tuple rate
limiting, scheduled-prompt lookup, configured complexity, dispatch, writer
admission, witness verification, and safe result shaping. It refuses calls
that carry any runtime session or trigger context, preventing an LLM from
recursively invoking the administrative surface.

### Derive a trusted silent execution context

The control derives `api:day_close_refresh:<date>`; callers cannot supply it.
The prompt explicitly asks for cache narration without notification. At the
MCP wrapper boundary, that exact Chronicler trigger may execute only
`chronicler_day_close_bundle`; every other core or module tool returns a
non-retryable suppressed outcome before its handler runs. This closes direct
notification, reminder, scheduler, child-trigger, routing, and deferred side
paths independently of prompt compliance. Cron uses
`schedule:chronicler_day_close` and remains unchanged.

### Return metadata only

The daemon returns status, cache key/timestamp, quiet, invalid, and invalid
reason. Prompt text, prose, tool calls, bundle content, and provenance never
cross this administrative MCP response. The dashboard maps known error codes
to its existing HTTP envelopes and treats malformed responses as a contained
502.

The Chronicler control bounds its complete operation at 100 seconds, the
dashboard bounds the daemon MCP call at 110 seconds, and the browser bounds the
enclosing HTTP request at 120 seconds. Cancellation therefore begins inside
the owning process before either transport deadline, leaving response delivery
headroom and preventing an unbounded background mutation behind a failed UI
action.

## Risks / Trade-offs

- [Two concurrent refreshes reach the control] -> The single owning Chronicler
  daemon serializes each exact tuple, and a waiter re-checks durable success
  after acquiring the lock before it can dispatch.
- [The model attempts another side-effect tool] -> The MCP wrapper policy
  permits only the bounded bundle read for the trusted manual trigger and
  suppresses every other tool before handler execution.
- [The daemon writes cache but not coverage] -> The control verifies the exact
  witness before returning success. A cache row without authoritative witness
  never rate-limits a later request, which re-runs the canonical evidence read
  instead of promoting cache presence into coverage proof.
- [Other unavailable causes look similar] -> The frontend requires the typed
  successful-read ledger and otherwise omits the action.

## Migration Plan

1. Deploy the dashboard UI/API and daemon control together.
2. Existing stale and unavailable pages remain truthful during rollout; older
   frontends simply do not expose the new recovery state.
3. After deployment, the owner may regenerate each missing settled day
   individually. No backfill is triggered by this change itself.
4. Rollback removes the control and UI affordance; no schema or historic data
   needs reversal.
