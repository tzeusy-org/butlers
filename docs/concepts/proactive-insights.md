# Proactive insight feedback

Switchboard keeps one global daily insight cap while shaping which categories
earn its slots. Recent ignored deliveries reduce only their own category's
weight. The API reports the reason without reproducing insight content, for
example `hearing less from Health: 9 of last 10 ignored`.

The owner has three bounded feedback verbs on delivered-message actions and
dashboard insight rows:

- **Useful** restores the insight family and its category toward baseline.
- **Not now** requires a future snooze deadline.
- **Never** holds the family indefinitely, but a later Useful reverses it.

The Trust Console attention ledger also counts `expired_unseen` per originating
butler. Each expired row records only its candidate reference and a closed
`blocked_by` reason (`budget`, `cooldown`, `held_by`, or `dedup`); it never
copies the insight message or other sensitive evidence.

## Premise-bound insights

An insight can name the fact it asserts (its `premise`): either an owner-condition
episode that must still be active, or a registered zero-LLM probe such as finance's
`bill_still_pending`. The delivery cycle re-checks the premise before selection:

- **False**: the candidate is withdrawn and never sent (attention-ledger outcome
  `withdrawn`), so a paid bill's "due tomorrow" does not go out.
- **Unknown** (probe error, no evidence): the insight is delivered stamped
  `(as of <proposed time> UTC)` and is never presented as rechecked.
- **Resolved after delivery**: when an owner-condition premise resolves, a standalone
  Telegram message is edited in place to a struck-through original plus `Resolved HH:MM UTC`
  (outcome `amended`, no new ping). A digest line, an email, a rejected edit (Telegram
  400) or three transport failures instead fold a `Since last digest: Resolved ...`
  line into the next delivery that happens anyway.

A candidate with no premise behaves as before. Probes live in
`roster/switchboard/tools/insight/premises.py`; producers pass `premise=` to
`propose_insight_candidate`.
