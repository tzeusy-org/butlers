// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";

import { Spine } from "./Spine.tsx";
import type { SpineEntry } from "./types.ts";

const entries: SpineEntry[] = [
  {
    key: "s:READY_SECRET",
    family: "system",
    label: "Ready credential",
    state: "ok",
    mono: true,
    subline: "shared",
  },
  {
    key: "u:not-set",
    family: "user",
    label: "Not-set credential",
    state: "never_set",
    mono: false,
    subline: "not connected",
  },
  {
    key: "c:stale",
    family: "cli",
    label: "Stale credential",
    state: "warn",
    mono: false,
    subline: "unverified",
  },
  {
    key: "s:NEEDS_HAND_SECRET",
    family: "system",
    label: "Needs-hand credential",
    state: "expired",
    mono: true,
    subline: "expired",
  },
  {
    key: "c:in-progress",
    family: "cli",
    label: "In-progress credential",
    state: "rotating",
    mono: false,
    subline: "rotating",
  },
];

function renderSpine(search = "") {
  return render(
    <Spine
      entries={entries}
      activeKey=""
      onSelect={vi.fn()}
      onSortChange={vi.fn()}
      search={search}
      onSearchChange={vi.fn()}
      identities={[{ id: "owner", label: "Owner", role: "owner", hue: "blue" }]}
      activeIdentityId="owner"
      onIdentityChange={vi.fn()}
    />,
  );
}

afterEach(cleanup);

describe("Spine roving keyboard navigation", () => {
  it("follows filtered five-group order with one tab stop and omits empty groups", () => {
    const { container } = renderSpine();
    const rows = Array.from(
      container.querySelectorAll<HTMLButtonElement>('[data-spine-row="true"]'),
    );

    expect(rows.map((row) => row.dataset.state)).toEqual([
      "expired",
      "rotating",
      "warn",
      "ok",
      "never_set",
    ]);
    expect(
      Array.from(container.querySelectorAll<HTMLElement>("[data-spine-group]")).map(
        (group) => group.dataset.spineGroup,
      ),
    ).toEqual(["needs-hand", "in-progress", "stale", "ready", "not-set"]);
    expect(rows[0].tabIndex).toBe(0);
    expect(rows[1].tabIndex).toBe(-1);

    rows[0].focus();
    fireEvent.keyDown(rows[0], { key: "ArrowDown" });

    expect(document.activeElement).toBe(rows[1]);
    expect(rows[0].tabIndex).toBe(-1);
    expect(rows[1].tabIndex).toBe(0);

    for (const [search, groupId] of [
      ["needs-hand", "needs-hand"],
      ["in-progress", "in-progress"],
      ["stale", "stale"],
      ["ready", "ready"],
      ["not-set", "not-set"],
    ] as const) {
      cleanup();
      const filtered = renderSpine(search).container;
      expect(
        Array.from(filtered.querySelectorAll<HTMLElement>("[data-spine-group]")).map(
          (group) => group.dataset.spineGroup,
        ),
      ).toEqual([groupId]);
    }
  });
});
