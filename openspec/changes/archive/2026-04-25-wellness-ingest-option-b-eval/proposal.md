# Option B Evaluation: Pre-LLM Hook for Wellness Envelope Translation

**Date:** 2026-04-25  
**Status:** Research / Architecture  
**Discovered-from:** bu-k5l35.3.2 (Option A shipped as PR #1126)

---

## Background

Option A (PR #1126) routes each `wellness/google_health` envelope through a full
LLM session on the Health butler, which calls `wellness_ingest_envelope(context)`
exactly once. The translation inside that tool is fully deterministic
(`translate_wellness_envelope` in `roster/health/tools/wellness_ingest.py` —
a pure predicate-routing table with no LLM involvement). The LLM session exists
solely to *invoke* the tool, not to reason about the data.

Option B asks whether the LLM middleman can be eliminated for high-volume
passive signals (hourly steps, per-session sleep).

---

## 1. Where the Hook Would Live

### Current flow

```
google_health connector
  → ingest_v1 (Switchboard MCP)        # persists to message_inbox, assigns triage_decision
  → pipeline.process()                  # reads triage_decision
    → policy bypass if triage_decision == "route_to"
    → OR LLM spawn → route_to_butler("health") → route.execute
      → Health butler spawns LLM session
        → LLM calls wellness_ingest_envelope(context)
          → translate_wellness_envelope()  # deterministic, no LLM needed
```

There are **two concrete insertion points** for an Option B hook:

#### Option B1 — Switchboard ingestion policy rule (`route_to` bypass)

The pipeline already has a fully-working pre-LLM bypass path
(`pipeline.py` lines 1340–1466). When `triage_decision == "route_to"` and
`triage_target` is set in `request_context`, the pipeline dispatches directly
to the target butler's `route.execute` tool without spawning an LLM session.

To wire this for wellness envelopes, add an ingestion rule scoped to
`global` or `connector:google_health:*` that matches
`source_channel = "wellness"` and produces `action = "route_to:health"`.
This is supported today: `ingestion_policy.py` has `_match_source_channel`
and the pipeline already reads and honours `triage_decision` / `triage_target`
from `request_context`.

**Files to touch:**
- `src/butlers/ingestion_policy.py` — no code change needed; the existing
  `source_channel` rule type already handles this.
- A DB-level ingestion rule row in the `ingestion_rules` table for `scope =
  "global"` with `conditions = [{"source_channel": "wellness"}]` and
  `action = "route_to:health"`. This can be seeded in a Switchboard migration.
- `roster/switchboard/migrations/007_wellness_route_rule.py` (new migration).

The policy bypass path routes to `route.execute` on the Health butler.
The Health butler's `route.execute` handler receives the raw envelope in
`input.prompt` / `input.context` and still spawns an LLM session on the
Health butler side — it just eliminates the Switchboard-side LLM spawn.

> **Half-measure caveat:** B1 alone saves the Switchboard-side LLM session but
> the Health butler still spawns its own LLM to call `wellness_ingest_envelope`.
> To eliminate *that* session too, see B2.

#### Option B2 — Dedicated ingest HTTP endpoint on the Health butler

Add a `POST /api/butlers/health/wellness-ingest` FastAPI route (in
`roster/health/api/router.py`) that calls `translate_wellness_envelope()`
directly — bypassing both LLM sessions entirely.

The google_health connector would need a second submission path: in addition to
(or instead of) submitting to the Switchboard MCP `ingest` tool, it would POST
directly to the Health butler's HTTP endpoint. This is architecturally
straightforward but it breaks the Switchboard-as-single-ingress contract and
creates a direct connector→butler coupling outside the MCP routing fabric.

**Files to touch (B2):**
- `roster/health/api/router.py` — new `/wellness-ingest` endpoint.
- `roster/health/api/models.py` — request/response Pydantic models.
- `src/butlers/connectors/google_health.py` — add a second submit path or
  replace Switchboard submission for wellness channel.
- Remove the `wellness_ingest_envelope` MCP tool registration (optional
  cleanup), or keep it for ad-hoc/interactive use.
- New tests for the endpoint and updated connector tests.

**Alternative B2a — Switchboard calls the Health butler API endpoint directly**
(instead of teaching the connector about it). The Switchboard's ingestion
pipeline, upon seeing `source_channel = "wellness"`, makes an HTTP POST to the
Health butler's `/wellness-ingest` endpoint. This preserves single-ingress but
adds a non-MCP cross-butler call path.

#### Recommendation on placement

B1 is the natural seam: the ingestion policy bypass mechanism already exists and
is tested. It eliminates the Switchboard-side LLM but not the Health butler-side
LLM. B2 eliminates both but requires more code and breaks the single-ingress
contract. See recommendation section.

---

## 2. Latency Comparison

### Option A (current): two LLM sessions

| Step | Estimated cost |
|---|---|
| Connector → Switchboard `ingest` MCP call | ~5–20 ms |
| Switchboard pipeline: queue + LLM spawn | 300–800 ms |
| LLM session startup (SDK + model init) | 200–600 ms |
| LLM `route_to_butler("health")` call | 50–150 ms |
| Health butler `route.execute` → LLM spawn | 200–600 ms |
| LLM `wellness_ingest_envelope(ctx)` call | 20–50 ms |
| `translate_wellness_envelope()` execution | < 5 ms |
| `memory_store_fact()` DB write | 10–30 ms |
| **Total (median estimate)** | **~1–3 s** |

The LLM spawns dominate. For passive signals that fire every 30–60 minutes,
this is not a user-visible latency concern, but each session consumes compute
resources and incurs token cost.

### Option B1 (policy bypass, Health LLM still runs)

Eliminates the Switchboard-side LLM spawn only:

| Step | Estimated cost |
|---|---|
| Connector → Switchboard `ingest` MCP | ~5–20 ms |
| Policy bypass: direct `route.execute` call | 20–80 ms |
| Health butler: LLM spawn | 200–600 ms |
| LLM `wellness_ingest_envelope(ctx)` call | 20–50 ms |
| `translate_wellness_envelope()` + DB write | 15–35 ms |
| **Total (median estimate)** | **~0.3–0.8 s** |

Saves ~50–70% of latency.

### Option B2 (both LLMs eliminated)

| Step | Estimated cost |
|---|---|
| Connector → Health butler HTTP endpoint | 5–20 ms |
| `translate_wellness_envelope()` | < 5 ms |
| `memory_store_fact()` DB write | 10–30 ms |
| **Total (median estimate)** | **~15–55 ms** |

~20–100× faster than Option A on end-to-end latency. For passive background
signals this speed difference is invisible to users but meaningful for
throughput during backfill (30 days × 7 resources = ~210 envelopes in a burst).

---

## 3. Cost Comparison

Using Claude Sonnet 4.5 pricing as a rough proxy: ~$3/MTok input, ~$15/MTok
output, with prompt caching reducing subsequent input cost by ~90%.

### Per envelope under Option A

A wellness routing session is minimal: the Switchboard prompt is ~1–2 k tokens
(system prompt + envelope context), the Health butler session is similar.
Conservative estimate:

- Switchboard session: ~1 500 input + ~200 output → ~$0.0050
- Health butler session: ~1 500 input + ~200 output → ~$0.0050
- **Total per envelope: ~$0.010**

At 7 resources × 48 polls/day (30-min cadence) = ~336 envelopes/day:
**~$3.36/day ($100/month)** just for passive wellness ingestion.

Prompt caching reduces this significantly if sessions hit the cache: likely
~$0.001–0.003 per envelope with caching, so ~$30–100/month range.

### Option B1 (Switchboard bypass only)

Eliminates one LLM session. Cost halved: **~$15–50/month** for passive signals.

### Option B2 (no LLM)

Zero LLM cost for passive signals: **$0/month** for the ingest path.
Cost arises only for interactive queries (the `health_sleep_latest` etc. tools
still spawn sessions when the user asks questions — unchanged by Option B).

---

## 4. Complexity and Risk

### Option B1 (policy bypass via ingestion rule)

**Code footprint:** ~1 new Switchboard migration file + 0 source changes.
The entire mechanism (source_channel rule → `route_to` action → policy bypass
in pipeline.py) already exists and is tested by
`roster/switchboard/tests/test_triage_ingest_integration.py`.

**Tests needed:**
- Integration test: wellness envelope with `source_channel = "wellness"` → policy
  bypass fires → `route.execute` called on health butler without LLM spawn.
- The existing switchboard conformance and triage tests cover the mechanics.

**Invariants to preserve:** None broken. The envelope still arrives at
`route.execute` on the Health butler, so the Health butler's CLAUDE.md
instruction ("call `wellness_ingest_envelope(context)` exactly once") still
applies. Idempotency, sender validation, predicate mapping, Prometheus counters
— all unchanged.

**Risk:** Low. The bypass path is production-battle-tested by other channels
(OwnTracks, home_assistant, gaming use it). The wellness channel is single-owner
and structurally simpler than email or Telegram.

### Option B2 (dedicated ingest endpoint)

**Code footprint:** ~5–8 files changed or created. New HTTP endpoint with full
Pydantic request/response models, auth/ownership validation (the endpoint must
enforce single-owner to match `_get_primary_google_identity`), Prometheus
counters matching the MCP tool's contract, and connector changes.

**Tests needed:**
- HTTP endpoint unit tests (success path, sender rejection, malformed payload,
  idempotency, unknown predicate).
- Connector integration tests for the new submit path.
- Regression tests ensuring the MCP `wellness_ingest_envelope` tool is not
  accidentally bypassed for interactive use.

**Invariants to preserve:**
- Single-owner check (`_get_primary_google_identity`) — must be reproduced in
  the endpoint.
- Idempotency key forwarding to `memory_store_fact`.
- Prometheus counter `health_wellness_ingest_total` with `predicate`/`outcome`
  labels.
- Malformed-payload tolerance (empty metadata → `skipped_malformed_payload`,
  not an HTTP 500).
- Fan-out correctness (activity → 2 facts, sleep with stages → 2 facts).

**Risk:** Medium. Breaking the single-ingress contract adds a direct
connector→butler coupling that sidesteps the Switchboard's deduplication,
policy evaluation, `public.ingestion_events` audit trail, and dead-letter queue.
A bug in the endpoint bypasses all these safety layers.

**If routing through Switchboard to health HTTP endpoint (B2a):** keeps
single-ingress but adds a non-standard cross-butler HTTP call from inside the
Switchboard pipeline, creating a new failure mode (HTTP timeout from the
Switchboard's pipeline process). Not recommended.

---

## 5. Recommendation

**Adopt B1 as the next incremental step; stay on B2 as optional future work.**

Rationale:

1. **B1 is essentially free.** The ingestion policy bypass is already shipped
   and tested. Seeding one DB migration rule costs ~20 lines and zero risk to
   core invariants.

2. **B1 still runs an LLM on the Health butler side.** That is actually a
   feature: the Health butler's LLM session can handle occasional ad-hoc
   envelopes, edge cases, and future enrichment logic that may be added
   (e.g., attaching a calendar event summary as a memory annotation).
   Eliminating the Health butler LLM entirely (B2) bets that wellness ingestion
   will never need LLM reasoning, which may not hold long-term.

3. **B2 carries architectural debt.** Bypassing the Switchboard's dedup layer,
   audit trail (`public.ingestion_events`), and dead-letter queue for a
   marginal cost saving is not worth it at the current scale (336 envelopes/day,
   ~$3–100/month depending on caching). Revisit if ingestion volume grows 10×
   or if cost instrumentation shows wellness is a material budget line.

4. **Hybrid path is naturally B1 + optional later B2.** Shipping B1 does not
   preclude B2 later; B1's routing rule can be replaced or augmented.

**Stay on A** if the Switchboard-side LLM cost is negligible in practice
(confirmed by pricing dashboard) and the added migration complexity of B1 is
not worth it. B1 makes sense once cost instrumentation confirms wellness
sessions are a non-trivial fraction of monthly spend.

---

## 6. Follow-up Implementation Bead (if adopting B1)

**Title:** `feat(switchboard): seed wellness route_to ingestion rule for policy bypass`

**Acceptance criteria:**

1. A new Switchboard migration (`007_wellness_route_rule.py`) inserts an
   `ingestion_rules` row with `scope = "global"`, `conditions =
   [{"source_channel": "wellness"}]`, `action = "route_to:health"`,
   `rule_type = "source_channel"`.
2. The existing pipeline policy-bypass path activates for wellness envelopes:
   `triage_decision == "route_to"` and `triage_target == "health"` are set in
   `request_context` without an LLM spawn on the Switchboard side.
3. The Health butler still receives the envelope via its `route.execute` tool
   and spawns an LLM session as before — no Health butler changes needed.
4. Integration test in `roster/switchboard/tests/` verifies that a wellness
   envelope triggers the policy bypass (not the LLM spawn path).
5. Existing `translate_wellness_envelope` invariants (sender check, idempotency,
   predicate mapping, Prometheus counter, fan-out) pass without modification.
