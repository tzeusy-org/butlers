// @vitest-environment jsdom
/**
 * Component tests for MemorySearch — the one search affordance (bu-2ix8d.6).
 *
 * Acceptance (pr/overview/memory-redesign/prompts/05-search-and-rail.md Part 1):
 *   - Exactly one search input; `/` focuses it from anywhere on the page.
 *   - Enter submits → writes the `q` URL param (deep-linkable).
 *   - `×` / Esc clears `q`, restoring browse mode.
 *   - Kind pills write the `kind` URL param.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router";

import MemorySearch from "@/components/memory/MemorySearch";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let lastSearch = "";
function SearchProbe() {
  const search = useLocation().search;
  useEffect(() => {
    lastSearch = search;
  }, [search]);
  return null;
}

function render(initialEntries: string[] = ["/memory"]) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(
      <MemoryRouter initialEntries={initialEntries}>
        <MemorySearch />
        <SearchProbe />
      </MemoryRouter>,
    );
  });
  return { container, root };
}

function input(container: HTMLElement): HTMLInputElement {
  const el = container.querySelector<HTMLInputElement>('[aria-label="Search memory"]');
  if (!el) throw new Error("search input not found");
  return el;
}

/**
 * Set a controlled input's value the way React's synthetic event system
 * expects: use the native value setter, then dispatch a bubbling `input`
 * event so React's onChange fires and state updates.
 */
function typeInto(el: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    "value",
  )?.set;
  setter?.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

describe("MemorySearch", () => {
  let mounted: { container: HTMLDivElement; root: Root } | null = null;

  beforeEach(() => {
    lastSearch = "";
  });

  afterEach(() => {
    if (mounted) {
      act(() => mounted!.root.unmount());
      mounted.container.remove();
      mounted = null;
    }
    vi.restoreAllMocks();
  });

  it("renders exactly one search input", () => {
    mounted = render();
    expect(
      mounted.container.querySelectorAll('[aria-label="Search memory"]').length,
    ).toBe(1);
  });

  it("focuses the input when `/` is pressed anywhere on the page", () => {
    mounted = render();
    const el = input(mounted.container);
    expect(document.activeElement).not.toBe(el);

    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "/" }));
    });

    expect(document.activeElement).toBe(el);
  });

  it("submits on Enter and writes the q URL param", () => {
    mounted = render();
    const el = input(mounted.container);

    act(() => {
      typeInto(el, "fatigue");
    });
    act(() => {
      el.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Enter", bubbles: true }),
      );
    });

    expect(lastSearch).toContain("q=fatigue");
  });

  it("clears q on Esc, restoring browse mode", () => {
    mounted = render(["/memory?q=fatigue"]);
    const el = input(mounted.container);
    // Seeded from the URL.
    expect(el.value).toBe("fatigue");

    act(() => {
      el.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
      );
    });

    expect(lastSearch).not.toContain("q=");
  });

  it("writes the kind URL param when a scope pill is clicked", () => {
    mounted = render();
    // Find the "Facts" scope pill (role=switch from the Pill primitive).
    const pills = Array.from(
      mounted.container.querySelectorAll<HTMLButtonElement>('[role="switch"]'),
    );
    const factsPill = pills.find((p) => p.textContent?.trim() === "Facts");
    expect(factsPill).toBeTruthy();

    act(() => {
      factsPill!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(lastSearch).toContain("kind=fact");
  });
});
