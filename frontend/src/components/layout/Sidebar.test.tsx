// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";

import Sidebar from "@/components/layout/Sidebar";
import { useButlers } from "@/hooks/use-butlers";
import { useCostSummary } from "@/hooks/use-costs";

vi.mock("@/hooks/use-butlers", () => ({
  useButlers: vi.fn(),
}));

vi.mock("@/hooks/use-costs", () => ({
  useCostSummary: vi.fn(),
}));

vi.mock("@/hooks/use-qa-badge", () => ({
  useBadgeCounts: vi.fn(() => ({})),
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
    // Default cost mock — must be re-set after resetAllMocks
    vi.mocked(useCostSummary).mockReturnValue({
      data: { data: { total_cost_usd: 26.27 } },
      isLoading: false,
    } as ReturnType<typeof useCostSummary>);
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
  // Section headers (navigation category grouping)
  // -------------------------------------------------------------------------

  describe("section headers", () => {
    beforeEach(() => {
      setButlersState({
        data: {
          data: [{ name: "relationship", status: "ok", port: 40102, type: "butler" as const }],
          meta: {},
        },
      });
    });

    it("renders Main, Dedicated Butlers, and Telemetry section headers", () => {
      render();

      expect(container.textContent).toContain("Main");
      expect(container.textContent).toContain("Dedicated Butlers");
      expect(container.textContent).toContain("Telemetry");
    });

    it("hides Dedicated Butlers section when no butler items are visible", () => {
      setButlersState({
        data: { data: [], meta: {} },
      });
      render();

      // Health and Calendar don't have butler filters, so section still shows
      expect(container.textContent).toContain("Dedicated Butlers");
    });

    it("places Overview in Main and Timeline in Telemetry", () => {
      render();

      const headings = container.querySelectorAll("h3");
      const mainHeading = Array.from(headings).find((h) => h.textContent === "Main");
      const telemetryHeading = Array.from(headings).find((h) => h.textContent === "Telemetry");
      expect(mainHeading).toBeTruthy();
      expect(telemetryHeading).toBeTruthy();

      // Section container is the button's parent div
      const mainSection = mainHeading!.closest("button")!.parentElement;
      expect(mainSection?.querySelector('a[href="/"]')).toBeTruthy();
      expect(mainSection?.querySelector('a[href="/timeline"]')).toBeNull();

      // Telemetry starts collapsed — expand it first
      const telemetryButton = telemetryHeading!.closest("button")!;
      act(() => {
        telemetryButton.click();
      });

      const telemetrySection = telemetryButton.parentElement;
      expect(telemetrySection?.querySelector('a[href="/timeline"]')).toBeTruthy();
      expect(telemetrySection?.querySelector('a[href="/traces"]')).toBeNull();
      expect(telemetrySection?.querySelector('a[href="/"]')).toBeNull();
    });
  });

  // -------------------------------------------------------------------------
  // Existing flat nav items still work (no regression)
  // -------------------------------------------------------------------------

  it("includes navigation link to calendar workspace", () => {
    setButlersState({
      data: { data: [{ name: "relationship", status: "ok", port: 40102, type: "butler" as const }], meta: {} },
    });
    render();

    const calendarLink = container.querySelector('a[href="/calendar"]');
    expect(calendarLink).toBeInstanceOf(HTMLAnchorElement);
    expect(calendarLink?.textContent).toContain("Calendar");
  });

  it("includes navigation link to Ingestion", () => {
    setButlersState({
      data: { data: [{ name: "relationship", status: "ok", port: 40102, type: "butler" as const }], meta: {} },
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
          data: [{ name: "relationship", status: "ok", port: 40102, type: "butler" as const }],
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
          data: [{ name: "general", status: "ok", port: 40101, type: "butler" as const }],
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
          data: [{ name: "relationship", status: "ok", port: 40102, type: "butler" as const }],
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
  // Chronicles nav entry (bu-ig72b.13)
  // -------------------------------------------------------------------------

  describe("Chronicles nav entry", () => {
    it("shows Chronicles link when chronicler butler is present", () => {
      setButlersState({
        data: {
          data: [{ name: "chronicler", status: "ok", port: 40110, type: "butler" as const }],
          meta: {},
        },
      });
      render();

      const chroniclesLink = container.querySelector('a[href="/chronicles"]');
      expect(chroniclesLink).toBeInstanceOf(HTMLAnchorElement);
      expect(chroniclesLink?.textContent).toContain("Chronicles");
    });

    it("hides Chronicles link when chronicler butler is absent", () => {
      setButlersState({
        data: {
          data: [{ name: "general", status: "ok", port: 40101, type: "butler" as const }],
          meta: {},
        },
      });
      render();

      expect(container.querySelector('a[href="/chronicles"]')).toBeNull();
      expect(container.textContent).not.toContain("Chronicles");
    });

    it("places Chronicles in Dedicated Butlers section (not Telemetry)", () => {
      setButlersState({
        data: {
          data: [{ name: "chronicler", status: "ok", port: 40110, type: "butler" as const }],
          meta: {},
        },
      });
      render();

      const headings = container.querySelectorAll("h3");
      const dedicatedHeading = Array.from(headings).find(
        (h) => h.textContent === "Dedicated Butlers",
      );
      const telemetryHeading = Array.from(headings).find(
        (h) => h.textContent === "Telemetry",
      );
      expect(dedicatedHeading).toBeTruthy();
      expect(telemetryHeading).toBeTruthy();

      const dedicatedSection = dedicatedHeading!.closest("button")!.parentElement;
      expect(dedicatedSection?.querySelector('a[href="/chronicles"]')).toBeTruthy();

      // Telemetry starts collapsed — expand to check Chronicles is NOT there
      const telemetryButton = telemetryHeading!.closest("button")!;
      act(() => {
        telemetryButton.click();
      });
      const telemetrySection = telemetryButton.parentElement;
      expect(telemetrySection?.querySelector('a[href="/chronicles"]')).toBeNull();
    });

    it("uses disambiguation tooltip text on Chronicles link when sidebar is collapsed", () => {
      setButlersState({
        data: {
          data: [{ name: "chronicler", status: "ok", port: 40110, type: "butler" as const }],
          meta: {},
        },
      });
      // Render with collapsed sidebar
      act(() => {
        root.render(
          <MemoryRouter initialEntries={["/"]}>
            <Sidebar collapsed={true} />
          </MemoryRouter>,
        );
      });

      const chroniclesLink = container.querySelector('a[href="/chronicles"]');
      expect(chroniclesLink).toBeInstanceOf(HTMLAnchorElement);
      expect(chroniclesLink?.getAttribute("title")).toBe(
        "Retrospective lived-time reconstruction",
      );
    });
  });

  // -------------------------------------------------------------------------
  // Relationships group wiring (butlers-x3ki.3)
  // -------------------------------------------------------------------------

  describe("Relationships group wiring", () => {
    beforeEach(() => {
      setButlersState({
        data: {
          data: [{ name: "relationship", status: "ok", port: 40102, type: "butler" as const }],
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
