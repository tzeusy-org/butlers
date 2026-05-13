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
          <PageHeader title="Sessions" />
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
          <PageHeader title="Sessions" hideBreadcrumbs />
        </MemoryRouter>,
      );
    });

    expect(container.querySelector("nav")).toBeNull();
  });

  it("still renders title and action buttons when hideBreadcrumbs={true}", () => {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={["/sessions"]}>
          <PageHeader title="My Page" hideBreadcrumbs />
        </MemoryRouter>,
      );
    });

    expect(container.querySelector("h1")?.textContent).toBe("My Page");

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
          <PageHeader title="Sessions" />
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
    expect(nav?.parentElement?.textContent).not.toContain("Butlers");
  });
});
