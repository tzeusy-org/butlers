## Why

Most work promises are made in meetings, but commitment extraction only runs on routed
conversations and the calendar prep rail reads commitments into a meeting without anything writing
them back out. "I'll send you the deck" said in a 1:1 is lost; "nothing agreed" is silence
(bu-q7vx1q.12, JARVIS pursuit run 15).

## What changes

- A zero-LLM Relationship job, `meeting_debrief`, selects ended calendar occurrences the owner
  attended with at least one other person, records one `relationship.meeting_debriefs` row per
  `(event_id, occurrence_start)`, and proposes one end-of-day batched insight listing them.
- Only people whose posture is `active` are listed or counted. A meeting whose only other attendees
  are non-active (or none that remain) is never asked about.
- The owner replies in a Relationship conversation. Two new Relationship `tracking` tools,
  `meeting_debrief_pending` and `meeting_debrief_answer`, list the open debriefs and record the
  answer: `none_agreed`, or commitments created through `create_commitment` with
  `evidence_opened = {source: 'meeting_debrief', event_id, occurrence_start, ...}`. Nothing is
  created without an owner reply.
- Commitment metadata gains an optional `sphere` (`work` | `personal`), declared by the owner.
- After 3 consecutive unanswered prompts the job asks at most weekly and tells the owner so.

## Deferred

- The dashboard Debrief card and a "work commitments" filter on the condition ledger (frontend).
- Deriving `sphere` from endpoint custody (waits for the custody move); the first slice takes the
  owner's declaration only.
- A Vocation butler home for the job (an owner decision).

## Required answer atomicity repair (bu-q7vx1q.68)

Exactly-once answer recording includes competing distinct commitment sets, an empty/non-empty
race, complete rollback after an actual first write and committed-state retry. The repair composes
the existing public create_commitment path on the handler's owning connection, preserving shared
validation, source lock and graph/conditional premise hooks. The prompt job must recheck pending
before expiring a stale unaskable selection so it cannot reopen answered state. Current source
inspection and old sequential tests are not executed concurrency/rollback proof; retain meaningful
before-fix migrated PostgreSQL counterexamples and corrected exact-source receipts. All original
producer, sphere, job/back-off and registered inventory requirements remain mandatory. Separate
answer-time posture policy (.69) and free-text/LLM reply proof (.66) remain incomplete, and this
repair does not archive the whole active change as implemented.
