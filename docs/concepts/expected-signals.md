# Expected signals

An expected signal is an observation that normally arrives on a learned or
declared cadence: a weight reading, for example. Time alone cannot establish
that the observation is absent. The instrument must also be working.

Butlers records that distinction in `public.expected_signals`:

- **present** means the producer is healthy and the cadence has not elapsed;
- **absent** means the producer is healthy and the cadence has elapsed;
- **unmeasurable** means producer liveness or health is unavailable, so no claim
  about owner behavior is justified.

Gap detectors use the shared helper in `butlers.core.expected_signals`, which
joins connector producers to the canonical connector-liveness projection before
upserting the producer-owned key. Only `absent` may create an owner-facing gap
candidate. `unmeasurable` is shown as instrument failure and pauses the nudge.

Current runtime implementation is connector-type-only: `core_210` has no
`producer_endpoint_identity` column and the helper does not filter liveness by
endpoint. Health is the only adopted detector. Finance connector-backed signals
therefore remain unadopted/unmeasurable rather than relying on this weaker lookup.

**[TARGET-STATE — continued bu-8cdl1.3 adoption.]** Connector-backed signals
carry `producer_endpoint_identity` copied from server-derived source provenance.
Liveness is authorized only by the exact `(connector_type, endpoint_identity)`
pair; a healthy sibling endpoint never substitutes for a dead, stale, missing,
or unreadable source. The `owner` producer has no connector endpoint. The same
lane owns the shared schema/helper/API migration, existing Health compatibility
and provable backfill (with no guessed endpoint or type-only fallback), and
migrated-PostgreSQL endpoint tests.

The ledger contains cadence and timestamps, not measurement values or candidate
messages. See RFC 0029 for the schema, ownership, and failure contract.
