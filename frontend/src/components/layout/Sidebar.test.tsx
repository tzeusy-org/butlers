// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";

import Sidebar from "@/components/layout/Sidebar";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

describe("Sidebar", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  function render() {
    act(() => {
      root.render(
        <MemoryRouter>
          <Sidebar />
        </MemoryRouter>,
      );
    });
  }

  it("includes navigation link to calendar workspace", () => {
    render();

    const calendarLink = container.querySelector('a[href="/butlers/calendar"]');
    expect(calendarLink).toBeInstanceOf(HTMLAnchorElement);
    expect(calendarLink?.textContent).toContain("Calendar");
  });

  it("includes navigation link to Ingestion", () => {
    render();

    const ingestionLink = container.querySelector('a[href="/ingestion"]');
    expect(ingestionLink).toBeInstanceOf(HTMLAnchorElement);
    expect(ingestionLink?.textContent).toContain("Ingestion");
  });
});
