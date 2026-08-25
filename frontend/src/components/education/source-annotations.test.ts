import { describe, expect, it } from "vitest";

import type { EducationSourceMaterial } from "@/api/index.ts";
import {
  indexSources,
  parseConceptType,
  parseSourceRefs,
  resolveSourceRef,
  type StoredSourceRef,
} from "./source-annotations";

const SOURCE: EducationSourceMaterial = {
  source_id: "src-1",
  title: "Structure and Interpretation of Computer Programs",
  authors: ["Harold Abelson"],
  type: "book",
  url: "https://example.test/sicp",
  registered_at: "2026-08-21T00:00:00+00:00",
};

const REGISTRY = indexSources([SOURCE]);
const EMPTY_REGISTRY = indexSources([]);

function ref(overrides: Partial<StoredSourceRef> = {}): StoredSourceRef {
  return {
    sourceId: "src-1",
    location: "chapter 1.2",
    provenance: "referenced",
    note: null,
    ...overrides,
  };
}

describe("parseConceptType", () => {
  it("accepts each value of the classifier's vocabulary", () => {
    for (const type of ["factual", "procedural", "conceptual", "creative"]) {
      expect(parseConceptType({ concept_type: type })).toBe(type);
    }
  });

  it("treats an unknown or missing value as absent", () => {
    expect(parseConceptType({ concept_type: "kinaesthetic" })).toBeNull();
    expect(parseConceptType({ concept_type: 3 })).toBeNull();
    expect(parseConceptType({})).toBeNull();
    expect(parseConceptType(null)).toBeNull();
    expect(parseConceptType(undefined)).toBeNull();
  });
});

describe("parseSourceRefs", () => {
  it("returns nothing when source_refs is absent or not an array", () => {
    expect(parseSourceRefs({})).toEqual([]);
    expect(parseSourceRefs({ source_refs: "chapter 1" })).toEqual([]);
    expect(parseSourceRefs(undefined)).toEqual([]);
  });

  it("keeps the stored provenance when the writer recorded one", () => {
    const parsed = parseSourceRefs({
      source_refs: [
        { source_id: "src-1", location: "chapter 1.2", provenance: "model-recalled" },
      ],
    });
    expect(parsed).toEqual([
      {
        sourceId: "src-1",
        location: "chapter 1.2",
        provenance: "model-recalled",
        note: null,
      },
    ]);
  });

  it("derives referenced from a named source when no provenance is stored", () => {
    const parsed = parseSourceRefs({
      source_refs: [{ source_id: "src-1", location: "chapter 1.2" }],
    });
    expect(parsed[0].provenance).toBe("referenced");
  });

  it("derives model-recalled from a null source_id", () => {
    const parsed = parseSourceRefs({
      source_refs: [{ source_id: null, location: "the standard proof" }],
    });
    expect(parsed[0].sourceId).toBeNull();
    expect(parsed[0].provenance).toBe("model-recalled");
  });

  it("falls back to the weaker provenance when the stored value is unrecognized", () => {
    const parsed = parseSourceRefs({
      source_refs: [{ source_id: "src-1", location: "chapter 1.2", provenance: "verified" }],
    });
    expect(parsed[0].provenance).toBe("model-recalled");
  });

  it("keeps a ref with no location and marks the location as unrecorded", () => {
    const parsed = parseSourceRefs({ source_refs: [{ source_id: "src-1" }] });
    expect(parsed[0].location).toBeNull();
  });

  it("drops entries that name neither a source nor a location", () => {
    expect(parseSourceRefs({ source_refs: [{}, null, 7, { note: "hi" }] })).toEqual([]);
  });

  it("carries an optional note through", () => {
    const parsed = parseSourceRefs({
      source_refs: [{ source_id: "src-1", location: "ch 1", note: "worked example" }],
    });
    expect(parsed[0].note).toBe("worked example");
  });
});

describe("resolveSourceRef", () => {
  it("resolves a registered, source-read ref to referenced with its record", () => {
    const resolved = resolveSourceRef(ref(), REGISTRY, "resolved");
    expect(resolved.state).toBe("referenced");
    expect(resolved.source).toBe(SOURCE);
  });

  it("keeps a model-recalled ref model-recalled even when its source is registered", () => {
    const resolved = resolveSourceRef(
      ref({ provenance: "model-recalled" }),
      REGISTRY,
      "resolved",
    );
    expect(resolved.state).toBe("model-recalled");
    expect(resolved.source).toBe(SOURCE);
  });

  it("does not consult the registry for a ref with no source_id", () => {
    const resolved = resolveSourceRef(
      ref({ sourceId: null, provenance: "model-recalled" }),
      EMPTY_REGISTRY,
      "resolved",
    );
    expect(resolved.state).toBe("model-recalled");
    expect(resolved.source).toBeNull();
  });

  it("downgrades a dangling ref to unregistered and drops its stored provenance", () => {
    const resolved = resolveSourceRef(ref(), EMPTY_REGISTRY, "resolved");
    expect(resolved.state).toBe("unregistered");
    expect(resolved.source).toBeNull();
  });

  it("declines to classify when the registry has not answered", () => {
    for (const status of ["loading", "unavailable"] as const) {
      const resolved = resolveSourceRef(ref(), EMPTY_REGISTRY, status);
      expect(resolved.state).toBe("unresolved");
      expect(resolved.source).toBeNull();
    }
  });

  it("never upgrades to referenced without both a claim and a registry hit", () => {
    const cases = [
      resolveSourceRef(ref({ provenance: "model-recalled" }), REGISTRY, "resolved"),
      resolveSourceRef(ref(), EMPTY_REGISTRY, "resolved"),
      resolveSourceRef(ref(), EMPTY_REGISTRY, "loading"),
      resolveSourceRef(ref(), EMPTY_REGISTRY, "unavailable"),
      resolveSourceRef(ref({ sourceId: null }), REGISTRY, "resolved"),
    ];
    expect(cases.map((c) => c.state)).not.toContain("referenced");
  });
});
