## Why

Per-person recall about a meeting reaches the owner only on the dashboard prep rail or in the
night-before digest. Twenty minutes before a meeting is when it is useful, and the prep envelope
the Relationship butler already writes (`calendar/prep/<event_id>`: last met, commitments in both
directions) holds what a short card needs (bu-jv1moa.9, slice 1 of dots-superset run 20, move 9).

## What changes

- A deterministic, zero-LLM Relationship job, `meeting_card_push`, queues one timed insight (the
  bu-jv1moa.8 timed lane) per meeting that starts within 20 minutes and has at least one other,
  non-declined attendee. It sends nothing itself.
- The card is a fixed template: title, start time, a defanged location, each named attendee with
  the last time they met, "You owe" and "Waiting on" commitments, and a link to the dashboard
  Calendar page. A missing or stale envelope gives "No prep context yet." rather than no card.
- Idempotent per `(event_id, start_at)`: a moved meeting's pending card is withdrawn and it earns one
  new card; a cancelled meeting's pending card is withdrawn.
- Time-critical: `meeting` and `focused` do not hold the card (a per-candidate declaration the timed
  lane honors for this producer only); `dnd` and `sleeping` still do.
- Dark behind `MEETING_CARD_LIVE`, default off, and unscheduled.

## Deferred

- "Debrief later" button: no existing tap sink fits a timed-lane insight (the `meeting_debrief_answer`
  tool is a conversation tool, not a signed tap). Slice 2.
- Leave-by timing needs travel legs (bu-jv1moa.7) and is slice 2.
- "Last message topic": the messenger envelope that carries it is merged only by
  `calendar.v_prep_contributions`, which the Relationship role cannot read, and the Relationship
  envelope's `message_context` is empty. The card omits it; widening either producer is out of scope.
- An event deep link: the Calendar page has no per-event route, so the card links to `/calendar`.
- Intraday refresh of `calendar_prep_contribution` (30-minute cadence): a schedule change that
  activation makes with the cron.
