# RFC 0035: Bounded Post-Delivery Approval Reminders

**Status:** Proposed - owner selected Direction B on 2026-09-15; independent
security/product review and exact-artifact adoption remain required
**Date:** 2026-09-16
**Related:** RFC 0011 (proactive insight engagement), RFC 0017 (owner-routing
safety), RFC 0021 (one-tap approvals), RFC 0023 (durable approval delivery),
`about/heart-and-soul/security.md` (Approval Gates), OpenSpec change
`bounded-post-delivery-approval-reminders`

---

## Status and authority boundary

The owner already adopted RFC 0023 and separately released its repository
implementation. Those decisions are not reopened here. On 2026-09-15 the owner
selected Direction B for this successor: provider-confirmed delivery MAY be
followed by separately bounded reminders when the owner has not acknowledged
the approval.

Direction B authorizes this exact drafting work only. It did not select the
channel order, time bounds, reminder count, evidence windows, safe-hold model,
homecoming behavior, disengagement formula, implementation, rollout, or any
live send. Those details are proposed below for independent review and exact
owner adoption. Merging this draft records a candidate contract; it does not
activate it.

## Summary

RFC 0023 deliberately makes `delivered` mean provider acceptance, not owner
attention. It also makes authenticated dashboard defer the only existing
successor-presentation authority. That is safe recovery, but it leaves one
product gap: a confirmed notification can be ignored or missed while the
action remains pending.

This RFC adds a policy layer above RFC 0023 recovery. After a direct approval
presentation is provider-confirmed, and only while its action remains pending
and unexpired, deterministic policy may admit no more than two automatic
reminder presentations over the action's entire lifetime. Reminders are due at
fixed offsets of 4 hours and 24 hours from the current owner-requested
presentation's confirmed handoff. Each uses a different eligible channel, and
no channel used by that episode may be used again. With today's registered
channels (`telegram`, `email`), an initial delivery already consumes one, so at
most one automatic cross-channel reminder can actually be sent. Adding another
channel to Messenger does not make it eligible under this RFC.

Reminder admission is not transport recovery. Recovery keeps the same
presentation key and follows RFC 0023's 15-second exponential backoff, lease,
reconciliation, and ambiguity rules. Reminder policy creates a new presentation
key only after confirmed delivery and only at an unused, eligible channel. An
ambiguous effect freezes the episode: it cannot be converted into a reminder,
channel switch, homecoming item, or fresh key.

`safe_hold` is a durable attention outcome, not an approval decision.
`pending_actions.status` remains `pending` until an authenticated owner decision
or the existing expiry path changes it. Automatic policy never extends expiry,
approves, rejects, executes, revives, or defers an action.

## Doctrine position

- A reminder asks for attention. It grants no authority to act.
- The scheduler is deterministic daemon infrastructure and invokes no model.
- Channel identifiers are resolved at egress and are never persisted in the
  approval attention record, API, metrics, or audit payload.
- Only channels registered end-to-end by the Switchboard notify dispatcher and
  explicitly named by this RFC are eligible. The initial allowlist is exactly
  `telegram` and `email`. WhatsApp, Discord, and every future channel are out of
  scope until a reviewed amendment names them.
- Provider acceptance is not owner acknowledgement. Silence is not consent.
- Missing evidence fails closed. It never becomes permission to send.

## Normative design

### 1. Evidence vocabulary and acknowledgement

Each action delivery intent gains one durable attention episode with a closed
state vocabulary:

| State | Meaning |
| --- | --- |
| `open` | A provider-confirmed owner-requested presentation has no qualifying acknowledgement and may still admit a bounded reminder. |
| `acknowledged` | Action-specific, authenticated owner evidence closed the episode. |
| `safe_hold` | Policy may not send another automatic presentation. The domain action is unchanged. |

An acknowledgement qualifies only when it is durably bound to the action and
the relevant presentation generation, carries a server-derived owner principal,
and is one of:

1. a successful authenticated approve, reject, or defer operation;
2. an explicit authenticated `acknowledge attention` operation that changes no
   action status, arguments, expiry, or execution state; or
3. a provider-native read receipt whose adapter contract cryptographically or
   account-authentically binds it to the verified owner and exact presentation.

No current Telegram or email adapter exposes evidence satisfying item 3, so
provider receipts do not qualify today. Dashboard GETs, prefetches, polling,
message delivery, unrelated owner ingress, system expiry, connector traffic,
and non-owner activity are not action acknowledgement.

Acknowledgement writes are idempotent by `(action_id, presentation_generation,
evidence_kind, evidence_key)`. Replaying the same evidence returns the first
record. A different owner-authenticated acknowledgement may add provenance but
cannot reopen or reschedule an acknowledged episode. Evidence timestamped
before the generation it claims to acknowledge, bound to another action, or
supplied by a caller as an actor claim is stale or invalid and has no policy
effect.

If the acknowledgement store cannot be read completely, the scheduler enters
`safe_hold(reason=ack_evidence_unavailable)` and sends nothing. Absence may
authorize a reminder only after a successful, complete read proves no qualifying
acknowledgement exists.

### 2. Reachability evidence

Reachability is an observation used for truthful UI and homecoming behavior. It
is not reminder authority. Its observation inventory is the complete current
set of `telegram` and `email` channels that are present in the Switchboard
trusted-recovery notify registry, have an enabled Messenger adapter, and have
one unambiguous active owner reachability fact. Unlike reminder eligibility,
this inventory does not exclude a channel because a presentation already used
it in an attention episode, and owner preference does not affect membership.
Evidence is fresh for 24 hours from its durable occurrence time:

| Derived state | Exact rule |
| --- | --- |
| `reachable` | At least one qualifying owner-activity event is fresh. |
| `degraded` | No owner activity is fresh, but at least one channel in the reachability observation inventory has a fresh provider-confirmed presentation. |
| `unreachable` | No owner activity is fresh, the reachability observation inventory is non-empty, every channel in it has a fresh definitive pre-provider/no-effect unavailable result, and none has fresh confirmed or ambiguous evidence. |
| `unknown` | Evidence is missing, incomplete, stale, contradictory, or includes an unresolved ambiguous effect. |

Qualifying owner activity is limited to an accepted ingress whose sender
resolved to the owner, successful owner authentication ceremony, or successful
authenticated owner mutation. Background GETs, dashboard polling, connectors,
scheduled jobs, health probes, and unresolved/non-owner senders do not qualify.

The rules are evaluated in table order from one consistent snapshot. An
incomplete observation-inventory read yields `unknown`. Reachability older than
24 hours is retained as history but ignored by current-state derivation.
`unknown` never degrades to `unreachable` by passage of time. `degraded` is not
proof of disengagement: it says only that a provider accepted something and no
fresh owner activity is known.

### 3. Registered-channel eligibility and order

A channel is eligible for an automatic reminder only if all of these are true
at reminder admission:

1. it is exactly `telegram` or `email`;
2. it is present in the Switchboard notify dispatch registry for the trusted
   RFC 0023 recovery path;
3. Messenger has the corresponding send adapter enabled;
4. the owner entity has one unambiguous, active reachability fact for it; and
5. it has not been used by any provider-started or confirmed presentation in
   the current attention episode.

The first four predicates are the reachability observation inventory from
section 2. The fifth is action-episode policy and applies only to reminder and
homecoming channel selection. It never removes historical channel outcomes
from observational reachability derivation.

Private addresses and handles are resolved only at egress. Eligibility stores
the channel name and safe reason, never the identifier.

Eligible channels are ordered once under the action/intent lock:

1. the owner's active `prefers-channel` fact, if eligible and unused;
2. `telegram`, if eligible and unused;
3. `email`, if eligible and unused.

An unsupported preference is skipped, not provisioned. WhatsApp is not
eligible even though Messenger owns a WhatsApp tool, because it is not in the
current Switchboard notify registry or this RFC's allowlist. Registry or owner
reachability read failure yields `safe_hold(channel_inventory_unavailable)`.

A channel found definitively ineligible before a presentation is created is
skipped. Once a presentation exists, RFC 0023 recovery owns it on that channel.
A safe pre-handoff failure retries the same key and channel under RFC 0023; it
does not jump channels. A post-start ambiguous result freezes all later reminder
slots. This prevents a timeout from being laundered into cross-channel resend.

### 4. Timing, count, quiet hours, and burst control

The action has a lifetime automatic-reminder budget of two. The count is the
number of reminder ordinals durably admitted, including a reminder collapsed
into a cohort digest. Duplicate scheduler runs do not consume another ordinal.

An attention episode is anchored when an owner-requested direct presentation
(initial park or authenticated defer successor) becomes `delivered`. Its two
candidate due instants are:

- reminder 1: `anchor_confirmed_at + 4 hours`;
- reminder 2: `anchor_confirmed_at + 24 hours`.

An authenticated defer acknowledges the old generation and may create the
already-authorized defer successor. If that successor is later confirmed, it
starts a new episode using only the action's still-unused lifetime reminder
ordinals. Defer remains the only operation here allowed to change expiry.

At each due instant the scheduler locks action, intent, episode, and current
presentation in the RFC 0023 order and verifies all of the following:

- the action is still `pending`;
- database time is before the current canonical `expires_at`;
- acknowledgement evidence was read completely and none qualifies;
- no earlier presentation is `ambiguous` or still in recovery;
- the reminder ordinal is unused; and
- an eligible unused channel exists.

Failure of any check creates no provider effect. Terminal action or expiry
cancels policy. Missing evidence or no channel enters `safe_hold` with a closed
reason. A reminder due at or after expiry is never admitted.

Quiet hours use RFC 0021's existing end-exclusive Owner Attention Policy. The
reminder stores the exact quiet-hours release calculated at admission and is
not re-gated after a policy edit. If that release is at or after expiry, the
reminder is not sent and the episode enters `safe_hold(expiry_fence)`.

Automatic reminders participate in the same per-schema ten-minute approval
burst window. The first three approval attention presentations in a window are
direct; the fourth creates one cohort-owned digest on a channel eligible for
that action. A later due reminder may join that digest only when the digest
channel is also eligible and unused for that action under the same locked
per-member snapshot. A compatible membership records and consumes its ordinal.
A definitive per-member mismatch enters
`safe_hold(burst_digest_channel_incompatible)` without a membership, ordinal,
direct send, or second digest; an incomplete inventory read instead enters
`safe_hold(channel_inventory_unavailable)`. One confirmed or ambiguous digest
result applies to every admitted member. This preserves the one-digest burst
shape without bypassing the distinct-channel boundary.

### 5. Recovery is not reminder policy

The following boundaries are non-interchangeable:

| Concern | Key and timing | Authority |
| --- | --- | --- |
| Transport recovery | Same presentation key; RFC 0023 lease, 15-second initial retry, six exponential steps, deterministic jitter, 15-minute cap, reconciliation. | Recovery worker only. |
| Automatic reminder | New monotonic presentation generation at the 4-hour or 24-hour policy slot, different eligible channel, lifetime count at most two. | Reminder policy scheduler only after confirmed prior delivery. |
| Authenticated defer | New presentation generation at `now + hours`, with owner-authorized expiry change. | Existing dashboard defer transaction only. |
| Homecoming digest | One summary per owner-return episode, with no action mutation. | Homecoming projector only. |

Recovery constants are not reachability windows or reminder intervals. Safe
retries never consume a reminder ordinal or change channel. A confirmed replay
of the same presentation remains one delivery. `ambiguous` permits only
reconciliation of the same key and blocks every automatic fresh key.

### 6. Safe hold and ActionStatus

`safe_hold` belongs to the attention episode, not `ActionStatus`. This RFC does
not add `safe_hold` to the action enum. The only action states remain
`pending`, `approved`, `rejected`, `expired`, `executed`, and `abandoned`, with
their existing transitions.

Entering safe hold:

- cancels unstarted automatic reminder slots;
- records one closed reason such as `reminder_budget_exhausted`,
  `no_eligible_reminder_channel`, `ack_evidence_unavailable`,
  `channel_inventory_unavailable`, `burst_digest_channel_incompatible`,
  `ambiguous_delivery`, or `expiry_fence`;
- never changes `pending_actions.status`, arguments, `expires_at`, decision
  provenance, or execution state; and
- is idempotent and monotonic except that a later qualifying acknowledgement
  may project the episode as acknowledged without reviving a send.

The canonical expiry path still transitions a pending action to `expired` at
its existing expiry. No reminder extends, approves, or revives it. An expired
action may be described historically, but no approval control or callback may
be generated for it.

### 7. Homecoming summary and deduplication

The system maintains a content-blind owner-presence epoch. Under a singleton
database lock, a qualifying owner-activity event starts a new epoch only when
the preceding qualifying activity is at least 24 hours old; otherwise it
updates the current epoch. Concurrent events therefore resolve to one epoch.

At most one homecoming summary is admitted per epoch, enforced by a unique
`owner_presence_epoch_id`. It contains at most 20 actions and an overflow count:

- pending actions currently in safe hold; and
- safe-held actions that expired within the preceding 7 days and have not
  appeared in a confirmed homecoming summary.

Rows are ordered pending before expired, then earliest expiry/request time.
Pending rows link to the dashboard approval detail. Expired rows are explicitly
non-actionable. The summary carries no tool arguments, recipient identifier,
callback token, or direct approve/reject control.

An action whose latest presentation is ambiguous is excluded from outbound
homecoming content until reconciliation resolves it. It remains visible on the
authenticated dashboard as ambiguous. This prevents homecoming from laundering
an uncertain provider effect into a fresh presentation key.

The homecoming summary uses the qualifying activity's channel only when that
channel is eligible under section 3; otherwise it uses the same preferred then
`telegram`, `email` order. It observes quiet hours at the exact end and counts
as one presentation in the burst window. Repeated activity, restart, or
concurrent triggers replay the same epoch key and cannot create another
summary. A post-start ambiguous homecoming handoff is reconciled under its same
key and never reissued.

### 8. Disengagement denominator

The current proactive-insight writers are authoritative:

- `record_engagement_rows()` inserts one `insight_engagement` denominator row
  for each delivered insight;
- owner-resolved Switchboard ingress calls `check_and_update_engagement()` and
  `record_owner_ingress_rollup()`; and
- cleanup copies those insight counts into `attention_daily_rollup`.

Approval delivery, reminders, safe holds, and homecoming summaries write none
of those fields today. This RFC preserves that boundary. They MUST NOT insert
`insight_engagement` rows, increment `insights_delivered` or
`insights_engaged`, mark an insight engaged, or change the 14-day auto-off
denominator. Approval silence therefore cannot impersonate insight rejection,
and approval traffic cannot inflate the denominator.

If an approval-specific acknowledgement rate is exposed, its denominator is
only provider-confirmed direct or reminder membership for a still-pending
action with a complete evidence read. Ambiguous, safe-retry, collapsed-without-
confirmed-digest, homecoming, and already-expired observations are excluded.
Its numerator is qualifying action acknowledgement. The rate is diagnostic
only and cannot change insight verbosity or action state.

Reachability updates never delete or rewrite historical insight or approval
evidence. A future proposal to exclude periods of proven unreachability from
the insight ratchet requires its own exact owner-approved amendment; this RFC
does not infer that authority.

### 9. Concurrency and replay

- Scheduler claims are unique by `(action_id, reminder_ordinal)`.
- Acknowledgement, decision, expiry, defer, and reminder admission use the
  fixed action-then-intent-then-presentation/episode lock order.
- Ack-first prevents admission. Reminder-admission-first may create an
  unstarted presentation that ack cancels. Handoff-start-first may complete
  only that already-started call; acknowledgement blocks later reminders.
- A decision or expiry winner blocks every later provider start and cannot be
  reversed by a late handoff result.
- Concurrent owner-activity events serialize one presence epoch and one
  homecoming key.
- Replayed scheduler, callback, provider receipt, defer request, or homecoming
  trigger returns the durable prior result and creates no additional key.

## Required review and verification

Independent security review must verify actor derivation, content-blind
storage, channel allowlisting, ambiguity non-laundering, and the absence of any
new decision/execution authority. Independent product review must verify the
4-hour/24-hour cadence, two-reminder lifetime cap, channel ordering, safe-hold
semantics, homecoming content/window, and owner-facing truth.

Future implementation is not accepted without behavior-executing tests for:

- qualifying and non-qualifying acknowledgement evidence, stale evidence,
  replay, and concurrent acknowledgement;
- exact observation-inventory versus reminder-eligibility semantics, channel
  order, unsupported preference, missing registry, unavailable recipient, and
  no private identifier persistence;
- exact 4-hour/24-hour slots, lifetime count, expiry fence, quiet-hours release,
  per-member digest-channel compatibility, incompatible-member safe hold,
  burst collapse, restart replay, and defer with remaining budget;
- safe pre-handoff recovery versus confirmed reminder admission and ambiguous
  no-resend/no-channel-switch;
- safe hold leaving every `ActionStatus` and expiry rule unchanged;
- homecoming epoch concurrency, 20-row/7-day bounds, deduplication, quiet hours,
  expired non-actionability, and ambiguous exclusion; and
- proof that approval traffic never writes the proactive-insight denominator or
  numerator.

## Rejected alternatives

- **Blind repeat on the original channel.** Rejected: provider-confirmed
  delivery plus no acknowledgement is not transport failure, and same-channel
  repeats create noise without new reachability.
- **Use RFC 0023 backoff as reminder timing.** Rejected: recovery proves one
  presentation; it says nothing about owner attention.
- **Treat missing acknowledgement storage as silence.** Rejected: missing
  evidence is unknown, not permission.
- **Add `safe_hold` to ActionStatus.** Rejected: delivery attention must not
  become a domain decision or extend the action lifecycle.
- **Enable every Messenger-owned channel.** Rejected: tool ownership is not an
  end-to-end recovery registration or owner adoption. WhatsApp remains out.
- **Count approval reminders as insight engagement opportunities.** Rejected:
  that would poison an unrelated adaptive policy and contradict the real
  writers.
- **Use homecoming to replay ambiguous actions.** Rejected: a new digest key
  cannot make an uncertain provider effect safe.

## Rollout boundary

This RFC and its OpenSpec change are planning artifacts only. After independent
review, the owner must adopt the exact reviewed bytes before implementation can
begin. Implementation, migration, provider credentials, rollout flags,
deployment, runtime activation, notification sends, and action decisions each
remain separate gates. No historical action or delivery is backfilled or
replayed merely because this draft exists.
