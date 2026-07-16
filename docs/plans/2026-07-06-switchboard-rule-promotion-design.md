# Switchboard Rule Promotion — Learning Deterministic Rules from Repeated LLM Verdicts

**Date:** 2026-07-06
**Status:** Design (owner review requested before beads are filed)
**Related:** bu-p9z2u (this bead), bu-shk4p (owner gate, closed 2026-07-05), bu-qeaou
(identity enrichment / sender normalization — IN FLIGHT, dependency), RFC 0021
(decision loop / one-tap approvals — governs the approval-flow decision below),
openspec `ingestion-policy`, `autonomy-suggestions`, `autonomy-tracker`,
`dashboard-approvals` specs.

## Problem

In the trailing 100 days (measured live against `butlers-db-dev` on 2026-07-06):

| Metric | Value |
|---|---|
| `ingestion_events.triage_decision = 'skip'` | 170,378 |
| `triage_decision = 'pass_through'` (→ LLM classification) | 6,078 |
| `triage_decision = 'metadata_only'` | 234 |
| `triage_decision = 'route_to'` (rule-routed; only `health` has a live `route_to` seed rule today) | 148 |
| `switchboard.sessions` rows in the window (LLM triage sessions) | 5,154 |
| Distinct email senders in the window | 284 |

Only ~15 mostly-seed rules exist in `switchboard.ingestion_rules` (migration
`003_switchboard_routing.py`), covering a handful of finance/travel domains and
generic bulk-mail headers (`List-Unsubscribe`, `Precedence`, `Auto-Submitted`).
Every other inbound email that isn't `skip`-worthy falls through to a spawned LLM
classification session (`MessagePipeline.process()`,
`src/butlers/modules/pipeline.py`) that calls `route_to_butler`. Most of these
sessions are **re-deciding a verdict the LLM already gave for the same sender
before** — same sender, same target butler, every time — at LLM cost and ~seconds
of latency per event.

`switchboard.ingestion_rules` already supports exactly the condition types needed
(`sender_domain`, `sender_address`, `header_condition`, `source_channel`) with
`route_to:<butler>` / `skip` / `metadata_only` actions
(`src/butlers/ingestion_policy.py`, `IngestionPolicyEvaluator`). The rule engine
is not the gap. **The gap is that nothing turns repeated LLM agreement into a
rule automatically** — someone has to notice the pattern and hand-write it.

## What already exists (so this design doesn't rebuild it)

- **Rule storage + evaluation**: `switchboard.ingestion_rules`
  (`roster/switchboard/migrations/003_switchboard_routing.py:172-216`), evaluated
  by `IngestionPolicyEvaluator.evaluate()` (`src/butlers/ingestion_policy.py:594`),
  first-match-wins over a 60s TTL-cached, priority-ordered rule list. REST CRUD +
  dry-run test + bulk ops at `/api/switchboard/ingestion-rules`
  (`roster/switchboard/api/router.py:2918+`). Rule creation today is unguarded
  dashboard CRUD (audited, not approval-gated) — see "Approval flow" below for why
  *system-proposed* rules are treated differently.
- **Pipeline bypass**: `MessagePipeline.process()` already has a generic
  pre-resolved-triage bypass — if `request_context.triage_decision` is
  `route_to`/`skip`/`metadata_only`, the LLM session is skipped entirely
  (`pipeline.py:1869-2104`). PR #2896 (dashboard→butler pinned target) proved this
  bypass is reusable with **zero `pipeline.py` changes** for a new
  triage-decision source. A promoted rule plugs into the exact same path.
- **The proposal/approval pattern to mirror**: the progressive-autonomy-ladder
  (`src/butlers/modules/approvals/{autonomy_tracker,autonomy_suggestions}.py`,
  specs `autonomy-tracker` / `autonomy-suggestions`) already implements
  fingerprint → threshold → suggestion → confirm/dismiss → promoted-rule, with a
  dashboard banner (`autonomy-suggestions-banner.tsx`) and REST surface
  (`/api/approvals/suggestions`). This is the shape to copy. It cannot be reused
  **verbatim** — see "Why a sibling capability, not a literal extension" below.

## What's missing (the actual gap to build)

There is **no first-class, queryable record of "the LLM decided X for sender Y"**.
Investigated and ruled out:

- `ingestion_events.triage_decision`/`triage_target` are populated **only by the
  rule engine at accept time**, before any LLM runs
  (`roster/switchboard/tools/ingestion/ingest.py:1048-1049`). The eventual LLM
  verdict is never written back to this row.
- `switchboard.routing_log` records every `route.execute` dispatch
  (`roster/switchboard/tools/routing/route.py:557-571`) but has no column
  distinguishing "this dispatch came from an LLM `route_to_butler` call" vs. "this
  came from a rule bypass or thread-affinity," and records nothing for
  `skip`/`metadata_only` outcomes (no `route.execute` call happens for those).
- The closest existing trail is indirect: `sessions.ingestion_event_id` (set on
  the **target butler's own** sessions table, not the switchboard triage session —
  confirmed via `roster/switchboard/api/router.py:1821-1851`'s fanout query) joined
  back to `public.ingestion_events`, with the verdict itself buried in
  `sessions.tool_calls` JSONB (`{"name": "route_to_butler", "input": {"butler":
  ...}}`). I ran this join live against `butlers-db-dev` for the email channel,
  100-day window:

  ```
  217 distinct senders had >=1 attributable session
   16 of those (7.4%) show a single consistent target butler across >=3 sessions
    6 of those (2.8%) clear a stricter >=5 bar
  71 of 1,391 email-triggered sessions (5.1%) fall under the >=3-consistent senders
  ```

  This is a **lower-bound proxy, not a clean measurement** — the join can't tell
  a rule-bypassed dispatch from an actual LLM decision (both land in the same
  target-butler `sessions` row), which is exactly the instrumentation gap this
  design closes. Real yield should be measurably higher once genuinely
  LLM-only verdicts are isolated, and materially higher again once patterns are
  mined at the **domain** level rather than address level (see "Expected impact"
  below) — most "134 of 256 automated senders" are automated *vendors*
  (github.com, notifications from CI, etc.), not single addresses; multiple
  addresses at the same domain (`billing@`, `no-reply@`, `alerts@`) often share
  one verdict, and address-level mining structurally undercounts that.

Building the mining substrate is a prerequisite, not a footnote — see Bead 1.

## Dependency on bu-qeaou (sender identity normalization)

bu-qeaou is in flight, proposing: normalize sender identity at ingest, storing a
lowercased extracted address (`sender_address` or similar) alongside the raw
RFC-5322 display string (currently the only thing stored,
`ingestion_events.source_sender_identity`). Today, **no normalized column
exists** — the rule engine does its own throwaway regex extraction per-evaluation
(`IngestionPolicyEvaluator._extract_emails()`, `ingestion_policy.py:143-149`) and
never persists the result.

This design **keys all mining and matching off the normalized address**, because
without it, "same sender" degenerates into exact-string matching on
`"Name <addr>"` display strings, which fragments a single sender's history across
"GitHub <no-reply@github.com>" vs "no-reply@github.com" vs "GitHub
Notifications <no-reply@github.com>" — silently undercounting evidence and
missing real promotion opportunities.

**Decision on sequencing**: do not hard-block this feature's mining substrate
(Bead 1) on bu-qeaou merging. Bead 1 computes its own minimal normalization
(lowercase + regex-extract, same logic already inline in
`ingestion_policy.py`) into the new verdict-log table's own `sender_key` column,
independent of whatever column bu-qeaou lands on `ingestion_events`. Bead 7 (last
in the sequence, low priority) switches `sender_key` derivation over to read
bu-qeaou's shared column once it ships, deleting the duplicated regex. This
avoids a hard cross-bead dependency stalling Bead 1-4 on bu-qeaou's timeline,
at the cost of one intentionally short-lived duplication.

**Naming collision flag for whoever implements bu-qeaou**: `IngestionEventSummary
.sender_display` (`src/butlers/api/models/ingestion_event.py:86-89`) already
exists as a *different* concept — the API-resolved contact display name (via
`resolve_contacts_by_channel_bulk`), not the RFC-5322 display-name substring.
If bu-qeaou introduces a raw-display-name column, it should not be named
`sender_display` — pick something like `sender_display_name` to avoid the clash.

## Design

### 1. Verdict mining substrate (new)

New table `switchboard.routing_verdict_log`:

```sql
CREATE TABLE switchboard.routing_verdict_log (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ingestion_event_id UUID NOT NULL REFERENCES public.ingestion_events(id),
    sender_key         TEXT NOT NULL,       -- normalized lowercase address (see above)
    source_channel     TEXT NOT NULL,
    verdict_source     TEXT NOT NULL        -- 'llm' | 'rule' | 'pinned' | 'spot_check'
        CHECK (verdict_source IN ('llm', 'rule', 'pinned', 'spot_check')),
    verdict_action     TEXT NOT NULL        -- 'route_to' | 'skip' | 'metadata_only' | 'pass_through' | 'block'
        CHECK (verdict_action IN ('route_to', 'skip', 'metadata_only', 'pass_through', 'block')),
    verdict_target     TEXT,                -- butler name, only set when verdict_action = 'route_to'
    matched_rule_id    UUID REFERENCES switchboard.ingestion_rules(id),  -- set when verdict_source IN ('rule','spot_check')
    session_id         UUID REFERENCES switchboard.sessions(id),        -- set when verdict_source IN ('llm','spot_check')
    decided_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ON switchboard.routing_verdict_log (sender_key, source_channel, decided_at DESC);
CREATE INDEX ON switchboard.routing_verdict_log (verdict_source) WHERE verdict_source = 'llm';
```

Write hooks (all inside `MessagePipeline.process()`, `pipeline.py`, no new spawn
paths):

- **Rule bypass sites** (`pipeline.py:1875` route_to, `:2041` skip, `:2074`
  metadata_only): write one row with `verdict_source='rule'`,
  `matched_rule_id` = the rule that fired. This is cheap, synchronous, and gives
  us — for the first time — a durable per-sender history even for rule-bypassed
  traffic (useful for the demotion spot-check comparison in §4).
- **LLM verdict site** (`MessagePipeline.process()` immediately after
  `_extract_routed_butlers()` resolves `routed`/`acked`/`failed`): write one row
  per resolved `route_to_butler` target with `verdict_source='llm'`. The
  structured fast lane and CLI-session path both terminate at this extraction
  call, so one hook point covers both execution paths uniformly. Current LLM
  classification has no `skip` or `metadata_only` action: both the structured
  fast lane and standard CLI-session path offer `route_to_butler`; their
  dashboard-only classification variant also offers `file_bug_report`
  (`roster/switchboard/tools/routing/structured_classify.py`;
  `src/butlers/modules/pipeline.py`). A no-route fallback is a last-resort
  text/default route rather than an explicit LLM
  verdict and is deliberately excluded from promotion mining.

  **Future scope:** an LLM-level `skip` or `metadata_only` decision would need
  an explicit tool contract plus its own validated verdict-recording scenario.
  It is not implemented or assumed by this design.
- **Pinned-target site** (PR #2896's `control.pinned_target` bypass): write
  `verdict_source='pinned'`, excluded from promotion mining (it's already an
  explicit deterministic override, promoting it would be redundant).

This closes the exact gap identified above: after this lands, "how many times has
the LLM said X for sender Y" becomes a plain indexed query instead of a JSONB
excavation across every butler's `sessions` table.

### 2. Promotion trigger

A periodic scan (reuse Switchboard's existing scheduler/tick — no new cron
infra), grouped by `(sender_key, source_channel)`:

1. Skip senders already covered by an `enabled` `ingestion_rules` row (no point
   re-proposing what already exists).
2. Skip senders with an existing `pending_review` suggestion (bump
   `evidence_count` instead of creating a duplicate).
3. Pull the last N `routing_verdict_log` rows WHERE `verdict_source = 'llm'`,
   ordered by `decided_at DESC`.
4. **Promotion condition**: all N agree on `(verdict_action, verdict_target)`,
   AND N >= threshold (default 3, configurable), AND the N verdicts **span at
   least 2 distinct calendar days**.

The 2-distinct-days clause is a deliberate evidence-quality gate, not
boilerplate: I pulled a real example from `switchboard.sessions` while
researching this doc — three near-identical GitHub Actions CI-failure
notifications from `notifications@github.com`, all routed to `general`, all
within about two minutes of each other (a single flaky-CI burst). A raw
"N consecutive" rule with no time-spread requirement would have minted a
same-day burst into a standing rule on noisy, single-incident evidence. Requiring
the evidence to span real elapsed time is a cheap, high-value guard against that.

**[flagged, not silently decided]** "Calendar day" needs a pinned timezone
anchor, and naive date-difference counting has a real gaming hole at the
boundary: two verdicts 2 minutes apart — e.g. 23:59 and 00:01 — cross a
UTC calendar-day boundary and would satisfy ">=2 distinct calendar days"
under a `decided_at::date` (UTC, matching the column's `TIMESTAMPTZ` storage)
grouping, on the *exact same kind of single-burst evidence* this gate exists
to reject. Bead 3 should not implement this as a bare date-difference check.
Two ways to close the hole, either is acceptable and this doc does not
prescribe which: (a) keep the calendar-day framing but pin it to UTC *and*
add a minimum-elapsed-time floor (e.g. also require
`last_evidence_at - first_evidence_at >= interval '20 hours'`) so a
midnight-adjacent burst still fails; or (b) drop calendar-day counting
entirely in favor of a pure elapsed-time floor, which sidesteps timezone
anchoring altogether at the cost of losing the "2 distinct days" framing's
intuitive read. Owner/implementer should pick one before Bead 3 ships.

5. On trigger, upsert `switchboard.rule_promotion_suggestions`:

```sql
CREATE TABLE switchboard.rule_promotion_suggestions (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sender_key               TEXT NOT NULL,
    source_channel           TEXT NOT NULL,
    proposed_rule_type       TEXT NOT NULL,   -- 'sender_address' | 'sender_domain'
    proposed_condition       JSONB NOT NULL,
    proposed_action          TEXT NOT NULL,   -- 'route_to:<butler>' | 'skip' | 'metadata_only'
    evidence_count           INTEGER NOT NULL,
    first_evidence_at        TIMESTAMPTZ NOT NULL,
    last_evidence_at         TIMESTAMPTZ NOT NULL,
    is_clearly_automated     BOOLEAN NOT NULL DEFAULT FALSE,
    status                   TEXT NOT NULL DEFAULT 'pending_review'
        CHECK (status IN ('pending_review','confirmed','dismissed','superseded','demoted')),
    created_rule_id          UUID REFERENCES switchboard.ingestion_rules(id),
    dismissal_reason         TEXT,
    cooldown_until           TIMESTAMPTZ,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at               TIMESTAMPTZ,
    decided_by               TEXT
);
CREATE INDEX ON switchboard.rule_promotion_suggestions (status, created_at);
CREATE UNIQUE INDEX ON switchboard.rule_promotion_suggestions (sender_key, source_channel)
    WHERE status = 'pending_review';
```

`ingestion_rules` gains two small additive columns (mirrors the existing
`created_by` convention, no breaking change):

- `created_by` gains a new conventional value `'promotion'` (column is already
  unconstrained TEXT, no migration needed for the value itself).
- `promoted_from_suggestion_id UUID REFERENCES switchboard.rule_promotion_suggestions(id)`,
  nullable — provenance link for "why does this rule exist," visible in the
  dashboard rule editor.

**"Clearly automated" classification** (`is_clearly_automated`): reuses the
*same* header signals already seeded as rules in migration `003` — presence of
`List-Unsubscribe`, `Precedence: bulk|list`, or `Auto-Submitted: auto-generated`
on the evidence events, or a `noreply`/`no-reply`/`notifications`/`alerts`
local-part prefix. This isn't a new heuristic; it's the existing bulk-mail
signal vocabulary applied to a new decision (which senders get the
streamlined confirm path — see §3), not a new detector to build and validate
from scratch.

### 3. Approval flow — reusing the dashboard-approvals surface

**Why a sibling capability, not a literal extension of `autonomy_suggestions`**:
`autonomy_suggestions.resulting_rule_id` is a hard FK to `approval_rules`
(tool-call approval rules), and its fingerprint is `(tool_name, safety-critical
args)` — a different shape than `(sender_key, source_channel)`. Forcing ingestion
rules through that table means either a polymorphic FK (two nullable rule-id
columns with a discriminator, or a `resulting_rule_table` string column) or
weakening the existing FK's integrity guarantee for an unrelated feature. A
sibling table (`rule_promotion_suggestions`, above) with the **same lifecycle
shape and the same dashboard surface** gets the reuse benefit (one mental model,
one banner component, one confirm/dismiss UX) without contorting an existing,
working schema. `dashboard-approvals` (`GET /api/approvals`, the suggestions
banner) already surfaces `autonomy_suggestions`; it gains a second,
structurally-similar source rather than an awkward join.

**Key decision — auto-apply for skip/metadata_only, as the bead literally
asks for, or owner-confirm for everything?**

The bead's own description says: *"auto-apply for skip/metadata_only of
clearly-automated senders; owner-approve for route_to."* I did not implement
that literally. RFC 0021 (owner disposition recorded 2026-07-02, three days
before this bead was groomed) states, in its own "Rejected alternatives"
section: *"Auto-minting rules after N approvals — rejected (owner,
2026-07-02); crosses RFC 0019's line and inverts security.md's per-event-review
default."* That disposition is written as a general project doctrine
statement about the promotion ratchet, not scoped narrowly to
`approval_rules`/tool-calls — and a `skip`/`metadata_only` ingestion rule is
still a standing rule that silently changes what the owner gets to see, minted
without any human ever looking at it, which is precisely the shape RFC 0021
just rejected.

**[decision]** All rule-promotion suggestions — `route_to` **and**
`skip`/`metadata_only` — land in the same approvals queue and require an
explicit owner action before `ingestion_rules` gets a new row. Nothing is
written unattended. What differs by risk tier is the *UX cost* of that
confirmation, not whether it happens:
- `route_to` suggestions render individually (higher blast radius — a wrong
  target butler misroutes real content).
- `skip`/`metadata_only` suggestions for `is_clearly_automated = true` senders
  render batched with a single "Confirm all N" affordance (RFC 0021's own
  "one-tap" vocabulary, applied to a batch instead of a single item) — so the
  owner does a handful of taps total, not per-sender review, while the actual
  DB row is still owner-authored.

Rationale (tie-break: adhere to project doctrine first): a documented, dated
owner ruling on the exact mechanism this bead re-proposes (auto-minting a
standing rule from accumulated evidence) outranks a bead description written
before that ruling's scope was fully worked out. The confirmation cost this
design pays for staying inside the ratchet is small — confirmations are
O(distinct senders), not O(events); even at zero automation the owner is
tapping ~15-30 times total to cover the current backlog, not 5,000+ times.
**This is flagged here, not silently decided, so the owner can override it
when reviewing this doc** — if the owner wants literal unattended auto-apply
for the `skip`/`metadata_only` tier specifically (a narrower, lower-blast-radius
carve-out than what RFC 0021 was rejecting), that's a one-line change to Bead 4
(skip the confirm step, auto-transition `pending_review → confirmed` for that
tier) and should be recorded as a fresh, explicitly-scoped disposition rather
than inferred from this bead's original wording. Reversible: yes, this is a
design doc.

**API/UI** (mirrors `autonomy-suggestions` REST shape):
- `GET /api/switchboard/rule-promotion-suggestions` (filterable by status)
- `POST /api/switchboard/rule-promotion-suggestions/{id}/confirm` — creates the
  `ingestion_rules` row (`created_by='promotion'`,
  `promoted_from_suggestion_id=<id>`), transitions suggestion to `confirmed`
- `POST /api/switchboard/rule-promotion-suggestions/bulk-confirm` — for the
  batched skip/metadata_only path
- `POST /api/switchboard/rule-promotion-suggestions/{id}/dismiss` — sets
  `cooldown_until`, transitions to `dismissed`
- Dashboard: new banner section next to the existing autonomy-suggestions
  banner (or a tab within it), `route_to` items listed individually with scope
  description ("Route mail from `alerts@chase.com` → `finance`, seen 5× over
  12 days"), `skip`/`metadata_only` items grouped with a "Confirm all 9
  automated senders" button.

### 4. Demotion via spot-check sampling

Promoted rules bypass the LLM entirely once confirmed — which means drift
(a vendor changes what a domain sends, a sender starts mixing automated and
human mail) is invisible by construction unless something still occasionally
checks. Per-rule spot-check:

- When `IngestionPolicyEvaluator.evaluate()` matches a rule with
  `created_by='promotion'`, roll a 1-in-K die (config, default K=20) **before**
  taking the bypass.
- On a hit: do **not** bypass. Let the event fall through to normal LLM
  classification as if no rule had matched (`pipeline.py`'s existing
  `pass_through` path, unchanged), but record the resulting verdict as
  `verdict_source='spot_check'` in `routing_verdict_log`, with
  `matched_rule_id` set to the rule that would have fired.
- A rolling agreement score per rule (last 20 spot-checks,
  `verdict_action`/`verdict_target` match vs. mismatch) below a threshold
  (default <90%) creates a **demotion** suggestion in
  `rule_promotion_suggestions` — **[decided, bu-l6vbd, bead 2/sw_020]**: an
  explicit `suggestion_kind` column (`'promotion' | 'demotion'`), not a
  `status` value. `status` tracks the suggestion's own review lifecycle
  (`pending_review`/`confirmed`/`dismissed`/`superseded`), identical in shape
  for both kinds; `suggestion_kind` is the discriminator beads 4/5 filter and
  render on, and a `chk_rule_promotion_suggestions_kind_shape` CHECK ties it
  to column population (promotion rows: sender/condition/action triple, no
  `target_rule_id`; demotion rows: `target_rule_id`, none of the
  proposed-rule columns) — mirrors the existing
  execution-failure-triggers-demotion pattern in `autonomy-suggestions`
  (`openspec/specs/autonomy-suggestions/spec.md:115-134`), applied to
  verdict drift instead of tool-call failure.
- Demotion is **also owner-confirmed** — never auto-disables the rule. This is
  consistent with §3's ratchet position: minting and un-minting a standing
  rule are symmetric trust operations.

`PolicyDecision` (`ingestion_policy.py`) gains one field to carry this:
`spot_check: bool = False`, set when a promoted rule matched but the die roll
sent it through the LLM instead. This is the only change needed inside the
evaluator itself; `pipeline.py`'s bypass-vs-fallthrough branch just needs to
check it alongside the existing `triage_decision` check.

## Expected impact

Grounded in the live numbers above, with explicit caveats:

- **Optimistic framing** (bead's own headline number): 134 of 256 distinct
  all-time email senders are automated. If all of those converge to
  domain-level rules, most of the 6,078 `pass_through` LLM sessions in the
  100-day window collapse to rule bypasses — this is the ceiling, not a
  committed estimate.
- **Directly measured, conservative lower bound** (this doc's live join,
  address-level, 100-day window): 16 of 217 attributable senders (7.4%) clear
  a same-target/`>=3`-occurrence bar; that covers 71 of 1,391 email-triggered
  sessions (5.1%) in the window. This measurement **cannot isolate
  rule-bypassed dispatches from real LLM verdicts** (the substrate gap Bead 1
  fixes), so it's a noisy floor, not a ceiling.
- **The real lever is domain-level mining, not address-level.** The
  optimistic 134/256 number is inherently about *vendors* (a company's mail
  infrastructure), and vendors commonly rotate the sending address
  (`billing@`, `no-reply@`, `alerts@`, `notifications@`) while keeping the
  same domain and the same routing verdict. Address-level promotion (`Bead
  3`'s default) will structurally undercount this. **Recommendation**: ship
  address-level mining first (simpler correctness story, matches the existing
  `sender_address` rule type 1:1), but treat domain-level rollup
  (`sender_domain`, already a supported rule type) as the very next iteration,
  not a someday-maybe — it's where most of the headline number actually lives.
- **Latency**: every promoted rule removes one spawned-session round-trip
  (typically several seconds) per matching event; for automated/bulk senders
  this is a pure latency win with no user-facing behavior change (the event
  still gets `skip`/`metadata_only`/`route_to`'d exactly as before, just
  faster and without an LLM call).
- **Confirmation cost is bounded and small**: even at the current ~15-rule
  scale growing by an order of magnitude, the owner is doing tens of taps, not
  thousands — the design in §3 keeps this true by construction (batched
  confirm for the high-volume, low-risk tier).

## Proposed implementation beads

Sequenced; sizes are rough (S = <1 day, M = 1-3 days, L = 3-5 days). The
coordinator files these against this design once reviewed.

1. **Verdict mining substrate** (M, feature) — `switchboard.routing_verdict_log`
   migration; write hooks at the three rule-bypass sites and the LLM
   `_extract_routed_butlers()` resolution site in `pipeline.py`; local
   sender-address normalization (don't block on bu-qeaou, see dependency
   note). No promotion logic yet — durable recording only, independently
   valuable for future analysis even if promotion itself stalls. Depends on:
   nothing blocking.

2. **Suggestion schema** (S, feature) — `switchboard.rule_promotion_suggestions`
   migration; `promoted_from_suggestion_id` nullable FK + `'promotion'`
   `created_by` convention on `ingestion_rules`. Depends on: Bead 1 (logical
   ordering; no hard data dependency).

3. **Promotion trigger job** (M, feature) — periodic scan grouped by
   `(sender_key, source_channel)`; N-consecutive-same-verdict detection with
   the 2-distinct-days evidence-quality gate; `is_clearly_automated`
   classifier reusing existing bulk-mail header signals; suggestion
   upsert/evidence-bump logic. Depends on: Beads 1, 2.

4. **Approvals-surface integration** (M, feature) — REST endpoints
   (list/confirm/bulk-confirm/dismiss), dashboard banner (individual
   `route_to` cards, batched skip/metadata_only confirm-all), audit events.
   Depends on: Bead 3. **Owner should confirm the auto-apply-vs-owner-confirm
   decision (§3) before this bead starts** — it's a one-line difference in
   scope depending on the answer.

5. **Demotion via spot-check** (M, feature) — 1-in-K shadow-LLM-check on
   promoted-rule matches (`PolicyDecision.spot_check` field), rolling
   agreement scoring, demotion-suggestion creation reusing the Bead 4 surface.
   Depends on: Beads 1, 4.

6. **Rule-promotion metrics** (S, task) — small stats endpoint/dashboard tile:
   sessions avoided, pending-suggestion count, estimated LLM-session savings —
   closes the loop on measuring the win this design promises. Depends on:
   Beads 1-4.

7. **De-duplicate sender normalization against bu-qeaou** (S, chore, low
   priority) — once bu-qeaou ships its normalized sender column on
   `public.ingestion_events`, switch `routing_verdict_log.sender_key`
   derivation to read it directly; delete the local regex duplication from
   Bead 1. Depends on: Bead 1 AND bu-qeaou merged.

## Open questions for the owner

1. Confirm or override the auto-apply decision in §3 (default: everything is
   owner-confirmed, skip/metadata_only gets a batched one-tap instead of
   individual review).
2. Default promotion threshold N=3 and demotion agreement floor 90%/last-20 —
   reasonable starting points, not load-bearing; tune after Bead 4 ships and
   real suggestion volume is visible.
3. Whether domain-level mining (flagged above as "the real lever") should be
   folded into Bead 3's first cut or deliberately deferred to a Bead 8 — this
   doc defaults to deferring it to keep Bead 3 scoped to the existing
   `sender_address` rule type, but the owner may prefer to pull it forward
   given it's where most of the headline savings live.
