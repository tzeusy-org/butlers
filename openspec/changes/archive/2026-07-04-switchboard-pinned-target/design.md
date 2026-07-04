## Context

`ingest_v1()` (`roster/switchboard/tools/ingestion/ingest.py`) already resolves
one deterministic `route_to` bypass — email thread-affinity — ahead of global
ingestion-policy rule evaluation, and embeds the resulting `PolicyDecision`
into `message_inbox.request_context` via `_build_request_context()`. Every
downstream consumer of that context (`core_tools/_switchboard.py`'s `ingest`
tool, `DurableBuffer.enqueue`/scanner, and the "Pre-resolved triage bypass"
branch in `pipeline.py::PipelineOrchestrator.process()`, lines ~1530-1703)
reads `request_context["triage_decision"]` / `["triage_target"]` generically —
none of them special-case *how* the decision was produced. This means a new
bypass source only needs to plug into `ingest_v1()`; nothing downstream needs
to change.

## Goals / Non-Goals

**Goals**
- Let a caller (initially: per-butler dashboard chat, bu-mj2k2) pin an envelope
  to a specific butler and have it route there deterministically.
- Reject unknown/non-routable pins loudly at the ingest boundary instead of
  falling through to classification or silently misrouting.
- Zero behavior change when `pinned_target` is absent.

**Non-Goals**
- Wiring the dashboard conversation POST to actually set `pinned_target`
  (bu-mj2k2's scope).
- Sticky follow-up routing / `routed_butler` persistence (bu-p6ey8.1's scope).
- Any DB migration (only bu-p6ey8.1 may add one, per the epic's serialization
  rule).

## Decisions

**Field placement: `IngestControlV1.pinned_target`, not a new top-level
envelope field.** `control` is already the home for routing/queueing
directives (`policy_tier`, `ingestion_tier`, `idempotency_key`). A pin is a
directive, not payload content. Alternative considered: a top-level
`target` sibling to `source`/`event`/`sender` — rejected, since it would
imply the caller *knows* the destination the way a `route.v1` envelope's
`target.butler` does, blurring the ingest/route boundary; `control` keeps it
scoped as "how to handle this ingest," consistent with the rest of the class.

**Validation source: `_load_available_butlers(pool)` (routing.classify),
`routable_only=True, butler_only=True`.** This is the exact candidate set LLM
classification already uses to pick a target — reusing it means a pin can
never route somewhere classification itself would refuse to (quarantined,
stale, staffer-typed, or unregistered). Alternative considered: a lighter
existence-only check against `butler_registry` — rejected, because a pin to a
technically-registered-but-quarantined butler would silently blackhole the
dashboard conversation exactly the way this bead exists to prevent.

**Precedence: pinned_target is checked before thread-affinity, not after.**
In production the two never collide (thread-affinity only fires for
`source.channel == "email"`; pins are for `dashboard`), so ordering is not
safety-critical, but pinned_target represents an explicit, caller-asserted
routing decision for *this specific request* — a stronger signal than
historical thread affinity — so it is given top precedence for clarity and to
avoid surprise if the two ever do overlap in a future channel.

**Failure mode: reject via `ValueError`, mirroring the existing envelope-
validation failure path**, rather than degrading to `pass_through`. `ingest_v1`
already raises `ValueError` for schema-invalid envelopes and the MCP tool
wrapper (`core_tools/_switchboard.py::ingest`) already catches `ValueError` and
returns `{"status": "error", "error": str(exc)}`. Fail-open (falling through to
classification) was rejected: a misspelled or stale pin silently reclassifying
into some unrelated butler is a worse failure mode for a confirm-loop
dashboard chat than a loud, immediate rejection the caller can retry.

**No changes to `pipeline.py`, `buffer.py`, or `core_tools/_switchboard.py`.**
The generic `triage_decision`/`triage_target` plumbing already carries any
`route_to` decision end-to-end regardless of `matched_rule_type`. Adding
`"pinned_target"` to `ingest.py`'s `_ALLOWED_RULE_TYPES` telemetry allowlist is
the only touchpoint outside `ingest.py`/`contracts.py`.

## Risks / Trade-offs

- **[Risk]** Extra DB round-trip (`_load_available_butlers`) on every pinned
  ingest → **Mitigation**: only executed when `pinned_target` is set (rare;
  today exclusively dashboard-chat traffic), not on the hot unpinned path.
- **[Risk]** A butler quarantined *after* a conversation is pinned to it would
  reject follow-up messages → **Mitigation**: out of scope here; sticky
  `routed_butler` re-validation is bu-p6ey8.1's concern, and rejection (not
  silent misroute) is the correct behavior either way.

## Migration Plan

No data migration. Purely additive optional field; existing envelopes without
`control.pinned_target` are unaffected. Deploys as a normal code change.

## Open Questions

None outstanding — envelope-pin vs. policy-rule approach was owner-confirmed
2026-07-03 (epic bu-p6ey8 description).
