# Dashboard fast-answer runtime

Dashboard system questions can use a Switchboard-owned API runtime before the existing Spawner
continuation. The governing contract is the owner-adopted
[dashboard fast-answer change](../../openspec/changes/define-dashboard-fast-answer-runtime/design.md).
This implementation does not change domain routing or authorize a new tool.

## Admission and registered reads

The runtime registers one durable message/session identity before catalog or provider work. Both
classification and phrasing must resolve exact `cheap`, catalog-backed API candidates without tier
fallthrough. Only one schema-invalid classification retry is permitted; provider failures do not
retry or fail over.

The Memory module supplies its owning held-sensitivity catalog search with `limit=3`. Missing held
configuration, embedding/search failure, or any malformed candidate produces `unavailable`, never
an empty result. The deterministic rule retains every returned candidate and selects Concierge only
when the first two agree and all highest-score ties agree. RRF scores remain ranking evidence.

Structured admission is data-only. The whole plan must name no more than three live tools in the
exact sixteen-entry `FAST_ANSWER_CONCIERGE_TOOLS_V1` grant map. Actual reads use the registered
Switchboard-to-Concierge MCP route, target validation, enabled module state, and sanctioned RFC 0030
views. Catalog pointers never become canonical read authority. A plan with no reads falls back
before effects because it supplies no target-produced grounding. Domain and non-answer lanes keep
the existing Spawner and `route.execute` behavior.

Once a read starts, a failure cannot restart classification or replay the read plan. Reply sources
come from validated target envelopes, not model prose. The registered `conversation_reply` boundary
accepts only the same live runtime's prepared message, conversation, generation, text, and citations.

## Durable lease, Stop, and recovery

Core migration `core_267` follows actual `core_265`; held `core_266` is not a dependency. The
Switchboard runtime role uses narrow security-definer functions. The runtime table has forced RLS,
including after broad bootstrap grants. Legacy generationless release/completion/acknowledgement
cannot settle a fast claim, and a second legacy session cannot register while that claim is active.
A pre-read fallback releases its fast claim and permits the ordinary legacy continuation.

A boot-scoped owner and generation fence every runtime write. Heartbeats run at most twenty seconds
apart and renew a sixty-second lease from the database clock. Startup and periodic reconciliation
run at most sixty seconds apart. Healthy renewal can continue beyond registration minute fifteen.
After expiry, takeover captures immutable last lease expiry `L` and deadline `D = L + 15 minutes`.
Repeated sweeps and restarts do not move either timestamp or reconstruct execution.

Message-scoped Stop uses the existing registered `cancel_session` tool. Confirmed cancellation
requires settled invoke/reply claims and a durable acknowledgement. A cancelled MCP transport or
reply with an unprovable outcome retains pending reconciliation/cancellation. Receipt evidence wins;
otherwise the first available sweep at or after `D` records bounded ambiguity without replay.
Storage outages suspend the timing promise and preserve uncertainty.

## Observation and verification

A definitive classification stores immutable `intent_lane`, independently of routing target. The
SSE stream observes answer turns for forty-five seconds and other/unknown/legacy turns for three
hundred seconds, anchored to its original observation start. Late knowledge does not restart the
clock. Ending this observation does not Stop execution or suppress a later persisted reply.

The new integration owning file is `tests/integration/test_dashboard_fast_answer_db.py`. It uses
migrated PostgreSQL, real per-acquisition roles, independent durable readback, reciprocal races,
process restart, and the operational supervisor. Its twenty-question benchmark uses seeded
synthetic catalog data, fixed-latency API stubs, the registered MCP route, and durable SQL replies.
Its result is emitted as a JUnit property. It measures hermetic orchestration, not live provider,
embedding, production latency, or deployment. Expected normal calls are forty provider calls,
twenty reads, twenty replies, and zero CLI spawns, with p95 below three seconds.

Local execution currently cannot earn these SQL or benchmark claims: the ordinary Docker fixture
was denied access to the socket before any SQL ran. Authored integration tests require genuine
non-skipped execution at the resulting head in hosted CI. DB-free software controls, collection,
and static checks have separate scope. Native package adoption/archive, genuine review, current
normal checks, protected queue qualification, actual squash delivery, and any live activation are
separate obligations.

Each actual API provider attempt writes content-blind `duration_ms` under the same
fast-runtime fence, alongside purpose/model/catalog/timeout/outcome and session-bound
token usage. Repeated Stop injects cancellation once and waits for the same durable
settlement. Handled success and failure both finalize message ingestion; a failed or
unavailable ingress write reports failure without authorizing whole-turn replay.
The owner-only backup stage preserves durable fast-runtime rows as described in
[backup and restore](../operations/backup-restore.md).

Lease timestamps are sampled after the owning turn/runtime locks are acquired; each reconciled
row refreshes the database clock separately. Waiting for a lock cannot renew an expired lease,
continue a phase after expiry, or postpone a reached reconciliation deadline using an earlier clock.

A cancelled provider call records elapsed duration against the database-issued array position of
its exact `started` attribution. This metadata-only settlement may cross Stop for the same current
owner/generation and live lease, including an already-cancelled turn. It cannot renew a lease,
release a claim, start work, or persist a reply. Changed identities, an already-settled different
outcome, expiry, or takeover refuse the write. Exact cancellation receipt replay is idempotent.
If this attempt metadata cannot be durably settled, the adapter preserves an unknown outcome;
that failure cannot confirm Stop. The migrated held-lock and Stop-attribution companions remain
authored and locally unrun, pending genuine non-skipped execution.
