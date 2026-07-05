// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";

import { addRecent, getRecents } from "@/lib/recents-store";

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("recents-store", () => {
  it("returns [] when nothing has been recorded", () => {
    expect(getRecents()).toEqual([]);
  });

  it("records an entry and reads it back", () => {
    addRecent({ id: "e1", kind: "entity", label: "Yustynn", entityType: "person" });
    const recents = getRecents();
    expect(recents).toHaveLength(1);
    expect(recents[0]).toMatchObject({ id: "e1", kind: "entity", label: "Yustynn", entityType: "person" });
    expect(typeof recents[0].timestamp).toBe("number");
  });

  it("orders most-recent-first", () => {
    addRecent({ id: "a", kind: "page", label: "A" });
    addRecent({ id: "b", kind: "page", label: "B" });
    addRecent({ id: "c", kind: "page", label: "C" });
    expect(getRecents().map((r) => r.id)).toEqual(["c", "b", "a"]);
  });

  it("bumps (dedupes) a re-recorded id to the front instead of duplicating it", () => {
    addRecent({ id: "a", kind: "page", label: "A" });
    addRecent({ id: "b", kind: "page", label: "B" });
    addRecent({ id: "a", kind: "page", label: "A (updated label)" });
    const recents = getRecents();
    expect(recents.map((r) => r.id)).toEqual(["a", "b"]);
    expect(recents[0].label).toBe("A (updated label)");
  });

  it("does not collide entities and pages that happen to share a raw id", () => {
    addRecent({ id: "42", kind: "entity", label: "Entity Forty-Two" });
    addRecent({ id: "42", kind: "page", label: "Page Forty-Two" });
    const recents = getRecents();
    expect(recents).toHaveLength(2);
    expect(recents.map((r) => r.kind).sort()).toEqual(["entity", "page"]);
  });

  it("caps the list at 8 entries, dropping the oldest", () => {
    for (let i = 0; i < 10; i++) {
      addRecent({ id: `id-${i}`, kind: "action", label: `Action ${i}` });
    }
    const recents = getRecents();
    expect(recents).toHaveLength(8);
    // Most recent 8 survive (id-9 down to id-2); the oldest two (id-0, id-1) are gone.
    expect(recents.map((r) => r.id)).not.toContain("id-0");
    expect(recents.map((r) => r.id)).not.toContain("id-1");
    expect(recents[0].id).toBe("id-9");
  });

  it("degrades to an empty list, not a throw, when localStorage is unavailable", () => {
    const getSpy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("storage disabled");
    });
    expect(() => getRecents()).not.toThrow();
    expect(getRecents()).toEqual([]);
    getSpy.mockRestore();

    const setSpy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota exceeded");
    });
    expect(() => addRecent({ id: "x", kind: "page", label: "X" })).not.toThrow();
    setSpy.mockRestore();
  });

  it("ignores malformed JSON in storage rather than throwing", () => {
    localStorage.setItem("dashboard.finder.recents.v1", "{not valid json");
    expect(getRecents()).toEqual([]);
  });

  it("filters out malformed entries from an otherwise-valid array", () => {
    localStorage.setItem(
      "dashboard.finder.recents.v1",
      JSON.stringify([
        { id: "good", kind: "page", label: "Good", timestamp: 1 },
        { id: "bad-kind", kind: "not-a-kind", label: "Bad", timestamp: 2 },
        { missing: "fields" },
        null,
      ]),
    );
    expect(getRecents().map((r) => r.id)).toEqual(["good"]);
  });
});
