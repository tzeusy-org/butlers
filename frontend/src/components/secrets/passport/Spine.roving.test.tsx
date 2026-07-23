// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";

import { Spine } from "./Spine.tsx";
import type { SpineEntry } from "./types.ts";

const entries: SpineEntry[] = [
  {
    key: "s:FIRST_SECRET",
    family: "system",
    label: "FIRST_SECRET",
    state: "ok",
    mono: true,
    subline: "shared",
    lastTouchOrder: 1,
  },
  {
    key: "s:SECOND_SECRET",
    family: "system",
    label: "SECOND_SECRET",
    state: "ok",
    mono: true,
    subline: "shared",
    lastTouchOrder: 2,
  },
];

function renderSpine() {
  return render(
    <Spine
      entries={entries}
      activeKey={entries[0].key}
      onSelect={vi.fn()}
      onSortChange={vi.fn()}
      search=""
      onSearchChange={vi.fn()}
      identities={[{ id: "owner", label: "Owner", role: "owner", hue: "blue" }]}
      activeIdentityId="owner"
      onIdentityChange={vi.fn()}
    />,
  );
}

afterEach(cleanup);

describe("Spine roving keyboard navigation", () => {
  it("uses one tab stop and moves focus between credential rows with Arrow keys", () => {
    const { container } = renderSpine();
    const rows = Array.from(
      container.querySelectorAll<HTMLButtonElement>('[data-spine-row="true"]'),
    );

    expect(rows).toHaveLength(2);
    expect(rows[0].tabIndex).toBe(0);
    expect(rows[1].tabIndex).toBe(-1);

    rows[0].focus();
    fireEvent.keyDown(rows[0], { key: "ArrowDown" });

    expect(document.activeElement).toBe(rows[1]);
    expect(rows[0].tabIndex).toBe(-1);
    expect(rows[1].tabIndex).toBe(0);
  });
});
