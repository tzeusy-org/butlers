---
name: commitment-capture
description: Detect explicit first-person commitments the owner makes in conversation and record them as ledger commitments; close them when the owner reports the action done. Covers what counts as explicit, what to pass verbatim, and how to read a skip.
version: 1.0.0
tags: [relationship, commitments, extraction, owner-conditions]
---

# Commitment Capture

RFC 0026 / REQ-commitment-lifecycle-007 and -008.

When the owner tells you they are going to do something for someone, that is a
commitment the system should carry for them instead of leaving it in their
head. When they tell you they have done it, the system should stop carrying it.
This skill is both halves.

## The two tools

| Tool | Use when |
|---|---|
| `commitment_capture(utterance)` | The owner states something they will do |
| `commitment_resolve_from_utterance(utterance)` | The owner reports having done it |

## Pass the owner's words verbatim

This is the rule that matters most. Both tools take the sentence **exactly as
the owner said it** — same wording, same hedges, same tense.

Do not:
- paraphrase "I might call Sam" into "I'll call Sam"
- strip a hedge or a condition because you judged the owner meant it firmly
- assemble an utterance from several turns of conversation
- invent a deadline the owner did not state

The extractor decides whether the sentence is an explicit commitment. Handing
it a cleaned-up sentence is how a hedge becomes a promise the owner never made,
and a fabricated commitment then escalates at them for weeks. Your judgement is
expressed by *choosing to call the tool*; the wording is evidence, and evidence
is not yours to edit.

## What counts as explicit

Recorded — an unhedged first-person statement of intent:

- "I'll send Sam that book tomorrow."
- "I promised Maya I'd review her draft this week."
- "I need to follow up with Priya about the invoice."
- "I'm going to drop the keys off with Noah on Friday."
- "I told Devi I would book the table tonight."

Refused — call the tool anyway if you are unsure, and let it refuse:

| Statement | Why it is not a commitment |
|---|---|
| "I should probably get around to calling Sam." | Hedged — the owner left an exit |
| "I might grab coffee with Noah on Friday." | Hedged |
| "If the meeting ends early, I'll call Devi." | Conditional on something that has not happened |
| "You'll send Maya that book tomorrow." | Not the owner's commitment |
| "Priya said she'll send me the invoice." | Someone else's commitment |
| "Should I send Sam that book?" | A question |

## Reading the result

A skip is a normal outcome, not a failure. Do not retry a skip with reworded
input — that is the paraphrase rule again, and it defeats the gate.

| `reason` | Meaning | What to do |
|---|---|---|
| `no_commitment_pattern` | Not an explicit commitment | Nothing. Continue the conversation. |
| `counterparty_unresolved` | No known contact matched the name | The person may be new. Consider `contact_create` if the owner is introducing someone, then let the *next* mention capture naturally. Do not fabricate a contact to force a commitment through. |
| `below_threshold` | Confidence under the creation floor | Nothing. |
| `no_matching_commitment` | No open commitment matches that action | Nothing. The owner may be reporting something never recorded. |
| `ambiguous_match` | Several open commitments match equally | Ask the owner which one they mean, then say nothing further to the tool — an ambiguous close is worse than an open commitment. |

`{"status": "created"}` opened a new commitment. `{"status": "confirmed"}` means
the owner restated one that was already open — that is expected, not a
duplicate. `{"status": "resolved"}` closed one.

## Do not mention the mechanism

Capturing a commitment is background bookkeeping. Do not narrate it ("I've
recorded that as a commitment"), and do not report a skip to the owner. If the
owner asks what you are tracking, answer from the commitment queries — but a
normal conversational turn should read as if you simply listened.

## Related

- `fact-extraction` — the pipeline for facts about people; run it as usual.
  Commitment capture is additional to it, not a replacement.
- Counterparty resolution uses the same entity path as everything else, so a
  commitment anchors to the same entity a fact about that person would.
