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
