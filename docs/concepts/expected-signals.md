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

The ledger contains cadence and timestamps, not measurement values or candidate
messages. See RFC 0029 for the schema, ownership, and failure contract.
