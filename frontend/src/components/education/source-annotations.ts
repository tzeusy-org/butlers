/**
 * Reading pedagogy annotations off a mind map node's metadata (bu-istke.5).
 *
 * Two annotations are written by the curriculum planner and the teaching
 * session into `mind_map_nodes.metadata`:
 *
 * - `concept_type` — one of four values (see `roster/education/tools/
 *   concept_types.py`), telling the teaching phase which technique fits.
 * - `source_refs` — an array of `{source_id, location, provenance?, note?}`
 *   entries naming where a claim came from.
 *
 * The whole point of the source annotation is the provenance distinction, so
 * this module is deliberately unforgiving about it. A citation is only ever
 * presented as `referenced` when (a) the stored ref says so **and** (b) the
 * registry still resolves its `source_id`. Everything else resolves to a
 * weaker state that names its own weakness. There is no path through
 * {@link resolveSourceRef} that upgrades an unknown into a citation.
 *
 * Metadata is an open JSON bag written by an LLM-driven planner, so every
 * field is validated here rather than trusted.
 */

import type { EducationSourceMaterial } from "@/api/index.ts";

/**
 * The concept-type vocabulary, mirroring `CONCEPT_TYPES` in
 * `roster/education/tools/concept_types.py`. The classifier abstains rather
 * than guessing, so an unset type is normal and must render as absence, not
 * as a default.
 */
export const CONCEPT_TYPES = [
  "factual",
  "procedural",
  "conceptual",
  "creative",
] as const;

export type ConceptType = (typeof CONCEPT_TYPES)[number];

/** Where a stored ref claims its location came from. */
export type Provenance = "referenced" | "model-recalled";

/** A `metadata.source_refs` entry, after validation. */
export interface StoredSourceRef {
  /** Registered source this ref names, or null for a pure model recollection. */
  sourceId: string | null;
  /** Free-text location within the source (chapter, page range, section). */
  location: string | null;
  /** Provenance as stored, or derived when the writer did not record one. */
  provenance: Provenance;
  /** Optional free-text note recorded alongside the ref. */
  note: string | null;
}

/**
 * How confidently the panel may present one ref, once the registry has had
 * its say.
 *
 * - `referenced` — the ref claims to come from the source itself and the
 *   registry still resolves it. The only state that renders as a citation.
 * - `model-recalled` — the location came from the model's own knowledge.
 *   Shown whether or not a registered source backs the title, because a
 *   registered title does not make a recalled location verified.
 * - `unregistered` — the named source is not in the registry. No title, no
 *   citation affordance; the ref is shown only so the reader knows it exists.
 * - `unresolved` — the registry could not be consulted. We decline to
 *   classify rather than guess in either direction.
 */
export type SourceRefState =
  | "referenced"
  | "model-recalled"
  | "unregistered"
  | "unresolved";

/** Whether the registry lookup can be trusted to answer at all. */
export type RegistryStatus = "resolved" | "loading" | "unavailable";

export interface ResolvedSourceRef {
  ref: StoredSourceRef;
  state: SourceRefState;
  /** Registry record, present only when the lookup actually hit. */
  source: EducationSourceMaterial | null;
}

function nonEmptyString(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value.trim() : null;
}

/**
 * Read `metadata.concept_type`, returning null unless it is one of the four
 * known values. An unrecognized string is treated as absent: the vocabulary
 * is closed, and rendering an unknown word as a pedagogy tag would imply the
 * teaching phase understands it.
 */
export function parseConceptType(
  metadata: Record<string, unknown> | null | undefined,
): ConceptType | null {
  const raw = metadata?.concept_type;
  return CONCEPT_TYPES.includes(raw as ConceptType) ? (raw as ConceptType) : null;
}

/**
 * Read `metadata.source_refs` into validated entries, dropping anything with
 * neither a `source_id` nor a `location` (an entry that names nothing cannot
 * be shown honestly).
 *
 * Provenance is taken from the ref when it records one — the planner writes
 * it explicitly — and otherwise derived the way
 * REQ-education-source-grounding-002 specifies: a ref naming a source is
 * `referenced`, a ref with a null `source_id` is `model-recalled`. An
 * unrecognized provenance value falls back to `model-recalled`, the weaker of
 * the two: a malformed annotation must never be promoted into a citation.
 */
export function parseSourceRefs(
  metadata: Record<string, unknown> | null | undefined,
): StoredSourceRef[] {
  const raw = metadata?.source_refs;
  if (!Array.isArray(raw)) return [];

  const refs: StoredSourceRef[] = [];
  for (const entry of raw) {
    if (typeof entry !== "object" || entry === null) continue;
    const record = entry as Record<string, unknown>;

    const sourceId = nonEmptyString(record.source_id);
    const location = nonEmptyString(record.location);
    if (sourceId === null && location === null) continue;

    const storedProvenance = nonEmptyString(record.provenance);
    let provenance: Provenance;
    if (storedProvenance === "referenced" || storedProvenance === "model-recalled") {
      provenance = storedProvenance;
    } else if (storedProvenance === null && sourceId !== null) {
      provenance = "referenced";
    } else {
      provenance = "model-recalled";
    }

    refs.push({
      sourceId,
      location,
      provenance,
      note: nonEmptyString(record.note),
    });
  }
  return refs;
}

/**
 * Resolve one ref against the registry.
 *
 * `registry` is only consulted when `status` is `"resolved"`; a loading or
 * unavailable registry yields `unresolved` for every ref that names a source,
 * because "not found in a list we never received" is not a finding.
 */
export function resolveSourceRef(
  ref: StoredSourceRef,
  registry: ReadonlyMap<string, EducationSourceMaterial>,
  status: RegistryStatus,
): ResolvedSourceRef {
  if (ref.sourceId === null) {
    // Nothing to look up: the ref never claimed a registered source.
    return { ref, state: "model-recalled", source: null };
  }
  if (status !== "resolved") {
    return { ref, state: "unresolved", source: null };
  }

  const source = registry.get(ref.sourceId);
  if (source === undefined) {
    // Dangling: the source was removed after the ref was written. Its stored
    // provenance is now unbackable, so it is discarded rather than displayed.
    return { ref, state: "unregistered", source: null };
  }
  return { ref, state: ref.provenance, source };
}

/** Index a registry list by `source_id` for lookup. */
export function indexSources(
  sources: readonly EducationSourceMaterial[] | undefined,
): Map<string, EducationSourceMaterial> {
  return new Map((sources ?? []).map((s) => [s.source_id, s]));
}

/** Human-facing label for a concept type. */
export function conceptTypeLabel(conceptType: ConceptType): string {
  return conceptType.charAt(0).toUpperCase() + conceptType.slice(1);
}
