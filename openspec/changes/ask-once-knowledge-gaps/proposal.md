## Why

Today "I don't know" is terminal: a sourceless decline in the dashboard answer lane ends the
question, so the owner carries the job of remembering to re-ask. vision.md measures success by the
mental labor the system reliably absorbs, and the Relationship manifesto promises that "you forget
to ask about it next time" never happens.

## What Changes

- A sourceless `conversation_reply` decline may carry a typed `gap` (entity, predicate). The
  server validates it and records an owner knowledge gap in the answering butler's own memory
  schema, with the origin taken from routing context.
- A later write of a matching (entity, predicate) fact, through memory `store_fact` or
  `relationship_assert_fact`, marks the open gap answerable in the same transaction.
- A deterministic module-default job posts exactly one notice per origin conversation with the
  fact's reference, retries with bounded backoff, and expires gaps open past their ttl.
- `memory_open_gaps` and `GET /api/memory/gaps` let the owner ask what is still unknown, with
  honest coverage states.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `module-memory`: adds "Owner knowledge gaps".
- `butler-switchboard`: adds the answer-lane decline gap capture.
- `relationship-facts`: adds the gap closure hook on the central writer.

## Impact

Memory migration `mem_014`, `core_tools/_conversation_reply.py`, the answer-block instruction in
`core_tools/_switchboard.py`, `modules/memory/storage.py`, `relationship_assert_fact.py`, a new
`memory_open_gaps_deliver` maintenance job, and `GET /api/memory/gaps`. Deferred slice: Telegram
answer path, entity-detail "Still unknown" section, palette verb, and per-predicate demand counts.
