/**
 * Unit tests for memory-housekeeping pure helpers (bu-2ix8d.8).
 */

import { describe, expect, it } from "vitest";

import {
  VALID_RETENTION_KINDS,
  dryRunResultLine,
  embeddingDriftSentence,
  formatBytes,
  formatCompactionCounts,
  formatUpdatedStamp,
  isValidRetentionKind,
  reembedDoneLine,
} from "@/lib/memory-housekeeping";

describe("retention kind constraint", () => {
  it("matches the backend's valid set exactly", () => {
    // Mirrors _VALID_KINDS in src/butlers/api/routers/memory.py.
    expect([...VALID_RETENTION_KINDS].sort()).toEqual(
      ["embedding", "event", "fact", "preference", "summary", "transcript"].sort(),
    );
  });

  it("accepts valid kinds and rejects free-text", () => {
    expect(isValidRetentionKind("event")).toBe(true);
    expect(isValidRetentionKind("embedding")).toBe(true);
    expect(isValidRetentionKind("bogus")).toBe(false);
    expect(isValidRetentionKind("")).toBe(false);
  });
});

describe("formatUpdatedStamp", () => {
  it("renders ISO date · actor", () => {
    expect(formatUpdatedStamp("2026-05-02T10:00:00Z", "api")).toMatch(
      /^2026-05-0[12] · api$/,
    );
  });

  it("falls back to system when actor is null", () => {
    expect(formatUpdatedStamp("2026-04-18T00:00:00Z", null)).toContain("· system");
  });
});

describe("formatBytes", () => {
  it("returns null for null bytes (caller omits the fragment)", () => {
    expect(formatBytes(null)).toBeNull();
  });

  it("formats B / KB / MB", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(3_250_586)).toBe("3.1 MB");
  });
});

describe("formatCompactionCounts", () => {
  it("includes bytes when present", () => {
    expect(formatCompactionCounts(1204, 3_250_586)).toBe("1,204 rows · 3.1 MB");
  });

  it("OMITS the bytes clause entirely when bytes are null (no em-dash filler)", () => {
    const line = formatCompactionCounts(89, null);
    expect(line).toBe("89 rows");
    expect(line).not.toContain("·");
    expect(line).not.toContain("—");
  });
});

describe("embeddingDriftSentence", () => {
  it("returns null when zero (caller shows the serif-italic line)", () => {
    expect(embeddingDriftSentence(0)).toBeNull();
  });

  it("states drift summed across tiers when non-zero", () => {
    expect(embeddingDriftSentence(412)).toContain("412 rows");
    expect(embeddingDriftSentence(412)).toContain("older embedding model");
  });
});

describe("dryRunResultLine", () => {
  it("composes the inline mono line", () => {
    expect(dryRunResultLine(412, 2)).toBe("would re-embed 412 rows across 2 tiers");
  });
});

describe("reembedDoneLine", () => {
  it("composes the completion line with rounded seconds", () => {
    expect(reembedDoneLine(412, 38.4)).toBe("re-embedded 412 rows · 38s");
  });
});
