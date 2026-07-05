# Switchboard Rule Promotion: Learn Deterministic Rules from Repeated LLM Verdicts

## Why

In the trailing 100 days, Switchboard's LLM triage path ran 5,154 sessions while
the deterministic `ingestion_rules` engine handled only 148 rule-routed events —
most of those 5,154 sessions are the LLM re-deciding a verdict it already gave
for the same sender before (same sender → same target butler, every time), at
LLM cost and per-event latency. `switchboard.ingestion_rules` already supports
the condition types needed (`sender_domain`, `sender_address`, `header_condition`,
`source_channel`) with `route_to:<butler>` / `skip` / `metadata_only` actions —
the rule engine is not the gap. Nothing today turns repeated LLM agreement into
a rule; a human has to notice the pattern and hand-write it, and only ~15
mostly-seed rules exist.

There is also no first-class, queryable record of "the LLM decided X for sender
Y" to mine in the first place — `ingestion_events.triage_decision` is populated
only by the rule engine at accept time (before any LLM runs), and
`switchboard.routing_log` doesn't distinguish an LLM-decided dispatch from a
rule-bypassed one. Building that mining substrate is a prerequisite.

Full narrative, live evidence numbers, and rejected-alternative rationale:
`docs/plans/2026-07-06-switchboard-rule-promotion-design.md`.

## What Changes

- **New verdict mining substrate**: `switchboard.routing_verdict_log`, written
  at each of the pipeline's existing triage-bypass sites (rule bypass for
  `route_to`/`skip`/`metadata_only`) and at the LLM `route_to_butler` resolution
  site, tagged with which mechanism produced the verdict
  (`llm`/`rule`/`pinned`/`spot_check`). No pipeline dispatch behavior changes —
  this only adds durable recording alongside the existing bypass/dispatch code.
- **New promotion trigger**: a periodic scan groups verdict-log rows by
  normalized sender identity + channel; when N consecutive LLM verdicts agree
  (same action, same target) and span at least two distinct calendar days (an
  evidence-quality guard against single-burst false positives), it upserts a
  `switchboard.rule_promotion_suggestions` row rather than writing the rule
  directly.
- **New approvals-surface integration**: promotion (and demotion) suggestions
  render in the dashboard approvals surface. Every suggestion — including
  `skip`/`metadata_only` for clearly-automated senders — requires an explicit
  owner confirm action before an `ingestion_rules` row is created; automated
  senders get a batched one-tap "confirm all" affordance instead of unattended
  auto-write, per RFC 0021's human-confirmed-ratchet disposition (2026-07-02),
  which the design doc calls out explicitly since it revises a literal reading
  of this capability's originating bead text.
- **New demotion path**: promoted rules are spot-checked (1-in-K of matches
  still run through the LLM instead of bypassing) to detect drift; sustained
  disagreement creates an owner-confirmed demotion suggestion, mirroring the
  existing execution-failure-triggers-demotion pattern in `autonomy-suggestions`.
- **Rule provenance**: `ingestion_rules` gains a nullable
  `promoted_from_suggestion_id` FK and a `created_by = 'promotion'` convention
  value (no schema break — `created_by` is already unconstrained TEXT).
- **No changes to existing rule evaluation, condition schemas, or the pipeline
  bypass mechanism** — this composes entirely on top of `ingestion-policy`'s
  existing `IngestionPolicyEvaluator` and `MessagePipeline`'s existing
  pre-resolved-triage bypass (proven reusable with zero `pipeline.py` dispatch
  changes by the prior dashboard→butler pinned-target work).

## Impact

- Affected specs: `ingestion-policy` (new `created_by` value + provenance
  column), `dashboard-approvals` (new suggestions surface), new spec
  `switchboard-rule-promotion` (verdict log, promotion trigger, suggestion
  lifecycle, spot-check demotion).
- Affected code (implementation beads, not part of this change): switchboard
  migrations, `src/butlers/ingestion_policy.py`, `src/butlers/modules/pipeline.py`,
  `roster/switchboard/api/router.py`, dashboard approvals frontend.
- Depends on bu-qeaou (sender identity normalization, in flight) for the
  long-term normalized join key; this change's own mining substrate computes a
  local normalization independently so it is not hard-blocked on bu-qeaou's
  timeline (see design doc "Dependency on bu-qeaou" section).
- No migration in this change itself — this is a design-only change; beads
  implementing it will carry their own migrations.
