## Context

The Education butler teaches through Socratic conversation, diagnostic
calibration, and SM-2 spaced repetition. All content comes from model knowledge;
all pedagogy is hardcoded. This change grounds both content and pedagogy in
verifiable sources while maintaining the butler's core conversational character.

## Goals / Non-Goals

**Goals:**

- Enable source citations and reading pathways during teaching.
- Let the owner register source material the butler can reference.
- Select teaching techniques based on concept type and pedagogical research.
- Make pedagogy choices transparent and explainable.
- Store all source/pedagogy metadata in existing JSONB columns (no migration).

**Non-Goals:**

- Full-text indexing or parsing of source documents.
- Autonomous web retrieval of any kind.
- Changing spaced repetition intervals based on source characteristics.
- Cross-butler source sharing.

## Decisions

### Source material as lightweight registry, not document store

Source material registration captures metadata only: title, author(s), type
(book, paper, documentation, article), optional structured table of contents,
and optional URL. The butler does not ingest, parse, or store the source
content itself — it uses the metadata to anchor citations and reading
pathways, relying on model knowledge for the actual content. This keeps the
feature within the butler's conversational character and avoids the complexity
of a document store.

Storage: one JSON object per source in the education butler's `state` store
under `education/source/<source_id>`. No new table needed; the state store
already handles butler-scoped key-value data with the same lifecycle as other
education state (pruning, backup).

### Source refs as node metadata, not a separate table

Citations are stored as `metadata.source_refs` on `mind_map_nodes`:
```json
{
  "source_refs": [
    {
      "source_id": "<uuid>",
      "location": "chapter 3, pp. 45-52",
      "note": "formal proof of the pumping lemma"
    }
  ]
}
```

This avoids a migration and follows the same metadata-convention pattern used
by commitment-class `owner_conditions`. The existing `mind_map_node_update`
tool already writes arbitrary metadata fields.

### Concept type as node metadata, driving pedagogy selection

Each mind map node gains `metadata.concept_type`: one of `factual`,
`procedural`, `conceptual`, or `creative`. The teaching-session skill uses
this to select from a technique repertoire:

| Concept type | Primary technique | Rationale |
|---|---|---|
| `factual` | Retrieval practice (quiz-first) | Roediger & Butler (2011): testing effect |
| `procedural` | Worked examples → guided practice | Sweller (1988): cognitive load theory |
| `conceptual` | Socratic questioning → analogy | Chi (2009): active constructive interactive |
| `creative` | Divergent prompts → critique | Cropley (2001): creativity in education |

The default remains Socratic when `concept_type` is unset, preserving backward
compatibility. The curriculum-planning skill assigns concept types during
curriculum generation; the owner can override via conversation.

### Pedagogy transparency is conversational, not structural

When the owner asks "why are you teaching it this way?", the butler explains
its technique choice by citing the pedagogical principle and concept type. This
is a skill-level behavior (prompt instruction in the teaching-session skill),
not a structural feature — no new MCP tool or database field needed.

### Manifesto amendment is minimal and scoped

The content-sourcing exclusion is replaced with:
- Source material is owner-provided or model-recalled, never autonomously fetched
- The butler cites sources and suggests reading pathways, but does not replace
  its conversational teaching with passive content delivery
- Pedagogy choices are evidence-based and transparent

The amendment preserves the manifesto's emphasis on conversation-first teaching
and its boundaries against LMS integration, video, and classroom tools.

## Risks / Trade-offs

- **Citation accuracy.** The butler cites sources from model knowledge, which
  may be imprecise (wrong page numbers, misattributed claims). Mitigated by:
  owner-registered sources with structured metadata take precedence over
  model-recalled citations; unregistered citations are labeled as
  model-recalled.
- **Concept-type classification accuracy.** The curriculum planner infers
  concept types from node labels/descriptions; misclassification leads to
  suboptimal technique selection. Mitigated by: owner can override, and the
  default (Socratic) is a reasonable fallback for any concept type.
- **Scope creep toward document processing.** Registering source material
  could create pressure to parse PDFs or index content. Held firm by: the
  registry is metadata-only, and the manifesto amendment explicitly excludes
  document parsing.

## Verification

- Source material registration: tool creates, lists, and deletes entries in
  the state store.
- Source citations: teaching session output includes `source_refs` when
  relevant; citations reference registered sources by ID.
- Reading pathways: teaching session suggests specific source locations after
  concept explanation.
- Concept-type classification: curriculum planner assigns types; nodes default
  to unset.
- Pedagogy selection: teaching session varies technique by concept type.
- Pedagogy transparency: butler explains technique choice when asked.
- Backward compatibility: existing mind maps without source_refs or
  concept_type continue to work unchanged.
