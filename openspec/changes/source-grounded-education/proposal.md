## Why

The Education Butler teaches from model knowledge with hardcoded pedagogical
strategies. Two gaps limit the quality of instruction:

1. **Content provenance.** The butler cannot cite sources, recommend reading
   pathways, or verify its explanations against authoritative material. The
   owner has no way to go deeper, cross-reference, or trust that what was
   taught is accurate beyond the model's confidence.

2. **Pedagogical grounding.** Teaching strategies (Socratic questioning,
   spaced repetition, diagnostic calibration) are baked in. The butler cannot
   explain why it chose a particular technique, adapt its approach based on
   what research says works for a specific concept type (visual-spatial vs.
   procedural vs. conceptual), or incorporate frameworks the owner finds
   effective.

The manifesto's v1 scope boundary ("Not a content sourcing agent — all teaching
content is generated at runtime from model knowledge") is lifted by this change,
with explicit constraints: source material is owner-provided or model-recalled,
never autonomously scraped from the web.

## What Changes

### Amended governing document

- `roster/education/MANIFESTO.md`: replaces the content-sourcing exclusion with
  a scoped source-grounding commitment (owner-provided material, no web
  scraping); strengthens the pedagogy commitment to explicitly name
  evidence-based technique selection and transparency.

### New capability: `education-source-grounding`

- **Source citations.** Teaching sessions can cite sources (books, papers,
  documentation) when explaining concepts. Citations are recorded as
  `source_refs` in the mind map node's `metadata` JSONB.
- **Reading pathways.** After teaching a concept, the butler can suggest
  specific source material for deeper study ("read chapter 3 of [book]").
- **Owner-provided source material.** The owner can register source material
  (title, author, type, optional table of contents) that the butler uses to
  anchor curriculum structure and cite during teaching.

### Modified capability: `education-teaching-flows`

- **Pedagogy-aware technique selection.** The teaching session selects a
  technique based on the concept type (factual recall, procedural skill,
  conceptual understanding, creative application) rather than defaulting to
  Socratic questioning for everything.
- **Pedagogy transparency.** The butler can explain why it chose a particular
  teaching approach when asked, citing the pedagogical principle.

## In Scope

- Manifesto amendment (content-sourcing boundary → scoped source grounding)
- Source citation annotations on mind map nodes (metadata JSONB, no migration)
- Owner-provided source material registration (MCP tool + state store)
- Reading pathway suggestions in teaching sessions
- Concept-type classification on mind map nodes (metadata JSONB)
- Pedagogy-aware technique selection in teaching-session skill
- Pedagogy transparency ("why this approach?") in teaching-session skill
- Dashboard: source annotations visible on mind map node detail

## Out of Scope

- Web scraping, autonomous URL fetching, or any external content retrieval
- PDF/document parsing or full-text indexing of source material
- Automated curriculum generation from a textbook's table of contents (owner
  guides the mapping conversationally)
- Modifications to spaced repetition intervals based on source difficulty
- Cross-butler source sharing (source material is education-scoped)
- Changes to diagnostic assessment or analytics modules

## Impact

- `roster/education/MANIFESTO.md` (amended: content-sourcing boundary)
- `roster/education/.claude/skills/teaching-session/` (extended: citations,
  pedagogy selection, pedagogy transparency)
- `roster/education/.claude/skills/curriculum-planning/` (extended: accept
  source material, concept-type classification)
- `roster/education/modules/tools.py` (extended: `source_material_register`,
  `source_material_list`; `mind_map_node_create/update` document `source_refs`
  and `concept_type` metadata conventions)
- `frontend/src/components/` education mind map (extended: source annotation
  display)
- Tests: source material registration, citation in teaching session output,
  pedagogy selection per concept type

## Design

See `design.md` in this changeset.
