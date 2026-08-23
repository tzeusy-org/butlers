// ---------------------------------------------------------------------------
// probeEvidenceCopy tests — bu-vpdkk
//
// Coverage:
//   - every PROBE_FAILURE_VOCABULARY member maps to owner-readable copy, and
//     never back to its own bare token (the bug this module fixes: since
//     bu-nz4sn the wire carries "rejected", not a sentence)
//   - the mapping is total: an unrecognised token (a vocabulary the backend
//     grew after this file was written) still renders words, never blank and
//     never "undefined"
//   - null / undefined / blank fall back to PROBE_FAILED_COPY
//   - the mapping is applied regardless of `ok` — it is a property of the
//     field, not of the outcome (see the module docstring)
//
// The vocabulary list below is a deliberate hand-copy of
// PROBE_FAILURE_VOCABULARY in src/butlers/api/routers/secrets_v2.py. It is
// duplicated rather than imported (different language, different process) so
// that a backend member added without copy here shows up as a failure in this
// file when the list is updated, instead of silently rendering a bare token.
// ---------------------------------------------------------------------------

import { describe, expect, it } from "vitest";

import { PROBE_EVIDENCE_COPY, PROBE_FAILED_COPY, probeEvidenceCopy } from "./probe-copy.ts";

const VOCABULARY = [
  "not_set",
  "expired",
  "rejected",
  "rate_limited",
  "provider_error",
  "malformed",
  "unverified",
  "other",
] as const;

describe("probeEvidenceCopy: vocabulary", () => {
  it("covers every PROBE_FAILURE_VOCABULARY member", () => {
    expect(Object.keys(PROBE_EVIDENCE_COPY).sort()).toEqual([...VOCABULARY].sort());
  });

  it.each(VOCABULARY)("renders %s as prose rather than the bare token", (member) => {
    const copy = probeEvidenceCopy(member);
    expect(copy).not.toBe(member);
    expect(copy).not.toContain("_");
    expect(copy.length).toBeGreaterThan(member.length);
  });

  it("names the provider as the refuser for rejected", () => {
    expect(probeEvidenceCopy("rejected")).toBe("the provider rejected this credential");
  });

  it("distinguishes a stale credential from a stale verification", () => {
    expect(probeEvidenceCopy("expired")).toBe("the stored value has expired");
    expect(probeEvidenceCopy("unverified")).toBe(
      "no live signal this time; the last live probe had failed",
    );
  });
});

describe("probeEvidenceCopy: totality", () => {
  it("humanises a token this file has no copy for", () => {
    // PROBE_FAILURE_VOCABULARY is a backend constant that can grow; an
    // unmapped member must still read as words.
    expect(probeEvidenceCopy("insufficient_scope")).toBe("insufficient scope");
  });

  it("passes through a single-word unknown token unchanged", () => {
    expect(probeEvidenceCopy("wedged")).toBe("wedged");
  });

  it("falls back for null, undefined and blank", () => {
    expect(probeEvidenceCopy(null)).toBe(PROBE_FAILED_COPY);
    expect(probeEvidenceCopy(undefined)).toBe(PROBE_FAILED_COPY);
    expect(probeEvidenceCopy("   ")).toBe(PROBE_FAILED_COPY);
  });

  it("returns a string for a token that names an Object.prototype member", () => {
    // A bare index would resolve these through the prototype chain to a
    // function that TypeScript still types as string, and ?? would not fire.
    for (const token of ["constructor", "toString", "valueOf", "hasOwnProperty"]) {
      expect(typeof probeEvidenceCopy(token)).toBe("string");
      expect(probeEvidenceCopy(token)).toBe(token);
    }
  });

  it("never returns an empty string", () => {
    for (const input of [...VOCABULARY, "brand_new_member", "", "  ", null, undefined]) {
      expect(probeEvidenceCopy(input).trim().length).toBeGreaterThan(0);
    }
  });
});
