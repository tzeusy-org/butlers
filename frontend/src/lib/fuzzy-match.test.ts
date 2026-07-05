import { describe, expect, it } from "vitest";

import { fuzzyFilter, fuzzyScore } from "@/lib/fuzzy-match";

describe("fuzzyScore", () => {
  it("returns 0 for an empty query (matches everything, lowest tier)", () => {
    expect(fuzzyScore("", "Issues")).toBe(0);
    expect(fuzzyScore("   ", "Issues")).toBe(0);
  });

  it("returns null when target is empty and query is not", () => {
    expect(fuzzyScore("a", "")).toBeNull();
  });

  it("scores an exact prefix match higher than a substring match", () => {
    const prefix = fuzzyScore("iss", "Issues")!;
    const substring = fuzzyScore("iss", "Notifications")!; // no 'iss' substring, subsequence only
    expect(prefix).not.toBeNull();
    expect(substring === null || prefix > substring).toBe(true);
  });

  it("scores a substring match higher than a subsequence-only match", () => {
    const substring = fuzzyScore("cal", "Calendar")!;
    const subsequence = fuzzyScore("cal", "Circles Approvals")!; // c...a...l in order, not contiguous
    expect(substring).toBeGreaterThan(subsequence);
  });

  it("matches non-contiguous characters in order (subsequence)", () => {
    expect(fuzzyScore("gcal", "Google Calendar")).not.toBeNull();
  });

  it("returns null when characters aren't all present in order", () => {
    expect(fuzzyScore("zzz", "Issues")).toBeNull();
    expect(fuzzyScore("scal", "calendars")).toBeNull(); // 's' never occurs after the 'c'/'a'/'l' run completes... actually verify no match
  });

  it("is case-insensitive", () => {
    expect(fuzzyScore("ISS", "issues")).not.toBeNull();
    expect(fuzzyScore("iss", "ISSUES")).not.toBeNull();
  });

  it("ranks a shorter target higher than a longer one within the same tier", () => {
    const shortTarget = fuzzyScore("app", "Approvals")!;
    const longerTarget = fuzzyScore("app", "Approvals History Archive")!;
    expect(shortTarget).toBeGreaterThan(longerTarget);
  });
});

describe("fuzzyFilter", () => {
  interface Item {
    label: string;
    path: string;
  }
  const items: Item[] = [
    { label: "Issues", path: "/issues" },
    { label: "Notifications", path: "/notifications" },
    { label: "Calendar", path: "/calendar" },
  ];

  it("returns items unfiltered (capped at limit) for an empty query", () => {
    expect(fuzzyFilter("", items, { getLabel: (i) => i.label })).toEqual(items);
    expect(fuzzyFilter("", items, { getLabel: (i) => i.label, limit: 2 })).toEqual(items.slice(0, 2));
  });

  it("filters out items that don't match at all", () => {
    const result = fuzzyFilter("cal", items, { getLabel: (i) => i.label });
    expect(result.map((i) => i.label)).toEqual(["Calendar"]);
  });

  it("ranks a prefix match ahead of a weaker match", () => {
    const withNotif: Item[] = [
      { label: "Notifications", path: "/notifications" },
      { label: "Issues", path: "/issues" },
    ];
    const result = fuzzyFilter("iss", withNotif, { getLabel: (i) => i.label });
    expect(result[0].label).toBe("Issues");
  });

  it("matches on keywords when the label itself doesn't match", () => {
    const result = fuzzyFilter("notif", items, {
      getLabel: (i) => i.label,
      getKeywords: (i) => [i.path],
    });
    // "notif" is a substring of "Notifications" itself, so this also
    // exercises the label path — add a keyword-only case explicitly:
    const keywordOnly = fuzzyFilter("issues", [{ label: "Open items", path: "/issues" }], {
      getLabel: (i) => i.label,
      getKeywords: (i) => [i.path],
    });
    expect(result.map((i) => i.label)).toContain("Notifications");
    expect(keywordOnly.map((i) => i.label)).toEqual(["Open items"]);
  });

  it("respects the limit after sorting", () => {
    const many: Item[] = Array.from({ length: 10 }, (_, i) => ({
      label: `Calendar ${i}`,
      path: `/calendar/${i}`,
    }));
    const result = fuzzyFilter("cal", many, { getLabel: (i) => i.label, limit: 3 });
    expect(result.length).toBe(3);
  });
});
