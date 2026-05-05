/**
 * Tests for truncateGraphemes -- grapheme-cluster-safe string truncation.
 *
 * Plain string.slice() operates on UTF-16 code units and splits surrogate
 * pairs (emoji, CJK supplementary characters) producing replacement characters
 * (U+FFFD). truncateGraphemes uses Intl.Segmenter to work at grapheme-cluster
 * granularity.
 */

import { describe, expect, it } from "vitest";
import { truncateGraphemes } from "@/components/memory/concentric-circles-constants";

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
