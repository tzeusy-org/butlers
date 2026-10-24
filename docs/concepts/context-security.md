# Context rendering and attention-read failures

This is the proposed bu-dhloi1.17 contract for bu-dhloi1.2. It is not shipped
runtime behavior. Exact RFC text adoption and implementation remain pending.
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

## Current implementation gap

At the task baseline, src/butlers/context_bus.py still renders raw values;
src/butlers/jobs/context_producers.py still copies calendar titles and conflates
read failure with absent policy. src/butlers/core/attention_ledger.py and the
insight broker still fail open on unavailable context reads. This docs-only PR
changes none of them. The implementation successor must amend the named existing
behavior tests in the same PR as its production correction and retain unaffected
assertions/cases. No runtime test was executed or changed by this authoring task.
