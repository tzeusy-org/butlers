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
