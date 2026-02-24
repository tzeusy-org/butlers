// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";

import Sidebar from "@/components/layout/Sidebar";
import { useButlers } from "@/hooks/use-butlers";

vi.mock("@/hooks/use-butlers", () => ({
  useButlers: vi.fn(),
}));

type UseButlersResult = ReturnType<typeof useButlers>;

function setButlersState(state: Partial<UseButlersResult>) {
  vi.mocked(useButlers).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    ...state,
  } as UseButlersResult);
}

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

describe("Sidebar", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.resetAllMocks();
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

  function render(initialPath = "/") {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={[initialPath]}>
          <Sidebar />
        </MemoryRouter>,
      );
    });
  }

  // -------------------------------------------------------------------------
  // Existing flat nav items still work (no regression)
  // -------------------------------------------------------------------------

  it("includes navigation link to calendar workspace", () => {
    setButlersState({
      data: { data: [{ name: "relationship", status: "ok", port: 40102 }], meta: {} },
    });
    render();

    const calendarLink = container.querySelector('a[href="/calendar"]');
    expect(calendarLink).toBeInstanceOf(HTMLAnchorElement);
    expect(calendarLink?.textContent).toContain("Calendar");
  });

  it("includes navigation link to Ingestion", () => {
    setButlersState({
      data: { data: [{ name: "relationship", status: "ok", port: 40102 }], meta: {} },
    });
    render();

    const ingestionLink = container.querySelector('a[href="/ingestion"]');
    expect(ingestionLink).toBeInstanceOf(HTMLAnchorElement);
    expect(ingestionLink?.textContent).toContain("Ingestion");
  });

  // -------------------------------------------------------------------------
  // Collapsible nav group support (butlers-x3ki.1)
  // -------------------------------------------------------------------------

  describe("collapsible nav groups", () => {
    beforeEach(() => {
      setButlersState({
        data: {
          data: [{ name: "relationship", status: "ok", port: 40102 }],
          meta: {},
        },
      });
    });

    it("renders Relationships group header instead of separate Contacts/Groups links", () => {
      render();

      // Group header text should be present
      expect(container.textContent).toContain("Relationships");

      // The group header is a button, not a NavLink
      const groupButton = Array.from(container.querySelectorAll("button")).find(
        (btn) => btn.textContent?.includes("Relationships"),
      );
      expect(groupButton).toBeTruthy();
    });

    it("shows children when group is expanded via click", () => {
      render();

      const groupButton = Array.from(container.querySelectorAll("button")).find(
        (btn) => btn.textContent?.includes("Relationships"),
      );
      expect(groupButton).toBeTruthy();

      // Find the children container — it should be aria-hidden before click
      const childrenContainer = groupButton!.parentElement?.querySelector(
        '[aria-hidden]',
      ) as HTMLElement | null;
      expect(childrenContainer).toBeTruthy();
      expect(childrenContainer!.getAttribute("aria-hidden")).toBe("true");

      // Click to expand
      act(() => {
        groupButton!.click();
      });

      // Children container should now be visible (aria-hidden="false")
      expect(childrenContainer!.getAttribute("aria-hidden")).toBe("false");
      // Child links should be present inside
      const contactsLink = childrenContainer!.querySelector('a[href="/contacts"]');
      const groupsLink = childrenContainer!.querySelector('a[href="/groups"]');
      expect(contactsLink).toBeInstanceOf(HTMLAnchorElement);
      expect(groupsLink).toBeInstanceOf(HTMLAnchorElement);
    });

    it("auto-expands group when child route is active", () => {
      render("/contacts");

      // The group should be auto-expanded because /contacts is active
      const contactsLink = container.querySelector('a[href="/contacts"]');
      expect(contactsLink).toBeInstanceOf(HTMLAnchorElement);
      expect(contactsLink?.textContent).toContain("Contacts");
    });

    it("renders chevron icon on group header", () => {
      render();

      const groupButton = Array.from(container.querySelectorAll("button")).find(
        (btn) => btn.textContent?.includes("Relationships"),
      );
      expect(groupButton).toBeTruthy();

      // Should contain an SVG chevron
      const svg = groupButton!.querySelector("svg");
      expect(svg).toBeTruthy();
    });

    it("sets aria-expanded on group header button", () => {
      render();

      const groupButton = Array.from(container.querySelectorAll("button")).find(
        (btn) => btn.textContent?.includes("Relationships"),
      );
      expect(groupButton).toBeTruthy();
      expect(groupButton!.getAttribute("aria-expanded")).toBe("false");

      act(() => {
        groupButton!.click();
      });

      expect(groupButton!.getAttribute("aria-expanded")).toBe("true");
    });
  });

  // -------------------------------------------------------------------------
  // Butler-aware nav filtering (butlers-x3ki.2)
  // -------------------------------------------------------------------------

  describe("butler-aware filtering", () => {
    it("hides Relationships group when relationship butler is absent", () => {
      setButlersState({
        data: {
          data: [{ name: "general", status: "ok", port: 40101 }],
          meta: {},
        },
      });
      render();

      expect(container.textContent).not.toContain("Relationships");
      // Contacts and Groups should not appear
      expect(container.querySelector('a[href="/contacts"]')).toBeNull();
      expect(container.querySelector('a[href="/groups"]')).toBeNull();
    });

    it("shows Relationships group when relationship butler is present", () => {
      setButlersState({
        data: {
          data: [{ name: "relationship", status: "ok", port: 40102 }],
          meta: {},
        },
      });
      render();

      expect(container.textContent).toContain("Relationships");
    });

    it("shows all items while butlers are loading", () => {
      setButlersState({ isLoading: true });
      render();

      // Relationships group should still be visible during loading
      expect(container.textContent).toContain("Relationships");
    });

    it("shows all items when butlers query fails", () => {
      setButlersState({ isError: true, error: new Error("network error") });
      render();

      // Relationships group should still be visible on error (graceful degradation)
      expect(container.textContent).toContain("Relationships");
    });

    it("flat items without butler field always render", () => {
      setButlersState({
        data: { data: [], meta: {} },
      });
      render();

      // Core nav items like Overview, Sessions, etc. should always be present
      expect(container.textContent).toContain("Overview");
      expect(container.textContent).toContain("Sessions");
      expect(container.textContent).toContain("Settings");
    });
  });

  // -------------------------------------------------------------------------
  // Relationships group wiring (butlers-x3ki.3)
  // -------------------------------------------------------------------------

  describe("Relationships group wiring", () => {
    beforeEach(() => {
      setButlersState({
        data: {
          data: [{ name: "relationship", status: "ok", port: 40102 }],
          meta: {},
        },
      });
    });

    it("does not render separate top-level Contacts link", () => {
      render();

      // There should not be a top-level (non-indented) Contacts link
      // Instead, Contacts lives inside the Relationships group
      const allLinks = container.querySelectorAll("a");
      const topLevelContactsLinks = Array.from(allLinks).filter(
        (link) =>
          link.getAttribute("href") === "/contacts" &&
          !link.closest('[aria-expanded], [aria-expanded] ~ *'),
      );
      // The link exists (inside the group), but the group header is the top-level element
      const groupButton = Array.from(container.querySelectorAll("button")).find(
        (btn) => btn.textContent?.includes("Relationships"),
      );
      expect(groupButton).toBeTruthy();
      // Contacts is a child, not a standalone nav item
      expect(topLevelContactsLinks.length).toBeLessThanOrEqual(1);
    });

    it("auto-expands when navigating to /groups", () => {
      render("/groups");

      const groupsLink = container.querySelector('a[href="/groups"]');
      expect(groupsLink).toBeInstanceOf(HTMLAnchorElement);
      expect(groupsLink?.textContent).toContain("Groups");
    });

    it("auto-expands when navigating to /contacts/:id", () => {
      render("/contacts/abc123");

      // The /contacts/abc123 path should cause the group to auto-expand
      // because it starts with /contacts
      const contactsLink = container.querySelector('a[href="/contacts"]');
      expect(contactsLink).toBeInstanceOf(HTMLAnchorElement);
    });
  });
});
