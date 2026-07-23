// @vitest-environment jsdom

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import TopSessionsTable from "./TopSessionsTable";

afterEach(() => {
  cleanup();
});

function renderTable(props: Partial<React.ComponentProps<typeof TopSessionsTable>> = {}) {
  return render(
    <MemoryRouter>
      <TopSessionsTable sessions={[]} {...props} />
    </MemoryRouter>,
  );
}

describe("TopSessionsTable availability states", () => {
  it("renders a direct query failure as unavailable before the successful-empty message", () => {
    renderTable({ isUnavailable: true });

    expect(screen.getByTestId("top-sessions-unavailable")).toBeTruthy();
    expect(screen.queryByText("No session data available")).toBeNull();
  });

  it("keeps a successful empty result calm", () => {
    renderTable();

    expect(screen.getByText("No session data available")).toBeTruthy();
    expect(screen.queryByTestId("top-sessions-unavailable")).toBeNull();
  });
});
