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

// Radix Tooltip renders content in a portal — skip portal assertions in JSDOM
vi.mock("radix-ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("radix-ui")>();
  return {
    ...actual,
    Tooltip: {
      ...actual.Tooltip,
      Portal: ({ children }: { children: React.ReactNode }) => children,
    },
  };
});

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

  function renderMobile(initialPath = "/") {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={[initialPath]}>
          <Sidebar mobileExpanded />
        </MemoryRouter>,
      );
    });
  }

  // -------------------------------------------------------------------------
  // Rail geometry: brand mark
  // -------------------------------------------------------------------------

  describe("brand mark", () => {
    beforeEach(() => {
      setButlersState({
        data: { data: [], meta: {} },
      });
    });

    it("renders brand mark with testid in icon rail", () => {
      render();

      const brandDiv = container.querySelector("[data-testid='sidebar-brand']");
      expect(brandDiv).toBeTruthy();
      // Rail shows only "B" letter mark
      expect(brandDiv?.textContent).toContain("B");
    });

    it("renders Butlers label in mobile expanded mode", () => {
      renderMobile();

      const brandDiv = container.querySelector("[data-testid='sidebar-brand']");
      expect(brandDiv).toBeTruthy();
      expect(brandDiv?.textContent).toContain("Butlers");
    });
  });

  // -------------------------------------------------------------------------
  // Navigation links present in the rail
  // -------------------------------------------------------------------------

  it("includes navigation link to Overview in icon rail", () => {
    setButlersState({
      data: { data: [], meta: {} },
    });
    render();

    const overviewLink = container.querySelector('a[href="/"]');
    expect(overviewLink).toBeInstanceOf(HTMLAnchorElement);
  });

  it("includes navigation link to calendar workspace", () => {
    setButlersState({
      data: { data: [{ name: "relationship", status: "ok", port: 40102, type: "butler" as const }], meta: {} },
    });
    render();

    const calendarLink = container.querySelector('a[href="/calendar"]');
    expect(calendarLink).toBeInstanceOf(HTMLAnchorElement);
  });

  it("includes navigation link to Ingestion", () => {
    setButlersState({
      data: { data: [{ name: "relationship", status: "ok", port: 40102, type: "butler" as const }], meta: {} },
    });
    render();

    const ingestionLink = container.querySelector('a[href="/ingestion"]');
    expect(ingestionLink).toBeInstanceOf(HTMLAnchorElement);
  });

  // -------------------------------------------------------------------------
  // Mobile expanded mode renders labels alongside links
  // -------------------------------------------------------------------------

  describe("mobile expanded mode", () => {
    beforeEach(() => {
      setButlersState({
        data: {
          data: [{ name: "relationship", status: "ok", port: 40102, type: "butler" as const }],
          meta: {},
        },
      });
    });

    it("renders Main, Dedicated Butlers, and Telemetry section headers in mobile mode", () => {
      renderMobile();

      expect(container.textContent).toContain("Main");
      expect(container.textContent).toContain("Dedicated Butlers");
      expect(container.textContent).toContain("Telemetry");
    });

    it("renders nav labels in mobile expanded mode", () => {
      renderMobile();

      expect(container.textContent).toContain("Overview");
      expect(container.textContent).toContain("Calendar");
      expect(container.textContent).toContain("Relationships");
    });

    it("shows today's spend in mobile expanded footer", () => {
      renderMobile();

      expect(container.textContent).toContain("$26.27");
    });
  });

  // -------------------------------------------------------------------------
  // Tooltip: rail items have aria-label matching label
  // -------------------------------------------------------------------------

  describe("tooltip / aria-label on rail items", () => {
    beforeEach(() => {
      setButlersState({
        data: {
          data: [{ name: "chronicler", status: "ok", port: 40110, type: "butler" as const }],
          meta: {},
        },
      });
    });

    it("chronicle link has disambiguation tooltip as aria-label", () => {
      render();

      const chroniclesLink = container.querySelector('a[href="/chronicles"]');
      expect(chroniclesLink).toBeInstanceOf(HTMLAnchorElement);
      expect(chroniclesLink?.getAttribute("aria-label")).toBe(
        "Retrospective lived-time reconstruction",
      );
    });

    it("overview link has aria-label matching label", () => {
      render();

      const overviewLink = container.querySelector('a[href="/"]');
      expect(overviewLink?.getAttribute("aria-label")).toBe("Overview");
    });
  });

  // -------------------------------------------------------------------------
  // Active state: active item renders with a data-active attribute or class
  // React Router NavLink calls the className function; DOM reflects resolved value.
  // We verify the link is present (active) and check the data-slot for the trigger.
  // -------------------------------------------------------------------------

  it("active item link is present when its route matches", () => {
    setButlersState({
      data: { data: [], meta: {} },
    });
    render("/");

    // The Overview link should be in the DOM when on route "/"
    const overviewLink = container.querySelector('a[href="/"]');
    expect(overviewLink).toBeInstanceOf(HTMLAnchorElement);
    // In MemoryRouter the active NavLink gets "active" appended to className by react-router
    expect(overviewLink?.className).toContain("active");
  });

  // -------------------------------------------------------------------------
  // Status dots on butler-associated items
  // -------------------------------------------------------------------------

  describe("status dots", () => {
    it("renders a status dot on a degraded butler's nav item", () => {
      setButlersState({
        data: {
          data: [
            { name: "relationship", status: "degraded", port: 40102, type: "butler" as const },
          ],
          meta: {},
        },
      });
      render();

      // The Relationships group header should contain a dot span
      const groupButton = Array.from(container.querySelectorAll("button")).find(
        (btn) => btn.getAttribute("aria-label") === "Relationships",
      );
      expect(groupButton).toBeTruthy();

      // A status dot span with amber color should be inside the group button
      const dot = groupButton!.querySelector(".bg-amber-500");
      expect(dot).toBeTruthy();
    });

    it("renders a status dot on an error butler's nav item", () => {
      setButlersState({
        data: {
          data: [
            { name: "relationship", status: "error", port: 40102, type: "butler" as const },
          ],
          meta: {},
        },
      });
      render();

      const groupButton = Array.from(container.querySelectorAll("button")).find(
        (btn) => btn.getAttribute("aria-label") === "Relationships",
      );
      expect(groupButton).toBeTruthy();
      const dot = groupButton!.querySelector(".bg-destructive");
      expect(dot).toBeTruthy();
    });

    it("does not render a status dot when butler status is ok", () => {
      setButlersState({
        data: {
          data: [
            { name: "relationship", status: "ok", port: 40102, type: "butler" as const },
          ],
          meta: {},
        },
      });
      render();

      const groupButton = Array.from(container.querySelectorAll("button")).find(
        (btn) => btn.getAttribute("aria-label") === "Relationships",
      );
      expect(groupButton).toBeTruthy();
      expect(groupButton!.querySelector(".bg-amber-500")).toBeNull();
      expect(groupButton!.querySelector(".bg-destructive")).toBeNull();
    });
  });

  // -------------------------------------------------------------------------
  // Footer summary dot
  // -------------------------------------------------------------------------

  describe("footer status dot", () => {
    it("shows green dot when all butlers are ok", () => {
      setButlersState({
        data: {
          data: [{ name: "relationship", status: "ok", port: 40102, type: "butler" as const }],
          meta: {},
        },
      });
      render();

      // The footer should contain a green dot
      const greenDot = container.querySelector(".bg-green-500");
      expect(greenDot).toBeTruthy();
    });

    it("shows amber dot when any butler is degraded", () => {
      setButlersState({
        data: {
          data: [{ name: "relationship", status: "degraded", port: 40102, type: "butler" as const }],
          meta: {},
        },
      });
      render();

      // Footer dot is distinct from the status dot on nav items
      // At least one amber dot renders somewhere (footer or item)
      const amberDots = container.querySelectorAll(".bg-amber-500");
      expect(amberDots.length).toBeGreaterThan(0);
    });

    it("footer title attribute contains status summary", () => {
      setButlersState({
        data: {
          data: [{ name: "relationship", status: "degraded", port: 40102, type: "butler" as const }],
          meta: {},
        },
      });
      render();

      const footerEl = Array.from(container.querySelectorAll("[title]")).find((el) => {
        const title = el.getAttribute("title") ?? "";
        return title.includes("degraded") || title.includes("ok") || title.includes("error");
      });
      expect(footerEl).toBeTruthy();
    });

    it("shows neutral dot and loading title while butlers query is loading", () => {
      setButlersState({ isLoading: true });
      render();

      // No green, amber, or red dot when state is unknown
      expect(container.querySelector(".bg-green-500")).toBeNull();
      expect(container.querySelector(".bg-amber-500")).toBeNull();
      expect(container.querySelector(".bg-destructive")).toBeNull();

      // Neutral dot is present
      const neutralDot = container.querySelector(".bg-muted-foreground\\/40");
      expect(neutralDot).toBeTruthy();

      // Title reflects loading state
      const footerEl = Array.from(container.querySelectorAll("[title]")).find(
        (el) => el.getAttribute("title") === "Loading butlers",
      );
      expect(footerEl).toBeTruthy();
    });

    it("shows neutral dot and error title when butlers query fails", () => {
      setButlersState({ isError: true, error: new Error("network error") });
      render();

      // No green, amber, or red dot when state is unknown
      expect(container.querySelector(".bg-green-500")).toBeNull();
      expect(container.querySelector(".bg-amber-500")).toBeNull();
      expect(container.querySelector(".bg-destructive")).toBeNull();

      // Neutral dot is present
      const neutralDot = container.querySelector(".bg-muted-foreground\\/40");
      expect(neutralDot).toBeTruthy();

      // Title reflects error state
      const footerEl = Array.from(container.querySelectorAll("[title]")).find(
        (el) => el.getAttribute("title") === "Butlers query failed",
      );
      expect(footerEl).toBeTruthy();
    });

    it("shows red dot when any butler has error status (success path)", () => {
      setButlersState({
        data: {
          data: [{ name: "relationship", status: "error", port: 40102, type: "butler" as const }],
          meta: {},
        },
      });
      render();

      // Footer title shows error count
      const footerEl = Array.from(container.querySelectorAll("[title]")).find((el) => {
        const title = el.getAttribute("title") ?? "";
        return title.includes("error");
      });
      expect(footerEl).toBeTruthy();
    });
  });

  // -------------------------------------------------------------------------
  // Collapsible nav groups in icon rail (Relationships)
  // -------------------------------------------------------------------------

  describe("collapsible nav groups (icon rail)", () => {
    beforeEach(() => {
      setButlersState({
        data: {
          data: [{ name: "relationship", status: "ok", port: 40102, type: "butler" as const }],
          meta: {},
        },
      });
    });

    it("renders Relationships group header as a button with aria-label", () => {
      render();

      const groupButton = Array.from(container.querySelectorAll("button")).find(
        (btn) => btn.getAttribute("aria-label") === "Relationships",
      );
      expect(groupButton).toBeTruthy();
    });

    it("shows children when group is expanded via click", () => {
      render();

      const groupButton = Array.from(container.querySelectorAll("button")).find(
        (btn) => btn.getAttribute("aria-label") === "Relationships",
      );
      expect(groupButton).toBeTruthy();

      // Before expanding: child links should not be accessible (inside inert container)
      // After expanding: child links appear and are focusable
      act(() => {
        groupButton!.click();
      });

      // After expanding, contacts and groups links are present
      const contactsLink = container.querySelector('a[href="/contacts"]');
      const groupsLink = container.querySelector('a[href="/groups"]');
      expect(contactsLink).toBeInstanceOf(HTMLAnchorElement);
      expect(groupsLink).toBeInstanceOf(HTMLAnchorElement);
    });

    it("auto-expands group when child route is active", () => {
      render("/contacts");

      const contactsLink = container.querySelector('a[href="/contacts"]');
      expect(contactsLink).toBeInstanceOf(HTMLAnchorElement);
    });

    it("sets aria-expanded on group header button", () => {
      render();

      const groupButton = Array.from(container.querySelectorAll("button")).find(
        (btn) => btn.getAttribute("aria-label") === "Relationships",
      );
      expect(groupButton).toBeTruthy();
      expect(groupButton!.getAttribute("aria-expanded")).toBe("false");

      act(() => {
        groupButton!.click();
      });

      expect(groupButton!.getAttribute("aria-expanded")).toBe("true");
    });

    it("renders chevron svg on group header button", () => {
      render();

      const groupButton = Array.from(container.querySelectorAll("button")).find(
        (btn) => btn.getAttribute("aria-label") === "Relationships",
      );
      expect(groupButton).toBeTruthy();
      const svg = groupButton!.querySelector("svg");
      expect(svg).toBeTruthy();
    });

    it("collapsed children container has inert attribute then removes it on expand", () => {
      render();

      const groupButton = Array.from(container.querySelectorAll("button")).find(
        (btn) => btn.getAttribute("aria-label") === "Relationships",
      );
      expect(groupButton).toBeTruthy();

      // Find inert div that is a descendant of the NavGroup root
      // The NavGroup root is an ancestor of the button that also contains the children div.
      // Walk up from button looking for the first ancestor that has an inert child.
      let ancestor: HTMLElement | null = groupButton!.parentElement;
      let childrenContainer: HTMLElement | null = null;
      while (ancestor && ancestor !== container) {
        const inertChild = ancestor.querySelector("[inert]") as HTMLElement | null;
        if (inertChild) {
          childrenContainer = inertChild;
          break;
        }
        ancestor = ancestor.parentElement;
      }
      expect(childrenContainer).toBeTruthy();
      expect(childrenContainer!.hasAttribute("inert")).toBe(true);

      act(() => {
        groupButton!.click();
      });
      expect(childrenContainer!.hasAttribute("inert")).toBe(false);
    });
  });

  // -------------------------------------------------------------------------
  // Mobile group expand
  // -------------------------------------------------------------------------

  describe("collapsible nav groups (mobile expanded)", () => {
    beforeEach(() => {
      setButlersState({
        data: {
          data: [{ name: "relationship", status: "ok", port: 40102, type: "butler" as const }],
          meta: {},
        },
      });
    });

    it("renders Relationships group header as a button in mobile mode", () => {
      renderMobile();

      const groupButton = Array.from(container.querySelectorAll("button")).find(
        (btn) => btn.textContent?.includes("Relationships"),
      );
      expect(groupButton).toBeTruthy();
    });

    it("shows children when group is expanded via click in mobile mode", () => {
      renderMobile();

      const groupButton = Array.from(container.querySelectorAll("button")).find(
        (btn) => btn.textContent?.includes("Relationships"),
      );
      expect(groupButton).toBeTruthy();

      // Before click: contacts link is inside an inert/aria-hidden container
      // After click: contacts link becomes accessible
      act(() => {
        groupButton!.click();
      });

      const contactsLink = container.querySelector('a[href="/contacts"]');
      expect(contactsLink).toBeInstanceOf(HTMLAnchorElement);
    });

    it("auto-expands group when navigating to /groups in mobile mode", () => {
      act(() => {
        root.render(
          <MemoryRouter initialEntries={["/groups"]}>
            <Sidebar mobileExpanded />
          </MemoryRouter>,
        );
      });

      const groupsLink = container.querySelector('a[href="/groups"]');
      expect(groupsLink).toBeInstanceOf(HTMLAnchorElement);
    });
  });

  // -------------------------------------------------------------------------
  // Butler-aware nav filtering
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

      const groupButton = Array.from(container.querySelectorAll("button")).find(
        (btn) => btn.getAttribute("aria-label") === "Relationships",
      );
      expect(groupButton).toBeTruthy();
    });

    it("shows all items while butlers are loading", () => {
      setButlersState({ isLoading: true });
      render();

      const groupButton = Array.from(container.querySelectorAll("button")).find(
        (btn) => btn.getAttribute("aria-label") === "Relationships",
      );
      expect(groupButton).toBeTruthy();
    });

    it("shows all items when butlers query fails", () => {
      setButlersState({ isError: true, error: new Error("network error") });
      render();

      const groupButton = Array.from(container.querySelectorAll("button")).find(
        (btn) => btn.getAttribute("aria-label") === "Relationships",
      );
      expect(groupButton).toBeTruthy();
    });

    it("flat items without butler field always render", () => {
      setButlersState({
        data: { data: [], meta: {} },
      });
      render();

      expect(container.querySelector('a[href="/"]')).toBeInstanceOf(HTMLAnchorElement);
      expect(container.querySelector('a[href="/sessions"]')).toBeInstanceOf(HTMLAnchorElement);
      expect(container.querySelector('a[href="/settings"]')).toBeInstanceOf(HTMLAnchorElement);
    });
  });

  // -------------------------------------------------------------------------
  // Chronicles nav entry
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
    });

    it("uses disambiguation tooltip as aria-label on Chronicles link", () => {
      setButlersState({
        data: {
          data: [{ name: "chronicler", status: "ok", port: 40110, type: "butler" as const }],
          meta: {},
        },
      });
      render();

      const chroniclesLink = container.querySelector('a[href="/chronicles"]');
      expect(chroniclesLink).toBeInstanceOf(HTMLAnchorElement);
      expect(chroniclesLink?.getAttribute("aria-label")).toBe(
        "Retrospective lived-time reconstruction",
      );
    });
  });

  // -------------------------------------------------------------------------
  // Relationships group wiring
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

    it("auto-expands when navigating to /groups", () => {
      render("/groups");

      const groupsLink = container.querySelector('a[href="/groups"]');
      expect(groupsLink).toBeInstanceOf(HTMLAnchorElement);
    });

    it("auto-expands when navigating to /contacts/:id", () => {
      render("/contacts/abc123");

      const contactsLink = container.querySelector('a[href="/contacts"]');
      expect(contactsLink).toBeInstanceOf(HTMLAnchorElement);
    });
  });
});
