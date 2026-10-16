# Proactive insight feedback

Switchboard keeps one global daily insight cap while shaping which categories
earn its slots. Recent ignored deliveries reduce only their own category's
weight. The API reports the reason without reproducing insight content, for
example `hearing less from Health: 9 of last 10 ignored`.

The owner has three bounded feedback verbs on Telegram deliveries (standalone
and per-insight digest rows), delivered-message actions, and dashboard insight rows:

- **Useful** restores the insight family and its category toward baseline.
- **Not now** requires a future snooze deadline.
- **Never** holds the family indefinitely, but a later Useful reverses it.

Telegram shows **Useful**, **Not now** and **Never** for every insight. Not now
snoozes its family for 72 hours, computed by the server. A tap marks the current
verdict and keeps all buttons, so Useful can reverse Never without leaving the
message. Repeating the current verdict or replaying an already recorded Telegram query
adds no feedback row. Callback authority requires the primary owner's sender
and private chat, a signed `ifb1` token bound to the candidate's server-created
time, and the scoped connector credential. API outages keep the buttons for a
retry; purged candidates lose only their own digest row.


The Trust Console attention ledger also counts `expired_unseen` per originating
butler. Each expired row records only its candidate reference and a closed
`blocked_by` reason (`budget`, `cooldown`, `held_by`, or `dedup`); it never
copies the insight message or other sensitive evidence.

## Premise-bound insights

An insight can name the fact it asserts (its `premise`): either an owner-condition
episode that must still be active, or a registered zero-LLM probe: finance's
`bill_still_pending`, or health's `medication_refill_still_due` (the medication is
still active and its estimated supply is still inside the 14-day refill window). The delivery cycle re-checks the premise
before selection:

- **False**: the candidate is withdrawn and never sent (attention-ledger outcome
  `withdrawn`), so a paid bill's "due tomorrow" does not go out.
- **Unknown** (probe error, no evidence): the insight is delivered stamped
  `(as of <proposed time> UTC)` and is never presented as rechecked.
- **Resolved after delivery**: when an owner-condition premise resolves (probe premises,
  including the health refill probe, are checked only before selection and are not amended
  in place), a standalone
  Telegram message is edited in place to a struck-through original plus `Resolved HH:MM UTC`
  (outcome `amended`, no new ping). A digest line, an email, a rejected edit (Telegram
  400) or three transport failures instead fold a `Since last digest: Resolved ...`
  line into the next delivery that happens anyway.

A candidate with no premise behaves as before. Probes live in
`roster/switchboard/tools/insight/premises.py`; producers pass `premise=` to
`propose_insight_candidate`.

The origin of a candidate is never chosen by a session. Producers outside Switchboard
(deterministic jobs and tools) call the Python function and pass their own fixed
`origin_butler`. The Switchboard MCP tool has no `origin_butler` parameter: sessions get
the registering daemon's origin, `switchboard`, and a butler-scoped (four-segment) dedup key
must name it.
