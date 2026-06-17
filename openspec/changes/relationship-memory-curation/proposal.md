# relationship-memory-curation

## Why

`relational-edges-single-home` fixes *where* edges land once they are extracted
as edges. It does not address two adjacent failures that leave the relationship
graph thin and untrustworthy over time:

1. **Durable relationships are captured only as prose, never as edges.** The
   `fact-extraction` skill, when it reads "Cohabiting partner with Chloe Wong",
   writes a free-text property fact (`living_arrangement`) with no
   `object_entity_id` — so no edge exists to route, and no fix downstream can
   surface it. Verified on the live dev database (2026-06-17): the owner had
   **zero** kinship/partner edges in either store, while the owner's *actual*
   partner (Chloe Wong, an existing entity), mother, and a former employer were
   all recorded as prose. The graph reflected only what happened to be
   structured, which was almost nothing for the owner.

2. **Extraction has no confidence gate and the graph has no maintenance loop.**
   The same investigation surfaced a fabricated fact (`children` = "has a son";
   the owner has no son) sitting active, four duplicate `Chloe` entities and two
   `Papa` entities from unresolved mentions, and owner-carve-out
   `pending_actions` (RFC 0017) that expire silently after 72h. Nothing in the
   system notices or repairs any of this; today it took a human with raw SQL.

This is the doctrine-correct shape: per Non-Negotiable Rule 4, the daemon is
deterministic and judgment lives in ephemeral LLM sessions, so curation is a
scheduled LLM *session*, not daemon logic. Per the vision ("act autonomously on
schedules… progressively more autonomy… the system is boring, it works") an
unattended hygiene loop is exactly what the relationship butler should do. Per
the staffer-governance principle (architecture.md), curation is a *domain*
capability and belongs **on the relationship butler**, not in a new standalone
staffer.

## What Changes

- **NEW (`relationship-curation`).** A weekly `dispatch_mode="prompt"` scheduled
  task on the relationship butler, driven by a new `memory-curation` skill, that
  runs four jobs in one pass: (a) propose structured edges from relational prose
  facts, (b) propose duplicate-entity merges, (c) flag contradicted /
  low-confidence facts for retraction, (d) surface owner `pending_actions`
  nearing 72h expiry. The pass MAY auto-apply **high-confidence, non-owner,
  reversible** merges/retractions; everything touching the owner entity or below
  the confidence bar is PROPOSED via `relationship_assert_fact()` /
  `pending_actions`. A single `notify()` digest reports both proposed and
  auto-applied changes. It never silently rewrites the owner's graph.

- **ADDED (`relationship-facts`) — extraction emits edges from prose.** The
  `fact-extraction` skill SHALL, when prose asserts a *standing* relationship
  (partner/spouse/parent/child/sibling/friend/colleague/employer), resolve-or-
  create the object entity and emit the registry-relational edge via
  `relationship_assert_fact(object_kind='entity')`, in addition to any narrative
  fact — not store the relationship as prose alone.

- **ADDED (`relationship-facts`) — inferred-relationship confidence gate.**
  Relationship facts inferred (rather than user-stated) MUST carry confidence and
  provenance, and inferred *family* relationships below the confidence bar MUST be
  proposed for confirmation rather than written active.

- **ADDED (`relationship-facts`) — backfill retraction guard (bu-2ezvz).** The
  one-time backfill (and any re-home path) MUST NOT retract the source memory
  edge-fact when `relationship_assert_fact()` returns `pending_approval` (owner
  carve-out parked the write); the source is retracted only on a committed active
  write.

## Impact

- **Depends on** `relational-edges-single-home` (assumes `entity_facts` is the
  single home, the registry is seeded, and the alias map exists).
- New skill `roster/relationship/.agents/skills/memory-curation/SKILL.md`;
  edits to `roster/relationship/.agents/skills/fact-extraction/SKILL.md`.
- New `[[butler.schedule]]` entry in `roster/relationship/butler.toml`
  (weekly, `dispatch_mode="prompt"`, explicit `notify()` in the prompt).
- One spec defect corrected in the active change's backfill scenario; the
  backfill script gains a parked-write branch.
- No API contract change. No new MCP tool required; an optional read tool
  (list relational-prose facts) is out of scope for this change.

## Source References
- Non-Negotiable Rule 4 (daemon deterministic; judgment in LLM sessions)
- RFC 0017 (owner routing safety — carve-out / `pending_actions`)
- `relational-edges-single-home` (single home for registry-relational edges)
- vision.md (autonomy-on-schedules; "the system is boring, it works")
- architecture.md (staffer governance — domain capability on the domain butler)
