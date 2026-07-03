// @vitest-environment jsdom
/**
 * Component tests for SubpageTabs (§8.6).
 *
 * Covers:
 * - All 4 tabs render with correct labels and to paths (Plex/Index/
 *   Concentration/Circles — Circles added by bu-86c4c.19, absorbing the
 *   retired /groups vestige)
 * - Active tab gets aria-current="page" based on pathname
 * - Plex tab uses end matching (not active on /entities/hop etc.)
 * - Tab links have proper hrefs in the rendered <a> elements
 * - Custom className prop is applied to the wrapping nav
 * - nav has aria-label="Entity views" for a11y
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

import { SubpageTabs } from "./SubpageTabs";

// ---------------------------------------------------------------------------
// Render helper
// ---------------------------------------------------------------------------

let container: HTMLDivElement;
let root: Root;

function renderTabs(initialEntry = "/entities", customClassName?: string) {
  act(() => {
    root.render(
      <MemoryRouter initialEntries={[initialEntry]}>
        <SubpageTabs className={customClassName} />
      </MemoryRouter>,
    );
  });
}

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
  document.body.innerHTML = "";
});

// ---------------------------------------------------------------------------
// Accessibility and structure
// ---------------------------------------------------------------------------

describe("SubpageTabs — accessibility and structure", () => {
  it("renders a nav element with aria-label='Entity views'", () => {
    renderTabs();
    const nav = container.querySelector("nav[aria-label='Entity views']");
    expect(nav).toBeTruthy();
  });

  it("renders all 4 tabs with correct labels", () => {
    renderTabs();
    const nav = container.querySelector("nav[aria-label='Entity views']");
    const links = nav?.querySelectorAll("a") ?? [];
    const labels = Array.from(links).map((a) => a.textContent?.trim());

    expect(labels).toContain("Plex");
    expect(labels).toContain("Index");
    expect(labels).toContain("Concentration");
    expect(labels).toContain("Circles");
  });
});

// ---------------------------------------------------------------------------
// Tab links and routing
// ---------------------------------------------------------------------------

describe("SubpageTabs — tab links and routing", () => {
  it("each tab link has the correct href attribute", () => {
    renderTabs();
    const nav = container.querySelector("nav[aria-label='Entity views']");
    const links = nav?.querySelectorAll("a") ?? [];

    const hrefMap = new Map(Array.from(links).map((a) => [a.textContent?.trim(), a.getAttribute("href")]));

    expect(hrefMap.get("Plex")).toBe("/entities");
    expect(hrefMap.get("Index")).toBe("/entities/index");
    expect(hrefMap.get("Concentration")).toBe("/entities/concentration");
    expect(hrefMap.get("Circles")).toBe("/entities/circles");
  });
});

// ---------------------------------------------------------------------------
// Active tab styling (aria-current)
// ---------------------------------------------------------------------------

describe("SubpageTabs — active tab styling", () => {
  it("Plex tab has aria-current='page' when at /entities", () => {
    renderTabs("/entities");
    const nav = container.querySelector("nav[aria-label='Entity views']");
    const links = nav?.querySelectorAll("a") ?? [];
    const plexLink = Array.from(links).find((a) => a.textContent?.trim() === "Plex");

    expect(plexLink?.getAttribute("aria-current")).toBe("page");
  });

  it("Index tab has aria-current='page' when at /entities/index", () => {
    renderTabs("/entities/index");
    const nav = container.querySelector("nav[aria-label='Entity views']");
    const links = nav?.querySelectorAll("a") ?? [];
    const indexLink = Array.from(links).find((a) => a.textContent?.trim() === "Index");

    expect(indexLink?.getAttribute("aria-current")).toBe("page");
  });

  it("Concentration tab has aria-current='page' when at /entities/concentration", () => {
    renderTabs("/entities/concentration");
    const nav = container.querySelector("nav[aria-label='Entity views']");
    const links = nav?.querySelectorAll("a") ?? [];
    const concentrationLink = Array.from(links).find(
      (a) => a.textContent?.trim() === "Concentration",
    );

    expect(concentrationLink?.getAttribute("aria-current")).toBe("page");
  });

  it("Circles tab has aria-current='page' when at /entities/circles", () => {
    renderTabs("/entities/circles");
    const nav = container.querySelector("nav[aria-label='Entity views']");
    const links = nav?.querySelectorAll("a") ?? [];
    const circlesLink = Array.from(links).find((a) => a.textContent?.trim() === "Circles");

    expect(circlesLink?.getAttribute("aria-current")).toBe("page");
  });

  it("Plex tab does not stay active on /entities/concentration (end matching)", () => {
    renderTabs("/entities/concentration");
    const nav = container.querySelector("nav[aria-label='Entity views']");
    const links = nav?.querySelectorAll("a") ?? [];
    const plexLink = Array.from(links).find((a) => a.textContent?.trim() === "Plex");

    expect(plexLink?.getAttribute("aria-current")).toBeNull();
  });

  it("Plex tab does not stay active on /entities/index (end matching)", () => {
    renderTabs("/entities/index");
    const nav = container.querySelector("nav[aria-label='Entity views']");
    const links = nav?.querySelectorAll("a") ?? [];
    const plexLink = Array.from(links).find((a) => a.textContent?.trim() === "Plex");

    expect(plexLink?.getAttribute("aria-current")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Custom className prop
// ---------------------------------------------------------------------------

describe("SubpageTabs — custom className", () => {
  it("applies custom className to the nav element", () => {
    renderTabs("/entities", "my-custom-class");
    const nav = container.querySelector("nav[aria-label='Entity views']");

    expect(nav?.className).toContain("my-custom-class");
  });

  it("renders nav with default classes even when custom className is not provided", () => {
    renderTabs("/entities");
    const nav = container.querySelector("nav[aria-label='Entity views']");

    expect(nav?.className).toContain("flex");
    expect(nav?.className).toContain("gap-1");
    expect(nav?.className).toContain("border-b");
  });
});
