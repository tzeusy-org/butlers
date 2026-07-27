// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { fireEvent } from "@testing-library/react";

import Sidebar from "@/components/layout/Sidebar";
import { usePrefetchOnIntent } from "@/hooks/use-prefetch-on-intent";
import { useRouteChunkPrefetchOnIntent } from "@/hooks/use-route-chunk-prefetch-on-intent";
import { useSpendSummary } from "@/hooks/use-spend";
import { useBadgeCounts } from "@/hooks/use-qa-badge";
import { resetUseButlersMock, setUseButlersState } from "@/test-utils/use-butlers";

vi.mock("@/hooks/use-butlers", () => ({
  useButlers: vi.fn(),
}));

vi.mock("@/hooks/use-spend", () => ({
  useSpendSummary: vi.fn(),
}));

vi.mock("@/hooks/use-qa-badge", () => ({
  useBadgeCounts: vi.fn(() => ({})),
}));

vi.mock("@/hooks/use-prefetch-on-intent", () => ({
  usePrefetchOnIntent: vi.fn(),
}));

vi.mock("@/hooks/use-route-chunk-prefetch-on-intent", () => ({
  useRouteChunkPrefetchOnIntent: vi.fn(),
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

const setButlersState = setUseButlersState;

const dataPrefetchHandlers = {
  schedule: vi.fn(),
  cancel: vi.fn(),
  onPointerEnter: vi.fn(),
  onPointerLeave: vi.fn(),
  onFocus: vi.fn(),
  onBlur: vi.fn(),
};

const chunkPrefetchHandlers = {
  schedule: vi.fn(),
  cancel: vi.fn(),
  onPointerEnter: vi.fn(),
  onPointerLeave: vi.fn(),
  onFocus: vi.fn(),
  onBlur: vi.fn(),
};

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

describe("Sidebar", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.resetAllMocks();
    resetUseButlersMock();
    // Default cost mock — must be re-set after resetAllMocks
    vi.mocked(useSpendSummary).mockReturnValue({
      data: { data: { total_cost_usd: 26.27 } },
      isLoading: false,
    } as ReturnType<typeof useSpendSummary>);
    vi.mocked(usePrefetchOnIntent).mockReturnValue(dataPrefetchHandlers);
    vi.mocked(useRouteChunkPrefetchOnIntent).mockReturnValue(chunkPrefetchHandlers);
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

  // Icon-rail variant (desktop collapsed)
  function render(initialPath = "/") {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={[initialPath]}>
          <Sidebar collapsed />
        </MemoryRouter>,
      );
    });
  }

  // Desktop default variant (expanded with labels + section headers)
  function renderExpanded(initialPath = "/", onToggleCollapse?: () => void) {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={[initialPath]}>
          <Sidebar onToggleCollapse={onToggleCollapse} />
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

  describe("intent prefetch", () => {
    beforeEach(() => {
      setButlersState({
        data: {
          data: [{ name: "health", status: "ok", port: 40109, type: "butler" as const, sessions_24h: 0 }],
          meta: {},
        },
      });
    });

    it.each([
      ["desktop flat link", () => render(), "/sessions"],
      ["desktop group child", () => render("/health"), "/health"],
      ["mobile flat link", () => renderMobile(), "/spend"],
      ["mobile group child", () => renderMobile("/health"), "/health"],
    ])("composes route-chunk and data prefetch handlers for each %s", (_surface, renderSurface, path) => {
      renderSurface();

      const link = container.querySelector(`a[href="${path}"]`);
      expect(link).toBeInstanceOf(HTMLAnchorElement);
      expect(useRouteChunkPrefetchOnIntent).toHaveBeenCalledWith(path);
      expect(usePrefetchOnIntent).toHaveBeenCalledWith(path);

      fireEvent.pointerEnter(link!);
      fireEvent.pointerLeave(link!);
      fireEvent.focus(link!);
      fireEvent.blur(link!);

      for (const handler of ["onPointerEnter", "onPointerLeave", "onFocus", "onBlur"] as const) {
        expect(chunkPrefetchHandlers[handler]).toHaveBeenCalledOnce();
        expect(dataPrefetchHandlers[handler]).toHaveBeenCalledOnce();
      }
    });
  });

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
  // Desktop expanded mode (default) — labels + section headers
  // -------------------------------------------------------------------------

  describe("desktop expanded mode (default)", () => {
    beforeEach(() => {
      setButlersState({
        data: {
          data: [{ name: "relationship", status: "ok", port: 40102, type: "butler" as const, sessions_24h: 0 }],
          meta: {},
        },
      });
    });

    it("renders section headers by default (no collapsed prop)", () => {
      renderExpanded();

      expect(container.textContent).toContain("Main");
      expect(container.textContent).toContain("Dedicated Butlers");
      expect(container.textContent).toContain("Telemetry");
    });

    it("renders nav labels by default", () => {
      // Include health so the butler-gated Health nav item is visible (bu-w7b18.1: Health is now a
      // ButlerMark nav item like Education/Chronicles, filtered when its butler is absent).
      setButlersState({
        data: {
          data: [
            { name: "relationship", status: "ok", port: 40102, type: "butler" as const, sessions_24h: 0 },
            { name: "health", status: "ok", port: 40109, type: "butler" as const, sessions_24h: 0 },
          ],
          meta: {},
        },
      });
      renderExpanded();

      expect(container.textContent).toContain("Overview");
      expect(container.textContent).toContain("Calendar");
      // Health is an expandable group rather than a flat item. In the
      // expanded sidebar its visible label supplies the button's accessible
      // name, so it has no redundant aria-label attribute.
      const healthGroup = Array.from(container.querySelectorAll("button")).find((button) =>
        button.textContent?.includes("Health"),
      );
      expect(healthGroup).toBeInstanceOf(HTMLButtonElement);
    });

    it("renders Butlers brand label by default", () => {
      renderExpanded();

      const brandDiv = container.querySelector("[data-testid='sidebar-brand']");
      expect(brandDiv?.textContent).toContain("Butlers");
    });

    it("marks desktop spend as incomplete instead of rendering an all-unpriced $0.00 subtotal", () => {
      vi.mocked(useSpendSummary).mockReturnValue({
        data: {
          data: {
            total_cost_usd: 0,
            unpriced_models: [
              {
                model: "unknown-executed-model",
                calls: 1,
                input_tokens: 500,
                output_tokens: 100,
                cached_input_tokens: 0,
                cache_creation_tokens: 0,
              },
            ],
          },
        },
        isLoading: false,
      } as ReturnType<typeof useSpendSummary>);

      renderExpanded();

      expect(container.textContent).toContain("Spend coverage incomplete");
      expect(container.textContent).not.toContain("$0.00 today");
      expect(container.textContent).not.toContain("All systems ok");
    });

    it("renders a collapse toggle that invokes onToggleCollapse", () => {
      const onToggle = vi.fn();
      renderExpanded("/", onToggle);

      const toggleButton = Array.from(container.querySelectorAll("button")).find(
        (btn) => btn.getAttribute("aria-label") === "Collapse sidebar",
      );
      expect(toggleButton).toBeTruthy();

      act(() => {
        toggleButton!.click();
      });
      expect(onToggle).toHaveBeenCalledTimes(1);
    });

    it("does not render a collapse toggle without onToggleCollapse", () => {
      renderExpanded();

      const toggleButton = Array.from(container.querySelectorAll("button")).find(
        (btn) => btn.getAttribute("aria-label") === "Collapse sidebar",
      );
      expect(toggleButton).toBeUndefined();
    });

    it("rail mode renders an expand toggle when onToggleCollapse is provided", () => {
      const onToggle = vi.fn();
      act(() => {
        root.render(
          <MemoryRouter initialEntries={["/"]}>
            <Sidebar collapsed onToggleCollapse={onToggle} />
          </MemoryRouter>,
        );
      });

      const toggleButton = Array.from(container.querySelectorAll("button")).find(
        (btn) => btn.getAttribute("aria-label") === "Expand sidebar",
      );
      expect(toggleButton).toBeTruthy();

      act(() => {
        toggleButton!.click();
      });
      expect(onToggle).toHaveBeenCalledTimes(1);
    });

    it("shows footer status text in expanded mode", () => {
      renderExpanded();

      const footerEl = container.querySelector('[aria-label="$26.27 today"]');
      expect(footerEl).toBeTruthy();
      expect(footerEl?.textContent).toContain("$26.27 today");
      expect(footerEl?.getAttribute("title")).toBeNull();
    });

    it("names a summary source_error instead of showing its compatibility $0.00 as calm spend", () => {
      vi.mocked(useSpendSummary).mockReturnValue({
        data: { data: { total_cost_usd: 0, source_error: true } },
        isLoading: false,
      } as ReturnType<typeof useSpendSummary>);

      renderExpanded();

      const footerEl = container.querySelector('[aria-label="Spend source unavailable"]');
      expect(footerEl).toBeTruthy();
      expect(footerEl?.textContent).toContain("Spend source unavailable");
      expect(footerEl?.textContent).not.toContain("$0.00 today");
    });

    it("auto-expands Health into its overview and six ledger destinations for a direct sub-page visit", () => {
      setButlersState({
        data: {
          data: [{ name: "health", status: "ok", port: 40109, type: "butler" as const, sessions_24h: 0 }],
          meta: {},
        },
      });
      renderExpanded("/health/measurements");

      const healthGroup = Array.from(container.querySelectorAll("button")).find((button) =>
        button.textContent?.includes("Health"),
      );
      expect(healthGroup).toBeInstanceOf(HTMLButtonElement);
      expect(healthGroup?.getAttribute("aria-expanded")).toBe("true");

      for (const path of [
        "/health",
        "/health/measurements",
        "/health/medications",
        "/health/conditions",
        "/health/symptoms",
        "/health/meals",
        "/health/research",
      ]) {
        expect(container.querySelector(`a[href="${path}"]`)).toBeInstanceOf(HTMLAnchorElement);
      }
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
      data: { data: [{ name: "relationship", status: "ok", port: 40102, type: "butler" as const, sessions_24h: 0 }], meta: {} },
    });
    render();

    const calendarLink = container.querySelector('a[href="/calendar"]');
    expect(calendarLink).toBeInstanceOf(HTMLAnchorElement);
  });

  it("includes navigation link to Ingestion", () => {
    setButlersState({
      data: { data: [{ name: "relationship", status: "ok", port: 40102, type: "butler" as const, sessions_24h: 0 }], meta: {} },
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
          data: [{ name: "relationship", status: "ok", port: 40102, type: "butler" as const, sessions_24h: 0 }],
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
      // Include health so the butler-gated Health nav item is visible (bu-w7b18.1: Health is now a
      // ButlerMark nav item like Education/Chronicles, filtered when its butler is absent).
      setButlersState({
        data: {
          data: [
            { name: "relationship", status: "ok", port: 40102, type: "butler" as const, sessions_24h: 0 },
            { name: "health", status: "ok", port: 40109, type: "butler" as const, sessions_24h: 0 },
          ],
          meta: {},
        },
      });
      renderMobile();

      expect(container.textContent).toContain("Overview");
      expect(container.textContent).toContain("Calendar");
      // Health renders as a ButlerMark icon (not a text label) — check for the link, not text content.
      expect(container.querySelector('a[href="/health"]')).toBeInstanceOf(HTMLAnchorElement);
    });

    it("shows today's spend in mobile expanded footer", () => {
      renderMobile();

      expect(container.textContent).toContain("$26.27");
    });

    it("hides a mixed subtotal and names unpriced coverage in the mobile footer", () => {
      vi.mocked(useSpendSummary).mockReturnValue({
        data: {
          data: {
            total_cost_usd: 26.27,
            unpriced_models: [
              {
                model: "unknown-executed-model",
                calls: 1,
                input_tokens: 500,
                output_tokens: 100,
                cached_input_tokens: 0,
                cache_creation_tokens: 0,
              },
            ],
          },
        },
        isLoading: false,
      } as ReturnType<typeof useSpendSummary>);

      renderMobile();

      expect(container.textContent).not.toContain("$26.27");
      expect(container.textContent).toContain("Spend coverage incomplete");
      expect(container.textContent).toContain("unknown-executed-model");
    });
  });

  // -------------------------------------------------------------------------
  // Tooltip: rail items have aria-label matching label
  // -------------------------------------------------------------------------

  describe("tooltip / aria-label on rail items", () => {
    beforeEach(() => {
      setButlersState({
        data: {
          data: [{ name: "chronicler", status: "ok", port: 40110, type: "butler" as const, sessions_24h: 0 }],
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
  // Active state: NavLink sets aria-current="page" on the active anchor.
  // This is the stable identity token — className function resolution is
  // env-dependent and must not be used as the assertion target.
  // -------------------------------------------------------------------------

  it("active item link is present when its route matches", () => {
    setButlersState({
      data: { data: [], meta: {} },
    });
    render("/");

    // The Overview link should be in the DOM when on route "/"
    const overviewLink = container.querySelector('a[href="/"]');
    expect(overviewLink).toBeInstanceOf(HTMLAnchorElement);
    // NavLink sets aria-current="page" on the active link — this is the stable
    // identity token for active state (className function resolution is env-dependent).
    expect(overviewLink?.getAttribute("aria-current")).toBe("page");
    // An inactive link (sessions) must not have aria-current
    const sessionsLink = container.querySelector('a[href="/sessions"]');
    expect(sessionsLink?.getAttribute("aria-current")).toBeNull();
  });

  // -------------------------------------------------------------------------
  // Status dots on butler-associated items
  // -------------------------------------------------------------------------

  describe("status dots", () => {
    it("renders a status dot on a degraded butler's nav item", () => {
      setButlersState({
        data: {
          data: [
            { name: "education", status: "degraded", port: 40103, type: "butler" as const, sessions_24h: 0 },
          ],
          meta: {},
        },
      });
      render();

      // The Education nav item should contain an amber status dot
      const educationLink = container.querySelector('a[href="/education"]');
      expect(educationLink).toBeInstanceOf(HTMLAnchorElement);
      expect(educationLink!.querySelector('[class*="bg-[var(--amber)]"]')).toBeTruthy();
    });

    it("renders a status dot on an error butler's nav item", () => {
      setButlersState({
        data: {
          data: [
            { name: "education", status: "error", port: 40103, type: "butler" as const, sessions_24h: 0 },
          ],
          meta: {},
        },
      });
      render();

      const educationLink = container.querySelector('a[href="/education"]');
      expect(educationLink).toBeInstanceOf(HTMLAnchorElement);
      expect(educationLink!.querySelector(".bg-destructive")).toBeTruthy();
    });

    it("does not render a status dot when butler status is ok", () => {
      setButlersState({
        data: {
          data: [
            { name: "education", status: "ok", port: 40103, type: "butler" as const, sessions_24h: 0 },
          ],
          meta: {},
        },
      });
      render();

      const educationLink = container.querySelector('a[href="/education"]');
      expect(educationLink).toBeInstanceOf(HTMLAnchorElement);
      expect(educationLink!.querySelector('[class*="bg-[var(--amber)]"]')).toBeNull();
      expect(educationLink!.querySelector(".bg-destructive")).toBeNull();
    });
  });

  // -------------------------------------------------------------------------
  // Footer summary dot
  // -------------------------------------------------------------------------

  describe("footer status dot", () => {
    it("shows green dot when all butlers are ok", () => {
      setButlersState({
        data: {
          data: [{ name: "relationship", status: "ok", port: 40102, type: "butler" as const, sessions_24h: 0 }],
          meta: {},
        },
      });
      render();

      // The footer should contain a green dot
      const greenDot = container.querySelector('[class*="bg-[var(--green)]"]');
      expect(greenDot).toBeTruthy();
    });

    it("shows amber dot when any butler is degraded", () => {
      setButlersState({
        data: {
          data: [{ name: "relationship", status: "degraded", port: 40102, type: "butler" as const, sessions_24h: 0 }],
          meta: {},
        },
      });
      render();

      // Footer dot is distinct from the status dot on nav items
      // At least one amber dot renders somewhere (footer or item)
      const amberDots = container.querySelectorAll('[class*="bg-[var(--amber)]"]');
      expect(amberDots.length).toBeGreaterThan(0);
    });

    it("footer aria-label contains the status summary without a duplicate title", () => {
      setButlersState({
        data: {
          data: [{ name: "relationship", status: "degraded", port: 40102, type: "butler" as const, sessions_24h: 0 }],
          meta: {},
        },
      });
      render();

      const footerEl = Array.from(container.querySelectorAll("[aria-label]")).find((el) => {
        const label = el.getAttribute("aria-label") ?? "";
        return label.includes("degraded") || label.includes("ok") || label.includes("error");
      });
      expect(footerEl).toBeTruthy();
      expect(footerEl?.getAttribute("title")).toBe(footerEl?.getAttribute("aria-label"));
    });

    it("shows neutral dot and loading aria-label while butlers query is loading", () => {
      setButlersState({ isLoading: true });
      render();

      // No green, amber, or red dot when state is unknown
      expect(container.querySelector('[class*="bg-[var(--green)]"]')).toBeNull();
      expect(container.querySelector('[class*="bg-[var(--amber)]"]')).toBeNull();
      expect(container.querySelector(".bg-destructive")).toBeNull();

      // Neutral dot is present
      const neutralDot = container.querySelector(".bg-muted-foreground\\/40");
      expect(neutralDot).toBeTruthy();

      const footerEl = container.querySelector('[aria-label="Loading butlers"]');
      expect(footerEl).toBeTruthy();
      expect(footerEl?.getAttribute("title")).toBe("Loading butlers");
    });

    it("shows neutral dot and error aria-label when butlers query fails", () => {
      setButlersState({ isError: true, error: new Error("network error") });
      render();

      // No green, amber, or red dot when state is unknown
      expect(container.querySelector('[class*="bg-[var(--green)]"]')).toBeNull();
      expect(container.querySelector('[class*="bg-[var(--amber)]"]')).toBeNull();
      expect(container.querySelector(".bg-destructive")).toBeNull();

      // Neutral dot is present
      const neutralDot = container.querySelector(".bg-muted-foreground\\/40");
      expect(neutralDot).toBeTruthy();

      const footerEl = container.querySelector('[aria-label="Butlers query failed"]');
      expect(footerEl).toBeTruthy();
      expect(footerEl?.getAttribute("title")).toBe("Butlers query failed");
    });

    it("shows red dot when any butler has error status (success path)", () => {
      setButlersState({
        data: {
          data: [{ name: "relationship", status: "error", port: 40102, type: "butler" as const, sessions_24h: 0 }],
          meta: {},
        },
      });
      render();

      // The collapsed footer keeps its status summary available on hover.
      const footerEl = Array.from(container.querySelectorAll("[aria-label]")).find((el) => {
        const label = el.getAttribute("aria-label") ?? "";
        return label.includes("error");
      });
      expect(footerEl).toBeTruthy();
      expect(footerEl?.getAttribute("title")).toBe(footerEl?.getAttribute("aria-label"));
    });
  });

  // -------------------------------------------------------------------------
  // Butler-aware nav filtering
  // -------------------------------------------------------------------------

  describe("butler-aware filtering", () => {
    it("hides a butler-gated item when its butler is absent", () => {
      setButlersState({
        data: {
          data: [{ name: "general", status: "ok", port: 40101, type: "butler" as const, sessions_24h: 0 }],
          meta: {},
        },
      });
      render();

      expect(container.querySelector('a[href="/education"]')).toBeNull();
    });

    it("shows a butler-gated item when its butler is present", () => {
      setButlersState({
        data: {
          data: [{ name: "education", status: "ok", port: 40103, type: "butler" as const, sessions_24h: 0 }],
          meta: {},
        },
      });
      render();

      expect(container.querySelector('a[href="/education"]')).toBeInstanceOf(HTMLAnchorElement);
    });

    it("shows all items while butlers are loading", () => {
      setButlersState({ isLoading: true });
      render();

      expect(container.querySelector('a[href="/education"]')).toBeInstanceOf(HTMLAnchorElement);
    });

    it("shows all items when butlers query fails", () => {
      setButlersState({ isError: true, error: new Error("network error") });
      render();

      expect(container.querySelector('a[href="/education"]')).toBeInstanceOf(HTMLAnchorElement);
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
          data: [{ name: "chronicler", status: "ok", port: 40110, type: "butler" as const, sessions_24h: 0 }],
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
          data: [{ name: "general", status: "ok", port: 40101, type: "butler" as const, sessions_24h: 0 }],
          meta: {},
        },
      });
      render();

      expect(container.querySelector('a[href="/chronicles"]')).toBeNull();
    });

    it("uses disambiguation tooltip as aria-label on Chronicles link", () => {
      setButlersState({
        data: {
          data: [{ name: "chronicler", status: "ok", port: 40110, type: "butler" as const, sessions_24h: 0 }],
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
  // QA badge: red variant
  // -------------------------------------------------------------------------

  describe("QA badge variant", () => {
    beforeEach(() => {
      setButlersState({
        data: {
          data: [{ name: "qa", status: "ok", port: 40120, type: "butler" as const, sessions_24h: 0 }],
          meta: {},
        },
      });
    });

    it("renders var(--red) on QA badge when count > 0 (rail mode)", () => {
      vi.mocked(useBadgeCounts).mockReturnValue({ "qa-escalations": 3 });
      render();

      const redBadge = container.querySelector('[class*="bg-[var(--red)]"]');
      expect(redBadge).toBeTruthy();
    });

    it("renders var(--red) on QA badge when count > 0 (mobile expanded mode)", () => {
      vi.mocked(useBadgeCounts).mockReturnValue({ "qa-escalations": 5 });
      renderMobile();

      const redBadge = container.querySelector('[class*="bg-[var(--red)]"]');
      expect(redBadge).toBeTruthy();
    });

    it("does not render a badge when qa-escalations count is zero", () => {
      vi.mocked(useBadgeCounts).mockReturnValue({ "qa-escalations": 0 });
      render();

      const redBadge = container.querySelector('[class*="bg-[var(--red)]"]');
      expect(redBadge).toBeNull();
    });
  });

  describe("Decisions badge availability", () => {
    beforeEach(() => {
      setButlersState({
        data: { data: [], meta: {} },
      });
    });

    it.each([
      ["rail", () => render()],
      ["expanded desktop", () => renderExpanded()],
      ["mobile", () => renderMobile()],
    ])("keeps an available empty Decisions digest quiet in %s", (_variant, renderSidebar) => {
      vi.mocked(useBadgeCounts).mockReturnValue({
        "decisions-open": { kind: "count", count: 0 },
      });

      renderSidebar();

      const decisionsLink = container.querySelector('a[href="/decisions"]');
      expect(decisionsLink).toBeTruthy();
      expect(decisionsLink?.querySelector('[aria-label="Decisions digest unavailable"]')).toBeNull();
      expect(decisionsLink?.textContent).not.toContain("0");
    });

    it.each([
      ["rail", () => render()],
      ["expanded desktop", () => renderExpanded()],
      ["mobile", () => renderMobile()],
    ])("renders a numeric Decisions count when available in %s", (_variant, renderSidebar) => {
      vi.mocked(useBadgeCounts).mockReturnValue({
        "decisions-open": { kind: "count", count: 2 },
      });

      renderSidebar();

      const decisionsLink = container.querySelector('a[href="/decisions"]');
      expect(decisionsLink).toBeTruthy();
      expect(decisionsLink?.textContent).toContain("2");
      expect(decisionsLink?.querySelector('[aria-label="Decisions digest unavailable"]')).toBeNull();
    });

    it.each([
      ["rail", () => render()],
      ["expanded desktop", () => renderExpanded()],
      ["mobile", () => renderMobile()],
    ])("renders an accessible unavailable Decisions marker in %s", (_variant, renderSidebar) => {
      vi.mocked(useBadgeCounts).mockReturnValue({
        "decisions-open": { kind: "unavailable" },
      });

      renderSidebar();

      const decisionsLink = container.querySelector('a[href="/decisions"]');
      expect(decisionsLink).toBeTruthy();
      expect(decisionsLink?.querySelector('[aria-label="Decisions digest unavailable"]')).toBeTruthy();
      expect(decisionsLink?.textContent).not.toContain("0");
    });
  });

  // -------------------------------------------------------------------------
  // Relationships nav removal
  // -------------------------------------------------------------------------

  describe("Relationships nav removal", () => {
    beforeEach(() => {
      setButlersState({
        data: {
          data: [{ name: "relationship", status: "ok", port: 40102, type: "butler" as const, sessions_24h: 0 }],
          meta: {},
        },
      });
    });

    it("does not render a Groups/Circles nav link even when relationship butler is present", () => {
      render("/entities/circles");

      // Groups was retired (bu-86c4c.19) into the "Circles" lens on /entities,
      // reachable via that page's own SubpageTabs strip — not surfaced as a
      // top-level sidebar link or a "Relationships" group header.
      expect(container.querySelector('a[href="/entities/circles"]')).toBeNull();
      expect(container.querySelector('a[href="/groups"]')).toBeNull();
      const groupHeader = Array.from(container.querySelectorAll("button")).find(
        (btn) => btn.getAttribute("aria-label") === "Relationships",
      );
      expect(groupHeader).toBeUndefined();
    });

    it("does not render a Groups/Circles nav link in mobile mode", () => {
      renderMobile("/entities/circles");

      expect(container.querySelector('a[href="/entities/circles"]')).toBeNull();
      expect(container.querySelector('a[href="/groups"]')).toBeNull();
    });
  });
});
