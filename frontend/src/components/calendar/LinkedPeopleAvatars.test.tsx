// @vitest-environment jsdom
/**
 * LinkedPeopleAvatars / LinkedPeopleChips — RTL tests (bu-qs64f).
 *
 * These render the people the workspace read hydrates onto an *existing* event
 * (`UnifiedCalendarEntry.linked_people`), so linked-people avatars persist for
 * existing events, not only at creation time in the dialog. Covers:
 *  - Avatars render one mark per person (initials), collapsing the overflow
 *    into a "+N" chip and exposing the full name list through the container's
 *    accessible name.
 *  - Empty input renders nothing (never an empty box).
 *  - The detail-panel chips render each person's resolved display label.
 */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

import {
  LinkedPeopleAvatars,
  LinkedPeopleChips,
} from "./LinkedPeopleAvatars";
import type { CalendarLinkedPerson } from "@/api/types.ts";

afterEach(cleanup);

const PEOPLE: CalendarLinkedPerson[] = [
  { entity_id: "e1", display_label: "Ada Lovelace" },
  { entity_id: "e2", display_label: "Grace Hopper" },
  { entity_id: "e3", display_label: "Katherine Johnson" },
  { entity_id: "e4", display_label: "Dorothy Vaughan" },
];

describe("LinkedPeopleAvatars", () => {
  it("renders an avatar per person up to max, then a +N overflow", () => {
    render(<LinkedPeopleAvatars people={PEOPLE} max={3} />);

    const cluster = screen.getByTestId("linked-people-avatars");
    // Three avatars shown, one overflow chip for the fourth person.
    const avatars = within(cluster).getAllByTestId("linked-person-avatar");
    expect(avatars).toHaveLength(3);
    expect(screen.getByTestId("linked-people-overflow").textContent).toBe("+1");
    // Initials mark from first + last name part.
    expect(avatars[0].textContent).toBe("AL");
    // Full list is exposed for assistive technology without a duplicate native tooltip.
    expect(cluster.getAttribute("aria-label")).toBe(
      "Linked people: Ada Lovelace, Grace Hopper, Katherine Johnson, Dorothy Vaughan",
    );
    expect(cluster.getAttribute("title")).toBeNull();
  });

  it("renders nothing when there are no linked people", () => {
    render(<LinkedPeopleAvatars people={[]} />);
    expect(screen.queryByTestId("linked-people-avatars")).toBeNull();
  });
});

describe("LinkedPeopleChips", () => {
  it("renders a labelled chip per person with the resolved display label", () => {
    render(<LinkedPeopleChips people={PEOPLE.slice(0, 2)} />);

    const chips = screen.getAllByTestId("linked-person-chip");
    expect(chips).toHaveLength(2);
    expect(chips[0].textContent).toContain("Ada Lovelace");
    expect(chips[1].textContent).toContain("Grace Hopper");
  });

  it("renders nothing when there are no linked people", () => {
    render(<LinkedPeopleChips people={[]} />);
    expect(screen.queryByTestId("linked-people-chips")).toBeNull();
  });
});
