# Context-Bus Producers: Light the Bus with Deterministic Signals

## Why

For 3.5 months `public.user_context` held **zero rows** while three hardened
consumers read it every day: the notify dnd/sleeping deferred-delivery gate
(`core_tools/_notifications.py`), every spawned session's situational preamble
(`core/spawner_context.py`), and the attention-ledger context reasons
(`core/attention_ledger.py`). The read side was fully wired; nothing ever wrote a
signal. `set_context`/`clear_context` had no call sites repo-wide.

The context-bus spec (RFC 0009) named the *writers* in its permission matrix but
defined **no producer requirements** — which is exactly how the empty table went
unnoticed. The vision-level "shared situational awareness" claim existed only as
consumer plumbing: health check-ins still fired during meetings, and the shipped
sleeping deferred-delivery gate never triggered.

Producers are pure infrastructure at zero LLM spend — the highest
leverage-per-token move in the collaboration fabric, and the step that makes
already-shipped honesty machinery real.

## What Changes

- **calendar → meeting/focused** (writer `general`): a deterministic,
  `dispatch_mode="job"` producer reads the currently-active event from the
  general butler's `calendar_events` and publishes `meeting` (or `focused` for a
  focus-block title), so spawned-session preambles go non-empty immediately.
- **home → at_home** (writer `home`): from fresh `person.*`/`device_tracker.*`
  Home Assistant presence; a stale snapshot never asserts presence.
- **travel → traveling** (writer `travel`): from a currently-underway trip.
- **health → sleeping** (writer `health`): from the owner-declared quiet-hours
  window (`public.approvals_policy`), activating the shipped notify
  sleeping deferred-delivery gate.
- **Explicit `dnd`/`sick`** via new `check_context`/`set_context`/`clear_context`
  MCP tools on the general module (user-initiated context no producer can
  infer).
- **Spec**: adds producer SHALL-requirements to the context-bus capability
  (previously producer-silent), plus the explicit-tool requirement.

Single-writer discipline per source (RFC 0009 writer matrix), idempotent upserts
with bounded TTL/expiry so a crashed producer never leaves context permanently
pinned, and clear-on-reverse-transition.

## Impact

- Affected spec: `context-bus` (adds producer + explicit-tool requirements).
- Affected code: new `src/butlers/jobs/context_producers.py`; registry wiring in
  `src/butlers/scheduled_jobs.py`; schedules in general/home/travel/health
  `butler.toml`; general module MCP tools.
- No schema migration — the `public.user_context` schema already supports every
  field the producers write.
