# Context rendering and attention-read failures

This implementation follows the adopted bu-dhloi1.17 contract (PR4980).
It changes rendering, explicit context mutation and routine delivery gates;
it does not activate RFC0038 runtime features.
See the [active change](../../openspec/changes/specify-safe-context-rendering-and-bounded-read-failures/design.md)
for numerical source bindings and the complete requirement/test accounting.

## Producer and prompt boundary

Calendar still derives meeting or focused from eligible human events. Its
published state is only meeting/busy or focused/focused; titles, descriptions
and event free text stay out of context value, metadata and producer result.
The preamble admits only these typed values from the actual trusted producer.
Other signals keep type/confidence summaries. Stored strings, forged producer
labels and escaped/truncated instructions cannot become trusted prompt values.
No new context type, perception store or authority is created.

Explicit owner set/clear requires the verified current invocation and real
server-resolved owner, not text or a diagnostic session locator. Scheduled
producers retain their distinct role-scoped internal paths. Existing canonical
DND mutation identity, writer, replay, generation and receipt rules survive.

## Routine attention state and recovery

A successful read of missing/disabled/invalid policy clears/avoids the Health
sleep assertion; it does not invent a timezone or erase independent suppressors.
A read failure is unavailable. Hold/refuse this routine attempt without sending;
retain only already-confirmed finite expiry. Never refresh a sleep/DND timestamp
because the store could not be read. With no known anchor, or after its expiry,
finish unavailable rather than fabricate a wake time, run forever or drop pending
work. On recovery, read current state and apply existing expiry/deduplication
before normal release. Neither recovery nor expiry proves that delivery occurred.
A queue or ledger outage is never a claimed enqueue/delivery success.

Existing signal TTLs are DND 2h/24h, sleeping 8h/12h, meeting 1h/4h and focused 2h/8h
(default/max). Insight max-holds are DND 4h, focused 4h, meeting 2h, sleeping 10h and
traveling 6h from continuous set_at. Direct notify keeps its distinct latest active
DND/sleeping expiry rule. Policy sleep uses the exact end-exclusive configured
boundary, subject to the 12h TTL clamp, with no new timezone or duration setting.

Deterministically authorized safety/urgent paths keep their existing bypass.
Routine failures cannot block that bypass; an arbitrary priority label or source
instruction cannot create it or waive owner/approval/recipient checks.

## Implementation notes

`set_calendar_context()` records an exact returned-row witness in the producer's
own process and pool, including the database-observed `xmin` row revision.
Only an unexpired, unchanged row version witnessed through that
path can render busy/focused. The cache holds at most 16 pools and two rows per
pool. A replacement, clear, eviction or restart revokes the witness. Another
pool/process and legacy rows safely render type/confidence only; this is not
durable producer authentication. Stored `source`, writer labels or matching
values cannot reconstruct the witness. Suppression still uses the actual signal
and bounded expiry, including when its value is omitted from a prompt.

General's set/clear tools resolve the bound runtime invocation using the existing
server routing accessor and owner-entity resolver. An unbound session locator,
forged tool arguments or third-party routing context refuses before the context
write or canonical DND gateway. The existing DND generation/replay protocol is
unchanged.

Immediate owner-default notify and the scheduler's actual deferred transport
callback both re-read policy and context. Unavailable reads return a retryable
error or leave the existing deferred item pending; neither path invents a wake
anchor, refreshes a deadline or records delivery. A model `priority="high"` is
not a bypass credential. Insight's existing server-owned urgent cycle remains
separate: an unavailable routine consult narrows evaluation to that existing
urgent working set and leaves routine candidates pending. It does not promote a
candidate to urgent or override an explicit owner opt-out.

The sleep producer treats successful absent/invalid policy as a clear of its
own assertion, and failed reads as unavailable without writing. Existing sleep
TTL and continuous `set_at` semantics bound retained state. A successful later
read determines recovery; elapsed time alone cannot authorize a send.

Targeted software tests exercise the actual registered tools, producer-to-row
renderer path and deferred callback. Migrated database cases remain separate
hosted evidence; a software pool double is not role or PostgreSQL proof.
