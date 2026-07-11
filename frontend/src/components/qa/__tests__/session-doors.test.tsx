// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it } from "vitest";

import { SessionDoors } from "@/components/qa";

afterEach(() => {
  cleanup();
});

function renderDoors(props: {
  healingSessionId: string | null;
  sessionIds: string[];
}) {
  return render(
    <MemoryRouter>
      <SessionDoors {...props} />
    </MemoryRouter>,
  );
}

describe("SessionDoors", () => {
  it("renders a real Link door to the investigation session", () => {
    renderDoors({
      healingSessionId: "11111111-2222-3333-4444-555555555555",
      sessionIds: [],
    });

    const doors = screen.getAllByTestId("qa-session-door");
    expect(doors).toHaveLength(1);
    // A real <a href> — navigable, middle-clickable, not a dead onClick.
    expect(doors[0].tagName).toBe("A");
    expect(doors[0].getAttribute("href")).toBe(
      "/sessions/11111111-2222-3333-4444-555555555555",
    );
  });

  it("renders a door for every failing session, in order", () => {
    renderDoors({
      healingSessionId: "11111111-2222-3333-4444-555555555555",
      sessionIds: [
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "99999999-8888-7777-6666-555555555555",
      ],
    });

    const hrefs = screen
      .getAllByTestId("qa-session-door")
      .map((el) => el.getAttribute("href"));
    expect(hrefs).toEqual([
      "/sessions/11111111-2222-3333-4444-555555555555",
      "/sessions/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
      "/sessions/99999999-8888-7777-6666-555555555555",
    ]);
  });

  it("renders no door and no section when there are no sessions", () => {
    const { container } = renderDoors({
      healingSessionId: null,
      sessionIds: [],
    });

    expect(screen.queryByTestId("qa-session-doors")).toBeNull();
    expect(screen.queryByTestId("qa-session-door")).toBeNull();
    // The whole section collapses — no empty header, no broken link.
    expect(container.querySelector("section")).toBeNull();
  });

  it("still renders failing-session doors when no investigation session exists", () => {
    renderDoors({
      healingSessionId: null,
      sessionIds: ["aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"],
    });

    const doors = screen.getAllByTestId("qa-session-door");
    expect(doors).toHaveLength(1);
    expect(doors[0].getAttribute("href")).toBe(
      "/sessions/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    );
  });
});
