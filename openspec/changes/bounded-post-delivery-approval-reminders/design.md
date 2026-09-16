## Context

RFC 0023's source and implementation use a durable action intent, fenced
presentation generations, and provider handoff classes `confirmed`,
`safe_retry`, and `ambiguous`. Its recovery timer is transport-specific: 15
seconds initially, exponential steps, deterministic jitter, and a 15-minute
cap. Those constants are not evidence about owner attention.

The source trace establishes four current facts:

1. `roster/switchboard/tools/notification/deliver.py` registers only
   `telegram` and `email` in `_CHANNEL_DISPATCH`/`SUPPORTED_CHANNELS`.
2. `src/butlers/identity.py::resolve_outbound_channel()` honors an active,
   reachable `prefers-channel`, then falls back in `telegram`, `email` order.
3. Messenger owns a WhatsApp tool, but Switchboard notify and approval recovery
   do not register it end-to-end. Tool ownership alone is not eligibility.
4. `record_engagement_rows()` writes the proactive-insight denominator;
   owner-resolved Switchboard ingress calls `check_and_update_engagement()` and
   `record_owner_ingress_rollup()`. Approval delivery writes none of those
   values.

The active `durable-approval-delivery-intent-recovery` OpenSpec change has not
yet been archived even though its repository implementation was separately
released. This successor therefore defines a new capability and records the
future reconciliation dependency rather than authoring a second whole-body
replacement against a baseline that does not yet exist.

## Goals and non-goals

**Goals:**

- Admit a small, deterministic number of cross-channel reminders only after
  confirmed delivery and complete negative acknowledgement evidence.
- Preserve action safety, expiry, owner-only attribution, quiet hours, burst
  control, and provider ambiguity truth.
- Make missing/stale evidence explicit rather than converting it to silence.
- Give a returning owner one deduplicated, bounded summary of safe-held work.
- Keep approval response analytics independent of proactive insight
  disengagement.

**Non-goals:**

- No new channel, channel provisioning, provider integration, read-receipt
  claim, delivery guarantee, or reachability credential.
- No new action status or transition, automatic decision, expiry extension,
  execution path, or action revival.
- No implementation, migration, backfill, rollout, deployment, credential use,
  provider call, notification, or OpenSpec archive.
- No change to RFC 0023 recovery constants or generic notification
  retry/escalation/history controls.
- No rewrite of the proactive-insight engagement formula.

## Decisions

### D1 - Acknowledgement is explicit, action-bound evidence

The candidate adds an append-only acknowledgement record keyed to action and
presentation generation. A server-derived owner principal is mandatory. A
successful approve/reject/defer, explicit attention acknowledgement, or a
future provider-native verified-owner read receipt can qualify. Provider send
acceptance, dashboard reads/polling, unrelated owner messages, expiry, and
caller-supplied actor text cannot.

An explicit attention acknowledgement is intentionally separate from an
approval decision. It means "I saw this" and changes no action field. A defer
acknowledges the old generation but creates the already-authorized future
generation. If that generation is confirmed, it may start a new attention
episode with only the action's remaining lifetime reminder budget.

The scheduler must read acknowledgement evidence completely. Read failure
becomes `safe_hold(ack_evidence_unavailable)`, not "no acknowledgement".

### D2 - Reachability is four-valued and observational

The states are `reachable`, `degraded`, `unreachable`, and `unknown`. Activity
and channel evidence are fresh for 24 hours. The observational inventory is the
complete set of allowlisted channels with current dispatch registration,
enabled adapter, and active unambiguous owner reachability; it deliberately
does not apply the per-episode unused predicate. In evaluation order,
`reachable` requires fresh, qualified owner activity. `degraded` requires no
fresh activity but a fresh provider confirmation on an inventory channel.
`unreachable` requires a non-empty observation inventory plus fresh definitive
no-effect failure on every member, with no confirmation or ambiguity.
Everything else is `unknown`.

This state does not admit reminders. Reminder authority is narrower: confirmed
prior delivery, complete no-ack evidence, pending/unexpired action, no
ambiguous/in-flight presentation, unused ordinal, and an unused eligible
channel. This separation prevents a product label from becoming egress
authority.

### D3 - Reminder eligibility adds an episode-local predicate

The first four predicates in the RFC's closed intersection -- allowlist,
Switchboard dispatch registry, Messenger adapter availability, and active
unambiguous owner reachability fact -- form the reachability observation
inventory. Reminder eligibility adds the unused episode channel predicate. The
RFC allowlist is exactly `{telegram, email}`. The owner preference is first
only when it remains inside the reminder-eligible intersection, followed by
the existing fixed fallback.

| Channel | Messenger tool | Switchboard notify registry | RFC 0035 eligibility |
| --- | --- | --- | --- |
| Telegram | Yes | Yes | Eligible with active owner reachability. |
| Email | Yes | Yes | Eligible with active owner reachability. |
| WhatsApp | Yes | No | Ineligible. |
| Other/future | Irrelevant | No/currently unknown | Ineligible until amended. |

No private address is persisted in reminder policy state. Registry or identity
read failure yields safe hold. Once admitted, a presentation stays on its
selected channel and uses RFC 0023 recovery. A failure cannot trigger a hidden
channel jump.

### D4 - Two lifetime ordinals at 4 hours and 24 hours

The bound is deliberately small and testable. Each action has at most two
automatic reminder ordinals for its lifetime. A confirmed initial or defer
presentation anchors candidate due instants at +4 hours and +24 hours. A later
defer does not restore spent ordinals.

The budget counts admitted reminder ordinals, including membership collapsed
into a reminder digest. It does not count safe retry attempts on the same key.
Today, a confirmed initial presentation consumes either Telegram or email,
leaving only one distinct eligible channel, so no more than one reminder can be
sent. The second ordinal is future capacity, not permission to repeat a
channel.

The scheduler snapshots no new expiry. It reads the canonical action expiry
under lock. Automatic policy never writes it. If quiet-hours release is at or
after expiry, no reminder is admitted.

### D5 - Reminder admission reuses attention controls, not recovery timing

Reminder admission resolves RFC 0021 quiet hours once and stores the exact
end-of-window release. It participates in the same per-schema ten-minute burst
window. The first three attention presentations are direct, the fourth creates
one cohort digest on a channel eligible for the creator action. A later action
joins only if that digest channel is also eligible and unused for that action
under its locked snapshot. An incompatible member enters
`safe_hold(burst_digest_channel_incompatible)` without membership, ordinal
consumption, direct send, or a second digest; an incomplete inventory uses
`channel_inventory_unavailable`. Compatible cohort membership consumes the
action's ordinal even though it creates no direct send.

RFC 0023 recovery begins only after a presentation exists. It keeps the same
key and channel across safe retries. An ambiguous post-start effect permits
same-key reconciliation only and puts the action episode in safe hold. A
policy sweep cannot manufacture another channel/key to escape it.

### D6 - Safe hold is attention state

`safe_hold` lives beside the delivery intent with a closed reason. It is not
added to `ActionStatus`. This avoids two competing owners for action lifecycle.
The existing expiry writer still changes `pending` to `expired`; authenticated
decision and executor paths stay unchanged.

Safe hold is monotonic for send authority. Later acknowledgement can improve
the read projection to acknowledged, but it cannot restore an automatic
reminder. Expiry does not erase the hold evidence.

### D7 - Homecoming is one bounded epoch summary

A singleton owner-presence record serializes qualified activity. A gap of at
least 24 hours creates a new epoch; otherwise activity updates the current
epoch. A unique epoch foreign key allows at most one homecoming summary.

Membership is limited to pending safe-held actions plus safe-held actions that
expired within 7 days and have not appeared in a confirmed homecoming summary.
It is capped at 20 rows plus an overflow count. Expired rows are non-actionable.
Ambiguous actions are excluded from outbound content until reconciliation so
homecoming cannot become an ambiguity bypass.

The activity channel is used only if eligible; otherwise normal eligible order
applies. Quiet hours and burst counting still apply. Restart and concurrent
activity replay the epoch key.

### D8 - Disengagement keeps its actual denominator

Approval reminder delivery is not an insight. The successor adds no
`insight_engagement` writer and does not change `attention_daily_rollup` insight
counts. It also does not mark an insight engaged. Historical evidence is never
rewritten based on later reachability.

An optional approval acknowledgement rate has its own denominator: confirmed
direct/reminder membership with complete evidence while the action was still
pending. Ambiguous, safe-retry, unconfirmed collapsed membership, homecoming,
and already-expired observations are excluded. That diagnostic cannot affect
insight verbosity or action state.

### D9 - Fixed serialization and replay identities

The existing action -> intent -> presentation lock order extends to the
attention episode. Reminder admission has a unique `(action_id,
reminder_ordinal)` identity. Acknowledgement records have semantic uniqueness.
Presence epochs and homecoming summaries each have a unique current identity.

Ack-first blocks reminder admission. Admission-first can be cancelled before
handoff. Handoff-start-first may finish only that started attempt; the
acknowledgement blocks later ordinals. Decisions and expiry fence all future
starts. Replays return the durable prior result.

## Success/failure/replay/concurrency coverage matrix

| Successor matrix row | Success evidence | Failure boundary | Replay/concurrency evidence |
| --- | --- | --- | --- |
| Provider receipt versus owner receipt | Explicit acknowledgement closes attention without deciding. | Provider delivery is not acknowledgement; unreadable acknowledgement evidence fails closed. | Acknowledgement semantic replay and acknowledgement-versus-reminder race. |
| Ambiguity | Same-key reconciliation may later confirm. | Ambiguous effect blocks every fresh reminder, channel switch, and homecoming key. | Concurrent/duplicate sweeps return the existing ambiguous presentation. |
| Recovery timing | Safe pre-handoff failure uses RFC 0023 recovery on one key/channel. | Recovery constants cannot become reminder or reachability constants. | Recovery attempts do not consume ordinals; restart reclaims only under RFC 0023. |
| Reminder authority | Confirmed anchor admits one exact +4h/+24h ordinal when every gate passes. | Missing ack, terminal action, expiry, in-flight work, or no channel creates no send. | Unique `(action_id, reminder_ordinal)` and fixed lock order fence duplicate schedulers. |
| Channel order and burst compatibility | Eligible preferred channel, then Telegram, then email; digest members use only a channel eligible and unused for that action. | Unsupported/unregistered/unreachable preference is skipped; incomplete inventory holds; incompatible digest members hold without membership, ordinal consumption, direct send, or second digest. | The locked per-action eligibility snapshot is returned to concurrent/replayed admission. |
| Owner reachability | Fresh owner activity is reachable; fresh confirmation on the observation inventory without activity is degraded even when the episode used that channel. | Missing/stale/ambiguous evidence is unknown; non-empty all-channel no-effect evidence is unreachable. | One consistent observation-inventory snapshot derives state; event replay does not add activity. |
| Safe hold and expiry | Safe hold preserves a pending action for the owner until canonical expiry. | No hold mutates `ActionStatus`, expiry, decision, or execution. | Hold is monotonic/idempotent; concurrent canonical expiry remains the sole action writer. |
| Homecoming | One bounded summary is admitted for a new owner-presence epoch. | Ambiguous actions are excluded; expired items are non-actionable; no rows means no summary. | Singleton epoch lock and unique epoch key deduplicate concurrent/restarted activity. |
| Disengagement denominator | Existing insight delivery/owner-ingress writers keep their present semantics. | Approval traffic writes no insight denominator/numerator and cannot change auto-off. | Reachability replay/state changes never rewrite historic insight or approval evidence. |

## Alternatives rejected

- Same-channel periodic nagging: violates the distinct-channel evidence goal
  and is noisier than bounded cross-channel attention.
- Unbounded retry until expiry: confuses transport recovery with owner
  acknowledgement and can produce many sends.
- Treat any owner activity as action acknowledgement: loses action-specific
  evidence and hides unattended approvals.
- Treat provider confirmation as acknowledgement: overstates what the
  provider proved.
- Treat missing evidence as no acknowledgement: converts an outage into egress
  authority.
- Add `safe_hold` to `ActionStatus`: lets delivery policy own domain lifecycle.
- Use approval traffic in the insight denominator: changes an unrelated
  adaptive policy and contradicts current writers.
- Include ambiguous actions in homecoming: launders an uncertain effect into a
  new key.

## Review and adoption plan

1. Run strict OpenSpec, body-loss/overwrite, countable-task, documentation, and
   repository guards on this draft.
2. Obtain independent security review of actor attribution, channel
   allowlisting, content-blind storage, ambiguity fencing, and action authority.
3. Obtain independent product review of cadence, count, channel order,
   safe-hold truth, homecoming bounds, and owner experience.
4. Resolve all findings and record the exact reviewed commit/digest.
5. Obtain owner adoption of that exact artifact. Direction B and RFC 0023
   adoption do not satisfy this gate.
6. Only then decompose implementation. Activation and live sends remain later,
   separate owner gates.

## Rollback

This draft has no runtime state to roll back. Reverting it removes only the
candidate RFC and OpenSpec artifacts. Future implementation must default any
new writer and worker off, retain evidence on binary rollback, and forbid
backfill/replay of historical actions without separate authority.
