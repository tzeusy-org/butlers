# Design — Switchboard Rule Promotion

## Context

Full design rationale, live evidence numbers pulled against `butlers-db-dev`,
and the rejected-alternative discussion live in
`docs/plans/2026-07-06-switchboard-rule-promotion-design.md`. This file records
the implementation-shaping decisions and trade-offs for the spec deltas below.

Existing substrate this change composes on top of (all [Observed]):

- Rule storage + evaluation: `switchboard.ingestion_rules`
  (`roster/switchboard/migrations/003_switchboard_routing.py:172-216`),
  `IngestionPolicyEvaluator.evaluate()` (`src/butlers/ingestion_policy.py:594`).
- Pipeline's generic pre-resolved-triage bypass:
  `MessagePipeline.process()` (`src/butlers/modules/pipeline.py:1869-2104`) —
  already reused by thread-affinity, seed rules, and the dashboard→butler
  pinned-target feature with zero dispatch-logic changes.
- LLM verdict resolution: `_extract_routed_butlers()`
  (`pipeline.py:651`, consumed at `:2711`) — the single point where both the
  CLI-session path and PR #2960's structured tool-use fast lane converge on a
  resolved `route_to_butler` decision.
- The pattern being mirrored (not literally extended — see below):
  `autonomy_tracker.py` / `autonomy_suggestions.py`
  (`src/butlers/modules/approvals/`), specs `autonomy-tracker` /
  `autonomy-suggestions`.

## Decisions

**D1 — Sibling capability, not a literal extension of `autonomy_suggestions`.**
`autonomy_suggestions.resulting_rule_id` is a hard FK to `approval_rules`
(tool-call approval rules) and its fingerprint shape is
`(tool_name, safety-critical args)`. Ingestion-rule promotion's natural key is
`(sender_key, source_channel)` and its target is `switchboard.ingestion_rules`,
not `approval_rules`. Forcing this through the existing table means a
polymorphic FK or a weakened integrity guarantee on a working, unrelated
schema. A sibling table (`switchboard.rule_promotion_suggestions`) with the
same lifecycle shape (`pending_review → confirmed|dismissed|superseded`)
gets the reuse benefit (one mental model, one dashboard surface pattern)
without contorting the existing autonomy ladder. Promotion and demotion
share that one lifecycle rather than forking it: they are distinguished by a
`suggestion_kind` discriminator column (`promotion` | `demotion`, shipped in
sw_020, tied to column population by a kind-shape CHECK constraint) instead
of folding a rule-outcome value like `demoted` into `status` — a promotion
suggestion is never itself "demoted"; a *rule* gets demoted as the side
effect of confirming a demotion-kind suggestion (see spec.md "Requirement:
Rule Promotion Suggestion Data Model"). `superseded` remains reserved
`status` vocabulary with no trigger condition defined by any bead so far;
pinning that down is tracked separately (bu-2djc4), not decided here.
Trade-off accepted: two suggestion tables exist in the system rather than
one generalized table; judged worth it to avoid a cross-domain polymorphic
FK.

**D2 — Verdict mining substrate is a new table, not a `routing_log` extension.**
`switchboard.routing_log` records `route.execute` dispatches but has no
`skip`/`metadata_only` rows (no `route.execute` call happens for those) and no
column distinguishing an LLM-decided dispatch from a rule-bypassed one.
Retrofitting both onto `routing_log` risks destabilizing an existing,
load-bearing observability table; a new table (`routing_verdict_log`) scoped
exactly to "what did the triage layer decide, and how" is additive and
low-risk.

**D3 — Local sender normalization now, switch to bu-qeaou's shared column
later (Bead 7 in the design doc).** bu-qeaou (sender identity normalization) is
in flight but not merged. Hard-blocking this change's mining substrate on
bu-qeaou would stall Beads 1-4 on an external timeline for no correctness
reason — the existing regex normalization already inlined in
`ingestion_policy._extract_emails()` is sufficient to seed `sender_key` today.
Accepted trade-off: a short-lived duplication of normalization logic, closed
out by a small follow-up bead once bu-qeaou ships.

**D4 — All promotions are owner-confirmed; automated-sender tier gets a
batched one-tap, not unattended auto-write.** The bead's originating text
proposed literal auto-apply for `skip`/`metadata_only` of clearly-automated
senders. RFC 0021's owner disposition (2026-07-02) rejected "auto-minting rules
after N approvals" as crossing RFC 0019's line — a disposition recorded three
days before this bead was groomed, about the same category of action (minting
a standing rule from accumulated evidence without a human looking at it). This
design does not implement the literal auto-apply reading; every suggestion
requires an explicit confirm, with the UX cost kept small for the low-risk tier
via batching rather than skipped entirely. See the design doc's "Key decision"
callout for the full rationale and the explicit owner-override path if the
owner wants to record a narrower, freshly-scoped auto-apply disposition for
this specific tier.

**D5 — Evidence-quality gate: promotion requires >=2 distinct calendar days of
evidence, not just N consecutive verdicts.** Observed directly in
`switchboard.sessions` while researching this change: three GitHub Actions
CI-failure notifications from the same sender, routed identically, within ~2
minutes of each other (one flaky-CI burst). Raw "N-consecutive-agree" would
promote off single-incident noise; requiring real elapsed time between the
oldest and newest evidence event is a cheap structural guard.

**[flagged, not silently decided]** A bare `decided_at::date` difference
(unpinned timezone) has a midnight-boundary gaming hole: two verdicts 2
minutes apart straddling a day boundary would satisfy ">=2 distinct calendar
days" on the exact single-burst shape this gate is meant to reject. Bead 3
must either (a) pin the day boundary to UTC and add a minimum-elapsed-time
floor (e.g. `last_evidence_at - first_evidence_at >= interval '20 hours'`),
or (b) replace calendar-day counting with a pure elapsed-time floor. See the
design doc's Promotion Trigger section for the full callout; this doc does
not prescribe which of the two.

**D6 — Demotion via 1-in-K shadow LLM check, not passive drift detection.**
Once a rule bypasses the LLM, nothing observes it again by default. Rather than
waiting for a downstream failure signal (which doesn't exist for a
routing/skip decision the way it does for a failed tool execution), a
promoted rule's matches are spot-sampled: 1-in-K still runs the real LLM
classification in parallel/instead, and disagreement accumulates into a
rolling per-rule agreement score. This is the only place this change touches
`IngestionPolicyEvaluator`/`PolicyDecision` directly — one new boolean field
(`spot_check`) so `pipeline.py`'s existing bypass-vs-fallthrough branch can
route the sampled fraction through normal LLM classification instead of the
bypass, while still logging the comparison.

## Non-goals

- No change to `ingestion_rules`' condition schemas, evaluation order, or the
  REST CRUD/test/bulk surface for human-authored rules.
- No change to `MessagePipeline`'s dispatch behavior for non-promoted rules or
  thread-affinity routing.
- No fully-unattended rule minting (see D4) — this is an explicit non-goal
  even though the originating bead text suggested it for one tier.
- Domain-level (`sender_domain`) mining is flagged in the design doc as likely
  the larger long-term win but is deliberately out of scope for the first
  promotion-trigger cut (Bead 3 default; see design doc open question 3).
