# Education Butler

An adaptive tutor. It diagnoses what the owner already knows, decomposes a topic into a
concept graph, teaches one concept per session, and schedules spaced-repetition reviews so
comprehension becomes lasting mastery.

- **Identity and scope:** [`roster/education/MANIFESTO.md`](../../roster/education/MANIFESTO.md)
- **Required behavior:** [`butler-education` spec](../../openspec/specs/butler-education/spec.md)
- **Schedules, modules, and port:** [`roster/education/butler.toml`](../../roster/education/butler.toml)

## Related Pages

- [Switchboard Butler](switchboard.md) -- routes learning-related messages here
- [General Butler](general.md) -- holds general knowledge that is not part of a structured curriculum

## Curriculum lifecycle

Maps begin as addressable drafts. Starting a teaching flow writes its map and
flow state on one database connection in one transaction; either both commit or
neither does. Curriculum generation persists and sequences the concept graph
before requesting activation through the status tool. Empty graphs remain drafts.

The status tool locks the parent map before checking transitions and content.
Database triggers independently refuse direct activation of an empty map and
removal of the final node of an active map. Node removal writes the same parent
row, so concurrent activation/deletion and two deletions serialize; deleting the
parent itself may cascade normally. These guards refuse the operation, preserving
its rows and status, rather than silently repairing it.

Migration education_006 abandons legacy active maps with no concepts before
installing enforcement. Each transition appends a system-attributed audit entry
containing only the map ID and a fixed reason, never its title or curriculum.
It also provisions the existing goal metadata and nullable learning-sequence
fields already used by the teaching and curriculum tools. Downgrade refuses
unsettled drafts; it preserves abandonment, audit history and these data columns.

Weekly cleanup enumerates map rows, including flow-less drafts. Zero-node drafts
older than 24 hours are abandoned; populated unfinished maps become stale after
30 days without node activity. The separate teaching-flow sweep uses its
last-session clock; a recent session does not replace node activity in the
registered map sweep, and a recent node does not replace the flow clock.
Recent drafts and completed or all-mastered maps are
preserved. Review cleanup can resume after abandonment without rewriting state.
The dashboard keeps drafts visible, distinguishes fresh/slow/stalled setup from
an integrity fault, and names failed review sources while retaining successful
reviews. A partial result never produces a complete total or an all-clear.
An abandoned selection remains addressable through its identity-matched detail
query, retaining its status badge and reactivation action after it leaves the
active/draft lists. Unavailable progress sources show a degraded state rather
than claiming that no active maps exist.

Curriculum-request admission remains receipt-backed: acceptance precedes detached
work, `uq_curriculum_requests_one_open` guards the one-open slot, and terminal
settlement and abandoned-receipt cleanup retain their existing behavior.

Post-deploy inspection of the named historical phantom and deployed zero-node
active-map count remains a separate required verification; source and disposable
PostgreSQL tests do not attest deployed data.
