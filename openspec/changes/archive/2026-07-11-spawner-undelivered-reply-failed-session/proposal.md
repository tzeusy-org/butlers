# Spawner: Undelivered Interactive Reply is a Failed Session

## Why

PR #2693 (`feat(spawner): flag undelivered interactive replies as failed
sessions`) introduces a THIRD spawner session outcome that the normative
`core-spawner` spec does not model.

Today the spec recognises exactly two outcomes for a runtime invocation:

- **Successful session** (`core-spawner/spec.md`, "Successful session"): the
  runtime invocation completes successfully, so `session_complete(success=True)`.
- **Failed session** (`core-spawner/spec.md`, "Failed session - spawner fallback
  dispatch"): the runtime invocation raises an exception, so
  `session_complete(success=False)` AND the self-healing fallback dispatcher
  fires.

The real incident (`Dr-Ng-followup`) does not fit either. An interactive routed
message (a Telegram/WhatsApp message that instructs the runtime the user expects
a reply via `notify()`) ran to clean completion, but every `notify()` attempt
failed to deliver (a schema rejection left a null result, or the call returned a
non-delivering status). The runtime returned normally, so the session was logged
as a `success=True` even though the user received nothing.

PR #2693 closes this gap in code: on the normal-completion path the spawner
detects a route-triggered interactive session that attempted `notify()` but
delivered nothing, and persists that session row with `success=False` plus a
reason. Crucially this runs on the NON-raising path, so it does NOT trigger
same-tier failover or the self-healing fallback dispatcher (this is not a crash;
healing the runtime would not have helped). The in-memory `SpawnerResult.success`
stays `True` so memory extraction and the route reply flow are unaffected; only
the persisted session record reflects the undelivered outcome.

Butlers is OpenSpec-driven (doctrine: the daemon is deterministic
infrastructure, so its recorded session outcomes must be specified and honest).
This change amends `core-spawner` so the new "completed-but-undelivered =>
success=False" outcome is doctrinally sanctioned and drift does not recur.

## What Changes

- **Amend the "Successful session" scenario** under the `Spawner Session
  Lifecycle` requirement so that clean runtime completion no longer
  unconditionally means `session_complete(success=True)`: the delivery-accounting
  check can override the persisted record to `success=False`.
- **Add an `Interactive Reply Delivery Accounting` requirement** to
  `core-spawner` capturing the new outcome:
  - WHEN a runtime invocation completes successfully BUT a route-triggered
    interactive session attempted `notify()` and none of the attempts delivered,
    the persisted session row is recorded with `success=False` and a reason.
  - This is detected on the normal-completion path, NOT via a raised exception,
    so NO same-tier failover and NO self-healing fallback dispatch fires. This is
    the explicit difference from the "Failed session - spawner fallback dispatch"
    scenario, which DOES heal.
  - `SpawnerResult.success` stays `True` (route flow + memory extraction
    unaffected); only the persisted session record carries `success=False`.
- **Define the delivered-status set explicitly.** Delivered =
  `{ok, deferred}`. A `notify()` tool-call counts as delivered only when its
  captured result is a dict whose `status` is `ok` or `deferred`. Every other
  outcome is undelivered: `suppressed_quiet_hours`, `pending_approval`,
  `pending_missing_identifier`, `error`, an `outcome="error"` record, or a record
  with no result dict at all (the schema-rejection / null-result incident shape).
- **Conservative scope guards** (to avoid false positives): only
  `trigger_source == "route"` sessions; only interactive source channels
  (`telegram_bot`, `whatsapp`); a session that made zero notify attempts is left
  alone; if any single notify attempt delivered, the session is not flagged.

### Decision: `suppressed_quiet_hours` counts as UNDELIVERED

The `notify()` status vocabulary distinguishes two quiet-hours outcomes, and they
are NOT symmetric:

- `deferred` (DELIVERED): the notification is persisted into the deferred queue
  with a concrete `deliver_at` timestamp. It is a successful delivery decision -
  the user WILL receive the message later. Counting it as delivered is correct.
- `suppressed_quiet_hours` (UNDELIVERED): the owner page is DROPPED entirely. No
  queue entry, no `deliver_at`, no later delivery. The user receives nothing, so
  for an interactive reply that is exactly the silent-failure this change exists
  to catch.

This is safe in practice because both quiet-hours gates in `notify()` are
intent-restricted to `{send, insight}` and never fire on `reply` / `react`. An
interactive reply (`intent="reply"`) therefore cannot itself produce
`suppressed_quiet_hours`; but if a butler responds to an interactive message with
an `intent="send"`/`insight` call that gets suppressed, treating that as
undelivered is the honest outcome (the user still received no reply). Hence the
delivered set is `{ok, deferred}` and `suppressed_quiet_hours` is undelivered.

This matches PR #2693's code exactly: `_DELIVERED_NOTIFY_STATUSES =
frozenset({"ok", "deferred"})` in `src/butlers/core/spawner_guardrails.py`.

## Capabilities

### Modified Capabilities

- `core-spawner`: the "Successful session" scenario is amended so clean
  completion can still persist `success=False` via delivery accounting; a new
  `Interactive Reply Delivery Accounting` requirement defines the
  completed-but-undelivered outcome, the delivered/undelivered status set, the
  no-healing rule, and the `SpawnerResult.success`-stays-True invariant.

## Impact

- **Spec:** `core-spawner` only.
- **Code (already authored in PR #2693, not part of this change):**
  - `src/butlers/core/spawner_guardrails.py` -
    `_check_undelivered_interactive_reply`, `_is_notify_call`,
    `_notify_call_delivered`, `_extract_source_channel`,
    `_DELIVERED_NOTIFY_STATUSES`.
  - `src/butlers/core/spawner.py` - the normal-completion-path call that sets the
    persisted `session_complete(success=..., error=...)` while leaving
    `SpawnerResult.success=True`.
  - `tests/core/test_spawner_guardrails.py` - coverage for the new check.
- **No new migrations, no new deliverable channels, no API surface changes.**

## Source References

- Non-Negotiable Rule 4 (the daemon is deterministic infrastructure; its recorded
  session outcomes must be testable, predictable, and honest).
- Non-Negotiable Rule 7 (transport is connector responsibility; the spawner reads
  the captured routing context's source channel, it does not learn transport
  details).
