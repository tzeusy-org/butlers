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


## Reviewed q43 availability amendment

# Surface known-contact drops: q43 current-source amendment

Keep every original positive known-drop/privacy/replay/opener promise and the deferred list/replay/episode/other-connector scopes of the existing active proposal. This amendment repairs false all-clear caused by Gmail contact classification unavailable on first load, stale/failed refresh and restart, and preserves historical uncertainty instead of certifying it after recovery. Source snapshot, fixed frozen drop context, actual heartbeat epoch/generation admission, authoritative multi-account/window reads and actual query/opener form one cohesive behavior. The full original two-field equality and absent-stranger-context scenarios are retained by name but explicitly amended to allow content-free classification while retaining positive-marker absence for strangers. Provider/auth health, contact priority, rules, content privacy and replay policy remain unchanged.

This bounded source amendment was reviewed at base3411/current091a and released after real-PG baseline falsification on PR4379. Runtime source is implemented in that PR; fixed exact-head SQL, independent review and protected merge evidence are required before whole completion, and source delivery is not deployment evidence. Scope does not include new migrations/grants, global auth, new trust roots, signing/custody, provider/live rollout, automatic replay, or neighboring .41/.44 feature adoption. Preserve old proposal text as provenance during translation and add this bounded section; do not archive the active change as wholly delivered. Full contract bodies and clause-parity receipt accompany this proposal.


Accepted independent PRIMARY corrections F1/F2/F3 add the separate UTC drop observation, exact serialized publisher ACK lifecycle and distinct query/history versus current admission truth. All four mandatory requirements,38previous scenario names/assertions and six literal acceptance criteria are preserved. Publication failure never invents contact-query failure or a new auth/admission requirement on genuine historical classification. The active change remains open for its other mandatory deferred list/replay/episode/connector outcomes; this source repair does not adopt those neighboring implementations.
