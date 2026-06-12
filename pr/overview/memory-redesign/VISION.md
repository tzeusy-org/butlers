# Vision — memory redesign

> Authored 2026-06-12 as the binding Section 0 for the `/memory` redesign.
> Synthesized from `openspec/specs/module-memory/`, `about/heart-and-soul/`
> doctrine, the live `/memory` implementation, and the Dispatch design
> language shared by the sibling bundles under `pr/overview/`.

## Mission

`/memory` is where the owner audits what the house believes. Every belief
carries its confidence, its age, and its provenance; the page makes the act
of remembering — raw observation becoming durable knowledge — legible.

> **Show the believing, not just the beliefs.**

Memory is the only subsystem whose defining mechanics are *temporal*:
consolidation runs, confidence decays, confirmations re-ink, rules are
proven or retired by outcome. A page that renders only the stored rows
hides exactly the part the owner needs to trust.

## Problem being solved

Today's `/memory` is functionally complete and structurally mute: seven
sections stacked vertically (tier cards, tabbed browser, activity timeline,
inspect search, retention policies, compaction log, re-embed panel), all in
card-grid idiom. Five specific pains:

1. **The lifecycle is invisible.** The page stores the nouns — Episodes,
   Facts, Rules tabs — but hides the verb: consolidation. Pending counts
   live inside a card stat; last-run time and dead-letter count surface
   nowhere. "Is remembering keeping up?" is unanswerable without SQL.
2. **Belief renders dishonestly flat.** Confidence is a static progress
   bar; decay — the system's defining mechanic — has no visual presence.
   A fact at 0.95 and one fading at 0.21 read at identical weight.
3. **One table shape, three times.** Episodes, facts, and rules are forced
   into near-identical paginated tables, against doctrine ("forcing them
   into one rectangular table view is a regression"). Provenance
   (`fact —derived_from→ episode`) exists in the data model via
   `memory_links` and appears nowhere in the UI.
4. **Two searches, no answer.** Per-tab search boxes and a separate
   full-width Inspect section duplicate the same affordance with different
   result shapes.
5. **The back office plays at full volume.** Retention policies, compaction
   log, and the re-embed panel render at the same hierarchy as the beliefs
   themselves. Housekeeping outranks knowledge.

## Primary audience

**Owner** — single principal, per `about/heart-and-soul/security.md`. The
owner reads this page as the sysadmin of their own life: to trust that
consolidation is keeping up, to audit why the system believes something,
and to correct the record when it is wrong. Agents read memory via MCP
tools, never via this page. External users, household members, and
multi-tenant projections are explicitly **not** in scope.

## Deliberate design moves

1. **The pipeline is the page's spine.** The header band answers "is
   remembering working" before anything else: a single hairline-bound strip
   of mono numerals reading left to right along the lifecycle —
   episodes → pending → facts (active · fading) → rules (proven) — with the
   last write-up time. Dead letters earn red only when the count is
   non-zero.
   - *Why:* observability-first doctrine. The one failure mode that
     silently corrupts everything downstream is a stalled or dead-lettering
     consolidation pipeline.
2. **Three registers, three shapes.** Episodes read as a **daybook**
   (time-gutter journal feed, grouped by day); facts read as a **ledger**
   (subject · predicate grid with a right-aligned belief column); rules
   read as **standing orders** (numbered directives with outcome tallies).
   The metaphor governs form, never nouns — the UI says Episodes, Facts,
   Rules.
   - *Why:* the three kinds are semantically different documents. Doctrine
     forbids the unified table; the house-ledger grammar gives each its
     correct rhythm while staying one family.
3. **Confidence is ink.** Facts display *effective* (decayed) confidence as
   a mono numeral with its decay rate; fading facts dim their foreground to
   `--dim` — the ink literally fades. No bars, no percentage donuts, no
   color. A confirmation re-inks the row to full foreground.
   - *Why:* type is the system. Decay expressed typographically is honest,
     calm, and reads at a glance across two hundred rows.
4. **Provenance is one click.** Every consolidated fact and rule links to
   its source episode (`derived_from`); every detail page cross-references
   its chain (episode ↔ fact ↔ rule) and links entity anchors out to
   `/entities/:id`.
   - *Why:* "understand what the system did with their data" is one of the
     four jobs every dashboard screen serves. Belief without provenance is
     not auditable.
5. **One search.** A single `/ search` band above the registers (backed by
   the existing inspect endpoint) with kind pills; the registers themselves
   are the results surface. The separate Inspect section is deleted.
   - *Why:* one affordance per signal. Two search boxes that answer the
     same question is duplicated chrome.
6. **The attention rail owns all state color.** Dead-letter episodes,
   stalled consolidation, anti-pattern rules, high-importance facts
   entering fading, and embedding drift queue in a right rail. Register
   rows stay neutral. Empty rail collapses to a single serif-italic line.
   - *Why:* per Dispatch §1b and the entity-redesign precedent — color
     leaking into index rows is overdesign; the rail is where state earns
     authority.
7. **Housekeeping is demoted, not hidden.** Retention policies (still
   editable), compaction log, and re-embed controls move to a quiet
   bottom surface under a single mono eyebrow. Reachable in one scroll,
   visually subordinate to the registers.
   - *Why:* every element earns its place against state. Maintenance
     machinery matters when it matters; it should not outshout knowledge.

## What we are deliberately NOT doing

- **No storage or schema migration.** Episodes, facts, rules, links,
  policies stay where they live. The redesign is presentational plus a
  small number of read-side endpoint deltas (pipeline stats, provenance
  expansion) and at most one mutation (fact confirm/retract — see below).
- **No memory authoring from the dashboard.** Writing memories is an agent
  act via MCP tools. The only candidate mutations are lifecycle
  attestations on a fact's detail page — *confirm* (re-ink) and *retract* —
  each a Dispatch commit pill, at most one per surface, and each requiring
  a new backend endpoint flagged in the backend epic. If the endpoint is
  descoped, the affordance ships disabled-hidden, not as a dead button.
- **No entity-graph visualization on `/memory`.** Entity anchors link out
  to `/entities`; the graph lives there. Cross-link, don't duplicate.
- **No charts for charts' sake.** No embedding scatterplots, no
  knowledge-graph hairballs, no "memory health score" gamification, no
  decay sparklines on list rows. The pipeline band and the belief column
  are the quantitative surface.
- **No renaming product nouns to metaphor nouns.** "Daybook", "ledger",
  "standing orders" are design grammar, not labels. The UI vocabulary
  stays Episodes / Facts / Rules / consolidation / permanence / validity.
- **No chat-with-your-memory interface.** Diagnosis happens through search
  and provenance, not a conversational overlay.
- **No timeline playback.** Retrospective day reconstruction is the
  Chronicler's domain (RFC 0014); this page is current operational state.
- **No status-as-a-word badges.** "Active", "Consolidated", "Established"
  as colored chips are banned; state renders as {dot, glyph, numeral,
  dimming} per the one-affordance rule.

## Success criteria

- The owner can tell whether consolidation is keeping up **from the header
  band alone, without scrolling**: pending count, last write-up time,
  dead-letter count.
- "Why do you believe this?" is answerable in **one click** from any fact
  row — through to the source episode, and onward to the rule it informs
  where one exists.
- A day on which the pipeline is healthy and no rule is anti-pattern
  renders `/memory` with **zero red/amber pixels**.
- A fading fact is *visibly* fading in the ledger (dimmed foreground);
  superseded and expired facts appear only behind an explicit validity
  filter, never by default.
- Exactly **one search affordance** exists on the page; kind pills scope
  it; results render in the same register shapes as browsing.
- The three detail pages (`/memory/facts/:id`, `/memory/rules/:id`,
  `/memory/episodes/:id`) share one editorial page-shape: heading + state,
  dense KV band, provenance chain, cross-references.
- Retention policy editing survives the redesign intact; housekeeping is
  reachable in one scroll but renders subordinate (small type, quiet
  surface, no cards).
- The page passes the Dispatch 10-point extension checklist — hierarchy
  from type and rule, tabular mono numerals, butler hues only on
  letter-marks, at most one commit button per surface, serif-italic empty
  states, calm at 3am.
