# Design — relationship-memory-curation

## Context

The relationship graph degrades through three mechanisms that
`relational-edges-single-home` does not cover: relationships entered as prose
never become edges; extraction occasionally fabricates facts; and accumulated
debt (duplicate entities, contradicted facts, expiring approvals) has no repair
loop. This change adds the repair loop as a scheduled curation session and
tightens extraction so it produces edges and gates inferences.

The hard constraint, learned the expensive way in the 2026-06-17 investigation:
**the curator must propose, not silently rewrite.** Extraction already invented a
son; a "helpful" unsupervised cleaner with the same confidence would corrupt the
graph faster than a human could notice.

## The autonomy boundary (owner decision, 2026-06-17)

Three-way classification of every proposed mutation:

| Class | Condition | Action |
|---|---|---|
| **Auto-apply** | non-owner **and** high-confidence **and** reversible+logged | write directly (merges/retractions) or via `assert_fact` (new edges, already auto today) |
| **Propose** | touches the owner entity **or** below the confidence bar | `relationship_assert_fact()` → `pending_actions` (carve-out), or a `pending_actions` row for merges/retractions |
| **Report-only** | already auto-applied this run, or expiring approvals | listed in the digest, no further action |

Auto-applied changes are *not* invisible: every auto-applied merge/retraction is
logged with provenance and **listed in the same digest** as the proposals, so a
wrong high-confidence action is reviewable and reversible after the fact.

### High-confidence criteria (auto-apply gate)

Confidence is a session judgment, but the bar is specified, not vibes:

- **Entity merge** — auto-apply only when names match (normalized) **AND** ≥1
  corroborating signal: shared `contact_info` value, an overlapping alias, or a
  shared edge to the same third entity. Name-only matches are **proposed**, never
  auto-merged. (The four bare `Chloe` entities vs. `Chloe Wong` are name-only →
  proposed.)
- **Fact retraction** — auto-apply only on a *direct contradiction*: a newer fact
  of equal-or-higher confidence on the same `(entity, predicate, scope)`.
  Staleness/decay alone is **proposed**, never auto-retracted. A fact the owner
  flags or that has no corroboration (e.g. the fabricated "has a son") is
  **proposed for retraction with its evidence**, not deleted unilaterally.
- **Edge from prose** — non-owner durable edges flow through the existing
  `assert_fact` auto-write path (unchanged). Owner edges hit the carve-out and
  are proposed.

### Reversibility requirement

Auto-applied merges and retractions MUST be reversible: merges record the source
entity id and re-pointed rows; retractions set `validity='retracted'` (never hard
delete) with `metadata.curation_reason`. This keeps a wrong auto-action a
one-step undo, which is what makes high-confidence auto-apply acceptable.

## The curation pass (one weekly session)

`dispatch_mode="prompt"`, `cron = "0 9 * * 1"` (Mon 9am, alongside
`relationship-maintenance`). The dispatch prompt invokes the `memory-curation`
skill and **must** end with an explicit `notify()` instruction — the session is
headless, so per the shared Scheduled Task Output Contract any output not sent via
`notify()` is lost. The four jobs, in order:

1. **Prose → edges.** Scan active relationship-bearing prose facts (relational
   language, `object_entity_id IS NULL`). For each, resolve-or-create the object
   entity and emit the registry-relational edge. Owner → proposed; non-owner
   high-confidence → auto.
2. **Entity dedup.** Find duplicate entities (normalized-name clusters); merge or
   propose per the gate above.
3. **Contradiction sweep.** Find facts contradicted by a newer fact, or flagged /
   uncorroborated fabrications; auto-retract direct contradictions, propose the
   rest with evidence.
4. **Approval surfacing.** List owner `pending_actions` within ~24h of their 72h
   expiry so they are decided, not silently dropped.

Then one `notify()` digest: *N edges proposed, M merges auto-applied, K facts
flagged, P approvals expiring* — with the proposals linked to the approval
surface.

## Why an LLM session, not a daemon job

Three of the four jobs require judgment (is this prose a standing relationship?
are these the same person? is this fact contradicted or just old?). Per
Non-Negotiable Rule 4 that judgment must live in an ephemeral session, not the
deterministic daemon. The deterministic parts (the candidate queries) are cheap
SQL the session runs through existing read tools; only the classification is LLM
work. Contrast with `memory_consolidation` (a `dispatch_mode="job"`): that is
mechanical and stays a job; curation is reasoning and is a prompt task.

## Alternatives rejected

- **Standalone curation butler/staffer.** Rejected: curation is relationship-
  domain judgment over the relationship schema; a staffer is for cross-cutting
  infrastructure (Switchboard/Messenger/QA). A new staffer duplicates lifecycle
  for no boundary benefit and violates the "domain capability on the domain
  butler" principle.
- **Propose everything (zero auto-apply).** Considered; owner chose high-
  confidence auto-apply for non-owner reversible actions to keep the weekly queue
  small. Mitigated by the reversibility + report-in-digest requirements.
- **Daemon job.** Rejected per Rule 4 (judgment in the daemon is a defect).
- **Fix only extraction, skip the periodic pass.** Rejected: extraction fixes
  *new* facts; the existing prose debt and duplicate entities need a backfill/
  repair loop regardless.

## Risks & mitigations

- **Wrong high-confidence merge.** Mitigation: ≥1 corroborating signal required
  (never name-only); reversible; reported in digest.
- **Over-eager retraction.** Mitigation: direct-contradiction-only for auto;
  soft-retract (never delete); evidence in the proposal.
- **Digest fatigue / cost.** Mitigation: weekly cadence; one consolidated
  `notify()`; auto-apply path keeps the proposal queue short.
- **Prose mis-read as a standing relationship** (episodic vs durable). Mitigation:
  the skill's discriminator (durable kinship/employment/partner vs episodic
  coordination) — episodic stays narrative, as already established for
  `planned_dinner_with` et al.
