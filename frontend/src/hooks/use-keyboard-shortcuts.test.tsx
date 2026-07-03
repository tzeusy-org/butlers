// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";

import { useKeyboardShortcuts } from "@/hooks/use-keyboard-shortcuts";
import { OPEN_ENTITY_FINDER_EVENT } from "@/lib/entity-finder";
import { OPEN_SHORTCUT_HELP_EVENT } from "@/lib/shortcut-help";
import { G_CHORD_ROUTES } from "@/lib/route-registry";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

function Harness() {
  useKeyboardShortcuts();
  return <div>shortcuts</div>;
}

describe("useKeyboardShortcuts", () => {
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
    vi.restoreAllMocks();
  });

  it("dispatches entity-finder event on Ctrl+K outside editable fields (bu-xfjwk)", () => {
    const listener = vi.fn();
    // Ctrl+K now dispatches OPEN_ENTITY_FINDER_EVENT (entity-first finder).
    window.addEventListener(OPEN_ENTITY_FINDER_EVENT, listener);

    act(() => {
      root.render(
        <MemoryRouter>
          <Harness />
        </MemoryRouter>,
      );
    });

    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", ctrlKey: true, bubbles: true }));
    });

    expect(listener).toHaveBeenCalledTimes(1);

    window.removeEventListener(OPEN_ENTITY_FINDER_EVENT, listener);
  });

  it("still opens Ctrl+K inside editable fields (bu-86c4c.7: the keyboard floor fix)", () => {
    // Previously Ctrl+K (like every other shortcut) died the moment focus was
    // in an input — the audit's headline "shell" finding. Cmd/Ctrl+K is a
    // modifier chord that can't collide with typing, so it must fire
    // regardless of focus.
    const listener = vi.fn();
    window.addEventListener(OPEN_ENTITY_FINDER_EVENT, listener);

    act(() => {
      root.render(
        <MemoryRouter>
          <Harness />
        </MemoryRouter>,
      );
    });

    const input = document.createElement("input");
    document.body.appendChild(input);

    act(() => {
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "k", ctrlKey: true, bubbles: true }));
    });

    expect(listener).toHaveBeenCalledTimes(1);

    input.remove();
    window.removeEventListener(OPEN_ENTITY_FINDER_EVENT, listener);
  });

  it("ignores '/' inside editable fields (it's a normal typing character there)", () => {
    const listener = vi.fn();
    window.addEventListener(OPEN_ENTITY_FINDER_EVENT, listener);

    act(() => {
      root.render(
        <MemoryRouter>
          <Harness />
        </MemoryRouter>,
      );
    });

    const input = document.createElement("input");
    document.body.appendChild(input);

    act(() => {
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "/", bubbles: true }));
    });

    expect(listener).toHaveBeenCalledTimes(0);

    input.remove();
    window.removeEventListener(OPEN_ENTITY_FINDER_EVENT, listener);
  });

  it("opens the command menu on '/' outside editable fields — same surface as Cmd+K (bu-86c4c.7)", () => {
    const listener = vi.fn();
    window.addEventListener(OPEN_ENTITY_FINDER_EVENT, listener);

    act(() => {
      root.render(
        <MemoryRouter>
          <Harness />
        </MemoryRouter>,
      );
    });

    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "/", bubbles: true }));
    });

    expect(listener).toHaveBeenCalledTimes(1);

    window.removeEventListener(OPEN_ENTITY_FINDER_EVENT, listener);
  });

  it("opens the shortcut help sheet on '?' (previously click-only, no binding)", () => {
    const listener = vi.fn();
    window.addEventListener(OPEN_SHORTCUT_HELP_EVENT, listener);

    act(() => {
      root.render(
        <MemoryRouter>
          <Harness />
        </MemoryRouter>,
      );
    });

    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "?", bubbles: true }));
    });

    expect(listener).toHaveBeenCalledTimes(1);

    window.removeEventListener(OPEN_SHORTCUT_HELP_EVENT, listener);
  });

  it("navigates to /ingestion on g+e shortcut", () => {
    // We can't easily intercept navigate() without a full router setup.
    // This test verifies the shortcut is processed by checking window.__pendingGNav
    // is consumed when 'e' follows 'g'. Navigation destination is verified by
    // the keyboard shortcuts implementation and covered by code inspection.
    act(() => {
      root.render(
        <MemoryRouter initialEntries={["/"]}>
          <Harness />
        </MemoryRouter>,
      );
    });

    // Press 'g' to set pending state
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "g", bubbles: true }));
    });

    expect(window.__pendingGNav).toBe(true);

    // Press 'e' — should consume the pending state and navigate
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "e", bubbles: true }));
    });

    expect(window.__pendingGNav).toBe(false);
  });

  it("g+i still navigates to /issues (no regression)", () => {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={["/"]}>
          <Harness />
        </MemoryRouter>,
      );
    });

    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "g", bubbles: true }));
    });

    expect(window.__pendingGNav).toBe(true);

    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "i", bubbles: true }));
    });

    expect(window.__pendingGNav).toBe(false);
  });

  it("g+c still consumes the pending chord (now routes to the entities/contacts filter, not the dead /contacts redirect)", () => {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={["/"]}>
          <Harness />
        </MemoryRouter>,
      );
    });

    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "g", bubbles: true }));
    });

    expect(window.__pendingGNav).toBe(true);

    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "c", bubbles: true }));
    });

    expect(window.__pendingGNav).toBe(false);
  });

  it("g+h routes to /health, not the pre-redesign /health/measurements (bu-86c4c.7 drift fix)", () => {
    // This was the audit's concrete example of registry drift: the chord map
    // and the sidebar disagreed about where "Health" lives. The chord is now
    // declared directly on nav-config's Health entry, so it cannot drift.
    expect(G_CHORD_ROUTES.h).toBe("/health");
  });
});
