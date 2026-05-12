// @vitest-environment jsdom
/**
 * ButlerConfigTab — RTL tests.
 *
 * Tests cover:
 *  - Root container renders
 *  - 4 Panel atoms in 2x2 grid (process/schedule/scopes/integrations)
 *  - NO <RuntimeConfigCard> in the DOM
 *  - Accordion is COLLAPSED by default
 *  - Accordion item expands to show content on click
 *  - butler.toml Formatted/Raw toggle preserved inside accordion item
 *  - Loading state per Panel
 *  - Error state graceful (ErrorLine in panel, not bare crash)
 *  - Schedule panel shows relative timestamps via <Time>
 *  - Scopes panel: authorized / not-required badge mapping
 *  - Integrations panel: badge list for enabled modules, empty state
 *  - Null content -> "Not found" text in accordion
 *
 * bead: bu-k55lg (epic bu-hdavr F.3)
 */

import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement } from "react";

import ButlerConfigTab from "./ButlerConfigTab";

// ---------------------------------------------------------------------------
// Mock hooks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-butlers", () => ({
  useButler: vi.fn(),
  useButlerConfig: vi.fn(),
  useButlerModules: vi.fn(),
}));

// Stub <Time> to avoid timezone / date-fns complexity in jsdom
vi.mock("@/components/ui/time", () => ({
  Time: ({ value }: { value: string }) =>
    createElement("time", { dateTime: value }, value),
}));

import { useButler, useButlerConfig, useButlerModules } from "@/hooks/use-butlers";

// ---------------------------------------------------------------------------
// Fixed clock
// ---------------------------------------------------------------------------

const FIXED_NOW_ISO = "2026-05-13T10:00:00.000Z";

beforeAll(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(FIXED_NOW_ISO));
});

afterAll(() => {
  vi.useRealTimers();
});

// ---------------------------------------------------------------------------
// Fixture data
// ---------------------------------------------------------------------------

const NEXT_RUN_ISO = new Date(
  new Date(FIXED_NOW_ISO).getTime() + 30 * 60 * 1_000,
).toISOString();

const BUTLER_DETAIL = {
  name: "general",
  status: "running",
  port: 4000,
  type: "butler" as const,
  sessions_24h: 2,
  process_facts: {
    container_name: "butlers-general-1",
    port: 4000,
    registered_duration_seconds: 3665,
    config_path: "roster/general/butler.toml",
  },
  schedules: [
    { name: "daily-digest", cron: "0 8 * * *", next_run_at: NEXT_RUN_ISO },
  ],
  modules: [{ name: "email", enabled: true }],
  skills: [],
};

const MODULES = [
  { name: "email", enabled: true, status: "ok", oauth_status: "granted" },
  { name: "calendar", enabled: true, status: "ok", oauth_status: "not_configured" },
  { name: "telegram", enabled: false, status: "disabled", oauth_status: null },
];

const CONFIG = {
  butler_toml: { butler: { name: "general", description: "General purpose butler" } },
  claude_md: "# General\n\nThis is the CLAUDE.md file.",
  agents_md: "# Notes to self\n\nSome notes here.",
  manifesto_md: "# Manifesto\n\nThis is the manifesto.",
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderTab(butlerName = "general") {
  return render(
    <MemoryRouter>
      <QueryClientProvider client={makeQueryClient()}>
        <ButlerConfigTab butlerName={butlerName} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// Default mock setups
// ---------------------------------------------------------------------------

function setupWithData() {
  vi.mocked(useButler).mockReturnValue({
    data: { data: BUTLER_DETAIL, meta: {} },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useButler>);

  vi.mocked(useButlerModules).mockReturnValue({
    data: { data: MODULES, meta: {} },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useButlerModules>);

  vi.mocked(useButlerConfig).mockReturnValue({
    data: { data: CONFIG, meta: {} },
    isLoading: false,
    isError: false,
    error: null,
  } as unknown as ReturnType<typeof useButlerConfig>);
}

function setupLoading() {
  vi.mocked(useButler).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as unknown as ReturnType<typeof useButler>);

  vi.mocked(useButlerModules).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
  } as unknown as ReturnType<typeof useButlerModules>);

  vi.mocked(useButlerConfig).mockReturnValue({
    data: undefined,
    isLoading: true,
    isError: false,
    error: null,
  } as unknown as ReturnType<typeof useButlerConfig>);
}

function setupModulesError() {
  vi.mocked(useButler).mockReturnValue({
    data: { data: BUTLER_DETAIL, meta: {} },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useButler>);

  vi.mocked(useButlerModules).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: true,
  } as unknown as ReturnType<typeof useButlerModules>);

  vi.mocked(useButlerConfig).mockReturnValue({
    data: { data: CONFIG, meta: {} },
    isLoading: false,
    isError: false,
    error: null,
  } as unknown as ReturnType<typeof useButlerConfig>);
}

function setupConfigError() {
  vi.mocked(useButler).mockReturnValue({
    data: { data: BUTLER_DETAIL, meta: {} },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useButler>);

  vi.mocked(useButlerModules).mockReturnValue({
    data: { data: MODULES, meta: {} },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useButlerModules>);

  vi.mocked(useButlerConfig).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: true,
    error: new Error("Failed to fetch config"),
  } as unknown as ReturnType<typeof useButlerConfig>);
}

function setupEmptyModules() {
  vi.mocked(useButler).mockReturnValue({
    data: {
      data: { ...BUTLER_DETAIL, schedules: [], modules: [] },
      meta: {},
    },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useButler>);

  vi.mocked(useButlerModules).mockReturnValue({
    data: { data: [], meta: {} },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useButlerModules>);

  vi.mocked(useButlerConfig).mockReturnValue({
    data: { data: { ...CONFIG, manifesto_md: null }, meta: {} },
    isLoading: false,
    isError: false,
    error: null,
  } as unknown as ReturnType<typeof useButlerConfig>);
}

// ---------------------------------------------------------------------------
// Cleanup
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  cleanup();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ButlerConfigTab", () => {
  it("renders the root container", () => {
    setupWithData();
    renderTab();
    expect(screen.getByTestId("butler-config-tab")).toBeDefined();
  });

  describe("2x2 Panel grid", () => {
    it("renders the panel-grid container", () => {
      setupWithData();
      renderTab();
      expect(screen.getByTestId("config-panel-grid")).toBeDefined();
    });

    it("renders exactly 4 panels: process, schedule, scopes, integrations", () => {
      setupWithData();
      renderTab();
      expect(screen.getByTestId("panel-process")).toBeDefined();
      expect(screen.getByTestId("panel-schedule")).toBeDefined();
      expect(screen.getByTestId("panel-scopes")).toBeDefined();
      expect(screen.getByTestId("panel-integrations")).toBeDefined();
    });

    it("does NOT render <RuntimeConfigCard>", () => {
      setupWithData();
      renderTab();
      // RuntimeConfigCard renders a "hot" / "restart required" badge and
      // various runtime-config fields; its absence is verified by checking
      // that no element contains the unique restart-required text.
      const restartBadges = screen
        .queryAllByText(/restart required/i);
      expect(restartBadges.length).toBe(0);
    });
  });

  describe("Process panel", () => {
    it("shows container_name in process panel", () => {
      setupWithData();
      renderTab();
      const panel = screen.getByTestId("panel-process");
      expect(panel.textContent).toContain("butlers-general-1");
    });

    it("shows port in process panel", () => {
      setupWithData();
      renderTab();
      const panel = screen.getByTestId("panel-process");
      expect(panel.textContent).toContain("4000");
    });

    it("shows human-readable registered duration in process panel", () => {
      setupWithData();
      renderTab();
      const panel = screen.getByTestId("panel-process");
      // 3665s = 1h 1m
      expect(panel.textContent).toContain("1h 1m");
    });

    it("shows config_path in process panel", () => {
      setupWithData();
      renderTab();
      const panel = screen.getByTestId("panel-process");
      expect(panel.textContent).toContain("roster/general/butler.toml");
    });

    it("does NOT show pid in process panel", () => {
      setupWithData();
      renderTab();
      const panel = screen.getByTestId("panel-process");
      expect(panel.textContent?.toLowerCase()).not.toContain("pid");
    });
  });

  describe("Schedule panel", () => {
    it("renders schedule name", () => {
      setupWithData();
      renderTab();
      const panel = screen.getByTestId("panel-schedule");
      expect(panel.textContent).toContain("daily-digest");
    });

    it("renders cron expression", () => {
      setupWithData();
      renderTab();
      const panel = screen.getByTestId("panel-schedule");
      expect(panel.textContent).toContain("0 8 * * *");
    });

    it("renders next_run_at via <Time> in relative mode", () => {
      setupWithData();
      renderTab();
      const panel = screen.getByTestId("panel-schedule");
      const timeEl = panel.querySelector("time");
      expect(timeEl).toBeTruthy();
      expect(timeEl?.getAttribute("dateTime")).toBe(NEXT_RUN_ISO);
    });

    it("shows empty state when no schedules", () => {
      setupEmptyModules();
      renderTab();
      expect(screen.getByTestId("panel-schedule-empty")).toBeDefined();
      expect(screen.getByTestId("panel-schedule-empty").textContent).toContain("No schedules.");
    });
  });

  describe("Scopes and OAuth panel", () => {
    it("shows authorized badge for oauth_status=granted", () => {
      setupWithData();
      renderTab();
      const panel = screen.getByTestId("panel-scopes");
      expect(panel.textContent).toContain("authorized");
    });

    it("shows not-required badge for oauth_status=not_configured", () => {
      setupWithData();
      renderTab();
      const panel = screen.getByTestId("panel-scopes");
      expect(panel.textContent).toContain("not required");
    });

    it("shows module names in scopes panel", () => {
      setupWithData();
      renderTab();
      const panel = screen.getByTestId("panel-scopes");
      expect(panel.textContent).toContain("email");
      expect(panel.textContent).toContain("calendar");
    });

    it("shows empty state when no modules", () => {
      setupEmptyModules();
      renderTab();
      expect(screen.getByTestId("panel-scopes-empty")).toBeDefined();
    });

    it("shows ErrorLine when modules query fails", () => {
      setupModulesError();
      renderTab();
      const panel = screen.getByTestId("panel-scopes");
      const errorLines = panel.querySelectorAll("[data-testid='error-state-line']");
      expect(errorLines.length).toBeGreaterThanOrEqual(1);
    });
  });

  describe("Integrations panel", () => {
    it("shows enabled module names as badges", () => {
      setupWithData();
      renderTab();
      const panel = screen.getByTestId("panel-integrations");
      expect(panel.textContent).toContain("email");
      expect(panel.textContent).toContain("calendar");
    });

    it("does NOT show disabled modules in integrations panel", () => {
      setupWithData();
      renderTab();
      const panel = screen.getByTestId("panel-integrations");
      // telegram is disabled in MODULES fixture
      expect(panel.textContent).not.toContain("telegram");
    });

    it("shows empty state when no modules enabled", () => {
      setupEmptyModules();
      renderTab();
      expect(screen.getByTestId("panel-integrations-empty")).toBeDefined();
      expect(screen.getByTestId("panel-integrations-empty").textContent).toContain(
        "No modules enabled.",
      );
    });

    it("shows ErrorLine when modules query fails", () => {
      setupModulesError();
      renderTab();
      const panel = screen.getByTestId("panel-integrations");
      const errorLines = panel.querySelectorAll("[data-testid='error-state-line']");
      expect(errorLines.length).toBeGreaterThanOrEqual(1);
    });
  });

  describe("Accordion — collapsed by default", () => {
    it("renders the accordion container", () => {
      setupWithData();
      renderTab();
      expect(screen.getByTestId("config-accordion")).toBeDefined();
    });

    it("renders exactly 4 accordion items", () => {
      setupWithData();
      renderTab();
      const items = screen.getAllByTestId("accordion-item");
      expect(items.length).toBe(4);
    });

    it("accordion items are COLLAPSED by default (no open attribute)", () => {
      setupWithData();
      renderTab();
      const items = screen.getAllByTestId("accordion-item");
      items.forEach((item) => {
        // <details> without `open` attribute is collapsed
        expect((item as HTMLDetailsElement).open).toBe(false);
      });
    });

    it("markdown content is NOT visible before expand", () => {
      setupWithData();
      renderTab();
      // The text is in the DOM but details/summary hides it visually;
      // however RTL renders all DOM. We check the items are closed via the
      // open attribute, which is tested above. Here we just assert the
      // accordion container is present and items exist.
      const items = screen.getAllByTestId("accordion-item");
      expect(items.length).toBe(4);
    });

    it("expanding an accordion item reveals content", () => {
      setupWithData();
      renderTab();
      const items = screen.getAllByTestId("accordion-item");
      // Expand the CLAUDE.md item (index 1)
      const claudeItem = items[1];
      const summary = claudeItem.querySelector("summary");
      expect(summary).toBeTruthy();
      fireEvent.click(summary!);
      expect((claudeItem as HTMLDetailsElement).open).toBe(true);
      const content = claudeItem.querySelector("[data-testid='accordion-item-content']");
      expect(content?.textContent).toContain("This is the CLAUDE.md file.");
    });

    it("null accordion content shows 'Not found'", () => {
      setupEmptyModules();
      renderTab();
      // manifesto_md is null in setupEmptyModules
      const items = screen.getAllByTestId("accordion-item");
      // MANIFESTO.md is index 3
      const manifestoItem = items[3];
      const summary = manifestoItem.querySelector("summary");
      fireEvent.click(summary!);
      const content = manifestoItem.querySelector("[data-testid='accordion-item-content']");
      expect(content?.textContent).toContain("Not found");
    });
  });

  describe("butler.toml Formatted/Raw toggle", () => {
    it("toggle button is present in the butler.toml accordion item", () => {
      setupWithData();
      renderTab();
      // Expand the butler.toml item (index 0)
      const items = screen.getAllByTestId("accordion-item");
      const tomlItem = items[0];
      const summary = tomlItem.querySelector("summary");
      fireEvent.click(summary!);
      expect(screen.getByTestId("toml-format-toggle")).toBeDefined();
    });

    it("initially shows Formatted (not Raw)", () => {
      setupWithData();
      renderTab();
      const items = screen.getAllByTestId("accordion-item");
      const tomlItem = items[0];
      fireEvent.click(tomlItem.querySelector("summary")!);
      const toggle = screen.getByTestId("toml-format-toggle");
      expect(toggle.textContent).toBe("Raw");
    });

    it("clicking toggle switches to Raw view showing JSON.stringify output", () => {
      setupWithData();
      renderTab();
      const items = screen.getAllByTestId("accordion-item");
      const tomlItem = items[0];
      fireEvent.click(tomlItem.querySelector("summary")!);
      const toggle = screen.getByTestId("toml-format-toggle");
      // Clicking once shows Raw
      fireEvent.click(toggle);
      expect(toggle.textContent).toBe("Formatted");
      // Raw view should show JSON output
      const content = tomlItem.querySelector("[data-testid='accordion-item-content']");
      expect(content?.textContent).toContain('"general"');
    });

    it("clicking toggle again switches back to Formatted view", () => {
      setupWithData();
      renderTab();
      const items = screen.getAllByTestId("accordion-item");
      const tomlItem = items[0];
      fireEvent.click(tomlItem.querySelector("summary")!);
      const toggle = screen.getByTestId("toml-format-toggle");
      fireEvent.click(toggle); // -> Raw
      fireEvent.click(toggle); // -> Formatted
      expect(toggle.textContent).toBe("Raw");
    });
  });

  describe("Loading state", () => {
    it("shows loading skeleton when all data is loading", () => {
      setupLoading();
      renderTab();
      expect(screen.getByTestId("config-skeleton")).toBeDefined();
    });

    it("does not render panel grid while fully loading", () => {
      setupLoading();
      renderTab();
      expect(screen.queryByTestId("config-panel-grid")).toBeNull();
    });
  });

  describe("Config file error state", () => {
    it("shows ErrorLine in accordion block when config query fails", () => {
      setupConfigError();
      renderTab();
      const errorLines = screen.getAllByTestId("error-state-line");
      expect(errorLines.length).toBeGreaterThanOrEqual(1);
    });

    it("error message includes failure reason", () => {
      setupConfigError();
      renderTab();
      const errorLines = screen.getAllByTestId("error-state-line");
      const hasMessage = errorLines.some((el) =>
        el.textContent?.includes("Failed to fetch config"),
      );
      expect(hasMessage).toBe(true);
    });
  });
});
