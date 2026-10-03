---
name: meeting-debrief
description: Record the owner's answer to the end-of-day "anything agreed?" meeting prompt, as commitments or as "none agreed". Use when the owner replies to a meeting debrief ("1: send Sam the deck by Fri", "2: none").
version: 1.0.0
tags: [relationship, commitments, meetings, owner-conditions]
---

# Meeting Debrief

Each evening the owner may be asked, in one message, about meetings they had with other
people: "Anything agreed?" Their reply is the only thing that creates a record. This skill
turns that reply into commitments, or into a recorded "nothing agreed".

## The two tools

| Tool | Use when |
|---|---|
| `meeting_debrief_pending()` | The owner replies to a debrief prompt. Match their number to the entry's `number`. |
| `meeting_debrief_answer(debrief_id, commitments)` | Record the answer for one meeting. |

## Recording an answer

- "none", "nothing", "nothing agreed": call `meeting_debrief_answer(debrief_id)` with no
  commitments. Silence is not an answer; "none" is.
- "send Sam the deck by Fri": one item, `{"summary": "Send Sam the deck",
  "counterparty_entity_id": <Sam's id from the entry>, "deadline": <that Friday, ISO date>}`.
- Several things agreed: one item each, in the same call.

Rules that matter:

- Record only what the owner said was agreed. Do not add follow-ups you think are sensible.
- Take the counterparty from the entry's `attendees`. If the owner does not say who and the
  meeting had several people, leave `counterparty_entity_id` out; do not guess.
- Set `deadline` only when the owner gave one. Set `sphere` ("work" or "personal") only when
  the owner said which.
- `direction` is `owner_to_other` by default (the owner owes it). Use `other_to_owner` when
  the owner is waiting on someone ("Priya will send me the numbers").
- One call per meeting. A second call for an answered meeting reports `already_answered`
  and changes nothing.

## Reading the result

`captured` and `none_agreed` are done. `invalid` carries a reason (usually a counterparty who
was not at that meeting): fix the item or ask the owner, do not retry unchanged.
`not_found` means the id is wrong; call `meeting_debrief_pending()` again.

## People who are not active

The pending list never includes people the owner marked memorial, quiet or no_contact. Do
not mention, ask about or look up anyone who is not in the entry.

## Related

- `commitment-capture` handles commitments the owner volunteers in ordinary conversation.
