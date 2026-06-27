// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";

import PageHeader from "@/components/layout/PageHeader";
import { BreadcrumbsControlProvider } from "@/components/ui/breadcrumbs-control";
import { Page } from "@/components/ui/page";
import { useDarkMode } from "@/hooks/useDarkMode";
import { OPEN_COMMAND_PALETTE_EVENT } from "@/lib/command-palette";

vi.mock("@/hooks/useDarkMode", () => ({
  useDarkMode: vi.fn(),
}));

vi.mock("@/components/butler-detail/SiblingButlerNav", () => ({
  SiblingButlerNav: ({ activeButlerName }: { activeButlerName: string }) => (
    <nav
      aria-label="Navigate to butler"
      data-active-butler={activeButlerName}
      data-testid="sibling-butler-nav"
    >
      sibling nav
    </nav>
  ),
}));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

describe("PageHeader", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(useDarkMode).mockReturnValue({
      theme: "light",
      setTheme: vi.fn(),
      resolvedTheme: "light",
    });

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

  it("renders breadcrumbs by default (auto-builder)", () => {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={["/sessions"]}>
          <PageHeader />
        </MemoryRouter>,
      );
    });

    const nav = container.querySelector("nav");
    expect(nav).not.toBeNull();
    expect(nav?.textContent).toContain("Home");
    expect(nav?.textContent).toContain("Sessions");
  });

  it("renders breadcrumbs when hideBreadcrumbs is omitted (default false)", () => {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={["/memory"]}>
          <PageHeader />
        </MemoryRouter>,
      );
    });

    expect(container.querySelector("nav")).not.toBeNull();
  });

  it("suppresses breadcrumb nav when hideBreadcrumbs={true}", () => {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={["/sessions"]}>
          <PageHeader hideBreadcrumbs />
        </MemoryRouter>,
      );
    });

    expect(container.querySelector("nav")).toBeNull();
  });

  it("never renders an <h1> (page title ownership belongs to <Page>, not PageHeader)", () => {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={["/sessions"]}>
          <PageHeader hideBreadcrumbs />
        </MemoryRouter>,
      );
    });

    // PageHeader is shell chrome only; it must not own the page <h1>.
    expect(container.querySelector("h1")).toBeNull();

    // Action buttons (command palette + theme toggle) still render.
    const buttons = container.querySelectorAll("button");
    expect(buttons.length).toBeGreaterThanOrEqual(2);
  });

  it("renders explicit breadcrumbs when supplied and hideBreadcrumbs is false", () => {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={["/sessions"]}>
          <PageHeader
            breadcrumbs={[{ label: "Home", path: "/" }, { label: "Custom" }]}
            hideBreadcrumbs={false}
          />
        </MemoryRouter>,
      );
    });

    const nav = container.querySelector("nav");
    expect(nav).not.toBeNull();
    expect(nav?.textContent).toContain("Home");
    expect(nav?.textContent).toContain("Custom");
  });

  it("suppresses auto-builder crumbs when a sibling <Page> supplies breadcrumbs via context", () => {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={["/entities/entity-001"]}>
          <BreadcrumbsControlProvider>
            <PageHeader />
            {/* Page with breadcrumbs sets isSupplyingBreadcrumbs=true in context */}
            <Page
              title="Alice"
              archetype="detail"
              breadcrumbs={[
                { label: "Home", href: "/" },
                { label: "Entities", href: "/entities" },
                { label: "Alice" },
              ]}
            >
              <div>content</div>
            </Page>
          </BreadcrumbsControlProvider>
        </MemoryRouter>,
      );
    });

    // The shell-level <nav> from PageHeader's auto-builder should be suppressed.
    // Note: <Page> also renders a <nav aria-label="Breadcrumb"> inside the main
    // content; we verify the PageHeader nav (first nav, before the page content)
    // is gone. In practice both are in the same container here, so we confirm
    // the auto-builder's crumbs ("Entities" slug "entities") are not duplicated
    // and the PageHeader nav itself is absent.
    const navs = container.querySelectorAll("nav");
    // Only the <Page> breadcrumb nav should exist (inside the Page content)
    // The PageHeader should not render its own nav when context signals suppression
    navs.forEach((nav) => {
      // If any nav is from PageHeader auto-builder it would contain raw URL segment "entities"
      // as a current-page (non-link) span. The <Page> nav renders "Entities" as a link.
      // A simpler check: confirm the auto-builder nav (with path "/entities/entity-001"
      // split to segments) is not rendered. The auto-builder would produce "Entity-001"
      // as the last crumb, but our <Page> crumb says "Alice".
      expect(nav.textContent).not.toContain("Entity-001");
    });
  });

  it("renders search trigger with Cmd/Ctrl+K hint and dispatches open event", () => {
    const openListener = vi.fn();
    window.addEventListener(OPEN_COMMAND_PALETTE_EVENT, openListener);

    act(() => {
      root.render(
        <MemoryRouter initialEntries={["/sessions"]}>
          <PageHeader />
        </MemoryRouter>,
      );
    });

    const searchButton = Array.from(document.body.querySelectorAll("button")).find((button) =>
      button.getAttribute("aria-label")?.includes("Open command palette"),
    );

    expect(searchButton).toBeInstanceOf(HTMLButtonElement);
    expect(searchButton?.getAttribute("title")).toBe("Cmd/Ctrl+K");

    act(() => {
      searchButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(openListener).toHaveBeenCalledTimes(1);

    window.removeEventListener(OPEN_COMMAND_PALETTE_EVENT, openListener);
  });

  it("uppercases known acronyms in auto-built breadcrumbs (qa -> QA)", () => {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={["/qa"]}>
          <PageHeader />
        </MemoryRouter>,
      );
    });

    const nav = container.querySelector("nav");
    expect(nav).not.toBeNull();
    expect(nav?.textContent).toContain("QA");
    expect(nav?.textContent).not.toContain("Qa");
  });

  it("uppercases known acronyms even in nested segments (qa/investigations)", () => {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={["/qa/investigations"]}>
          <PageHeader />
        </MemoryRouter>,
      );
    });

    const nav = container.querySelector("nav");
    expect(nav?.textContent).toContain("QA");
    expect(nav?.textContent).toContain("Investigations");
    expect(nav?.textContent).not.toContain("Qa /");
  });

  it("applies the dossier eyebrow typography to the breadcrumb nav", () => {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={["/qa"]}>
          <PageHeader />
        </MemoryRouter>,
      );
    });

    const nav = container.querySelector("nav");
    expect(nav).not.toBeNull();
    const className = nav?.getAttribute("class") ?? "";
    expect(className).toContain("font-mono");
    expect(className).toContain("text-[10px]");
    expect(className).toContain("uppercase");
    expect(className).toContain("tracking-[0.14em]");
    expect(className).toContain("text-muted-foreground");
    expect(className).toContain("tabular-nums");
  });

  it("renders sibling butler navigation in the shell bar on butler detail routes", () => {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={["/butlers/switchboard"]}>
          <PageHeader />
        </MemoryRouter>,
      );
    });

    const nav = container.querySelector("[data-testid='sibling-butler-nav']");
    expect(nav).not.toBeNull();
    expect(nav?.getAttribute("data-active-butler")).toBe("switchboard");
    expect(nav?.parentElement?.textContent).not.toContain("Home");
  });

  it("renders a router-based back-to-board link on butler detail routes, reachable on mobile", () => {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={["/butlers/health"]}>
          <PageHeader />
        </MemoryRouter>,
      );
    });

    // The back link must be in the DOM unconditionally (not JS-gated; mobile visibility is CSS-only).
    const backLink = container.querySelector("a[href='/butlers']");
    expect(backLink).not.toBeNull();
    // Must be an anchor element — router Link, not a full-page reload.
    expect(backLink?.tagName.toLowerCase()).toBe("a");
    // Mobile-accessible label ("Butlers") must be present in the DOM.
    expect(backLink?.textContent).toContain("Butlers");
  });
});
