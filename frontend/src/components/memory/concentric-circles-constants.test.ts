/**
 * Tests for truncateGraphemes -- grapheme-cluster-safe string truncation.
 *
 * Plain string.slice() operates on UTF-16 code units and splits surrogate
 * pairs (emoji, CJK supplementary characters) producing replacement characters
 * (U+FFFD). truncateGraphemes uses Intl.Segmenter to work at grapheme-cluster
 * granularity.
 */

import { describe, expect, it } from "vitest";
import { matchesSearch, truncateGraphemes, getInitials } from "@/components/memory/concentric-circles-constants";
import type { DunbarEntry } from "@/api/types";

describe("truncateGraphemes", () => {
  it("returns the string unchanged when it is within the limit", () => {
    expect(truncateGraphemes("Hello", 10)).toBe("Hello");
  });

  it("returns the string unchanged when it is exactly at the limit", () => {
    expect(truncateGraphemes("Hello", 5)).toBe("Hello");
  });

  it("truncates a plain ASCII string and appends ellipsis", () => {
    // maxGraphemes=8 → keeps 7 graphemes + "…"
    expect(truncateGraphemes("Hello, World!", 8)).toBe("Hello, …");
  });

  it("does not split a surrogate pair (emoji)", () => {
    // "Ana 🌸" is 6 UTF-16 code units but 5 grapheme clusters.
    // slice(0, 4) would give "Ana " safely; slice(0, 5) in UTF-16 gives "Ana 🌸"
    // but a naive byte-level approach might split the surrogate pair.
    const result = truncateGraphemes("Ana 🌸 extra", 6);
    // Should be "Ana 🌸…" (5 graphemes = "A","n","a"," ","🌸", then "…")
    expect(result).toBe("Ana 🌸…");
    // Must not contain the replacement character
    expect(result).not.toContain("�");
  });

  it("handles a string of only emoji correctly", () => {
    const result = truncateGraphemes("🌸🌺🌼🌻🌹", 3);
    expect(result).toBe("🌸🌺…");
    expect(result).not.toContain("�");
  });

  it("handles CJK characters without splitting", () => {
    // CJK characters are single code points (no surrogate pair needed for BMP),
    // but supplementary CJK characters (U+20000+) use surrogate pairs.
    const result = truncateGraphemes("你好世界朋友们啊", 5);
    expect(result).toBe("你好世界…");
  });

  it("returns the full string when length equals maxGraphemes", () => {
    expect(truncateGraphemes("🌸🌺🌼", 3)).toBe("🌸🌺🌼");
  });

  it("handles an empty string", () => {
    expect(truncateGraphemes("", 5)).toBe("");
  });

  it("handles maxGraphemes of 1 by returning just the ellipsis", () => {
    // maxGraphemes=1 means slice(0, 0) + "…" = "…"
    expect(truncateGraphemes("Hello", 1)).toBe("…");
  });

  it("handles maxGraphemes of 0 by returning an empty string", () => {
    expect(truncateGraphemes("Hello", 0)).toBe("");
  });

  it("mixed ASCII and emoji: no replacement characters at boundary", () => {
    const result = truncateGraphemes("Hi 🎉 there", 6);
    // "Hi 🎉 there" has 11 graphemes; maxGraphemes=6 → keep 5 + "…"
    // Graphemes 0-4: H, i, " ", 🎉, " " → "Hi 🎉 …"
    expect(result).toBe("Hi 🎉 …");
    expect(result).not.toContain("�");
  });
});

// ---------------------------------------------------------------------------
// matchesSearch
// ---------------------------------------------------------------------------

function makeEntry(overrides: Partial<DunbarEntry> = {}): DunbarEntry {
  return {
    contact_id: "c-1",
    entity_id: "e-1",
    canonical_name: "Alice Nguyen",
    dunbar_tier: 15,
    dunbar_score: 0.7,
    dunbar_tier_override: false,
    ...overrides,
  };
}

describe("matchesSearch", () => {
  it("returns true for empty query", () => {
    expect(matchesSearch(makeEntry(), "")).toBe(true);
  });

  it("matches by canonical_name (case-insensitive)", () => {
    const entry = makeEntry({ canonical_name: "Alice Nguyen" });
    expect(matchesSearch(entry, "alice")).toBe(true);
    expect(matchesSearch(entry, "NGUYEN")).toBe(true);
    expect(matchesSearch(entry, "ali")).toBe(true);
  });

  it("returns false when query matches neither canonical_name nor aliases", () => {
    const entry = makeEntry({ canonical_name: "Alice Nguyen", aliases: ["Ally"] });
    expect(matchesSearch(entry, "bob")).toBe(false);
  });

  it("matches by alias (alias-only match, canonical_name does not match)", () => {
    const entry = makeEntry({
      canonical_name: "Alice Nguyen",
      aliases: ["Ally", "Allie N"],
    });
    expect(matchesSearch(entry, "ally")).toBe(true);
    expect(matchesSearch(entry, "Allie")).toBe(true);
  });

  it("alias match is case-insensitive", () => {
    const entry = makeEntry({
      canonical_name: "Alice Nguyen",
      aliases: ["ALLY"],
    });
    expect(matchesSearch(entry, "ally")).toBe(true);
    expect(matchesSearch(entry, "ALLY")).toBe(true);
  });

  it("matches partial alias substring", () => {
    const entry = makeEntry({
      canonical_name: "Robert Smith",
      aliases: ["Bobby"],
    });
    expect(matchesSearch(entry, "obb")).toBe(true);
  });

  it("returns true when aliases is undefined", () => {
    const entry = makeEntry({ canonical_name: "Alice Nguyen", aliases: undefined });
    expect(matchesSearch(entry, "alice")).toBe(true);
    expect(matchesSearch(entry, "bob")).toBe(false);
  });

  it("returns true when aliases is an empty array", () => {
    const entry = makeEntry({ canonical_name: "Alice Nguyen", aliases: [] });
    expect(matchesSearch(entry, "alice")).toBe(true);
    expect(matchesSearch(entry, "bob")).toBe(false);
  });
});

describe("getInitials", () => {
  it("returns first and last initials for multi-word names", () => {
    expect(getInitials("John Doe")).toBe("JD");
  });

  it("returns uppercase initials", () => {
    expect(getInitials("alice bob")).toBe("AB");
  });

  it("single-word name with leading emoji: returns emoji + first letter", () => {
    // "🌸blossom" has graphemes: 🌸, b, l, o, s, s, o, m
    // Taking first 2 graphemes → "🌸b"
    const result = getInitials("🌸blossom");
    expect(result).toBe("🌸B");
    expect(result).not.toContain("�");
  });

  it("single-word name with family emoji (ZWJ sequence) as single grapheme", () => {
    // Family emoji like 👨‍👩‍👧 is a single grapheme cluster even though it contains ZWJ joiners.
    // Taking first 2 graphemes should give the full emoji (1st) + any following char (if exists)
    // For a 1-grapheme emoji with no following text, result should be just that emoji, uppercased.
    const result = getInitials("👨‍👩‍👧");
    expect(result).toBe("👨‍👩‍👧");
    expect(result).not.toContain("�");
  });

  it("CJK characters: returns first two characters", () => {
    // "日本人" → first 2 graphemes: 日, 本 → "日本"
    const result = getInitials("日本人");
    expect(result).toBe("日本");
    expect(result).not.toContain("�");
  });

  it("single character name", () => {
    const result = getInitials("A");
    expect(result).toBe("A");
  });

  it("handles leading/trailing whitespace", () => {
    expect(getInitials("  john doe  ")).toBe("JD");
  });

  it("handles multiple spaces between words", () => {
    expect(getInitials("john    doe")).toBe("JD");
  });

  it("handles leading/trailing whitespace in single-word names", () => {
    const result = getInitials("  alice  ");
    expect(result).toBe("AL");
  });
});
