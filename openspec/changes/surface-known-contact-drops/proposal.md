## Why

A block or skip rule can silently drop mail from a person the owner knows, and the Filters opener then reads "All gates clear". The north star says failure must not impersonate health, and the owner would otherwise audit filtered events by hand.

## What Changes

- Connectors that drop a message from a known contact stamp the stored filtered row with a `drop_context` marker (`important_dropped`, `basis`). Gmail is the first producer and stamps every drop site: label exclusion, connector-scope block, and global-scope skip.
- The marker annotates the stored row only. Replay strips it, so the replayed `ingest.v1` envelope is unchanged.
- A new `GET /api/ingestion/events/dropped-known` aggregate counts unanswered marked drops (`dropped`, `episodes`) and reports `available=false` when the filtered-event store cannot be read.
- The Filters opener renders "N dropped from people you know" as a door, renders "gate harm unknown" when the aggregate is unavailable, and never renders the all-clear line in either case.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `ingestion-policy`: known-contact drops are marked on the stored filtered row.
- `dashboard-ingestion-dispatch-console`: the Filters opener surfaces the aggregate with an honest unknown state.

## Impact

- `src/butlers/connectors/filtered_event_buffer.py`, `gmail.py`, `gmail_policy.py`; `src/butlers/core/ingestion_events.py`; `src/butlers/api/routers/ingestion_events.py` and its model; the Filters opener and a query hook in the frontend.
- No schema change: the marker lives inside the existing `full_payload` JSONB, and the aggregate reads it by path.
- Deferred slices: a narrowed filtered list and replay verb from the clause, the runtime attention episode, the opt-in "except known contacts" rule-editor option, and the WhatsApp and Drive drop sites. Rule precedence is unchanged and remains an owner decision.
