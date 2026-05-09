import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter, useSearchParams } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import ButlerDetailPage, {
  BASE_TABS_OPERATOR,
  BASE_TABS_RESIDENT,
  OPERATOR_EXTENSION_TABS,
  getAllTabs,
  isValidTab,
} from "@/pages/ButlerDetailPage";
import { useButler } from "@/hooks/use-butlers";
import type { ButlerSummary } from "@/api/types";

// Mock react-router's useParams so we can control the butler name
vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return {
    ...actual,
    useParams: vi.fn(() => ({ name: "general" })),
    useSearchParams: vi.fn(() => [new URLSearchParams(), vi.fn()]),
  };
});

vi.mock("@/hooks/use-butlers", () => ({
  useButler: vi.fn(),
  useButlers: vi.fn(() => ({ data: { data: [] }, isLoading: false })),
  useButlerConfig: vi.fn(() => ({ data: null, isLoading: false })),
  useButlerSkills: vi.fn(() => ({ data: null, isLoading: false })),
  useRuntimeConfig: vi.fn(() => ({ data: null, isLoading: false })),
  usePatchRuntimeConfig: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}));

vi.mock("@/hooks/use-sessions", () => ({
  useButlerSessions: vi.fn(() => ({ data: null, isLoading: false })),
  useSessionDetail: vi.fn(() => ({ data: null, isLoading: false })),
}));

vi.mock("@/hooks/use-contacts", () => ({
  useUpcomingDates: vi.fn(() => ({ data: [], isLoading: false })),
}));

vi.mock("@/hooks/use-system", () => ({
  useButlerHeartbeats: vi.fn(() => ({ data: null, isLoading: false, error: null })),
}));

vi.mock("@/hooks/use-costs", () => ({
  useCostSummary: vi.fn(() => ({ data: null, isLoading: false })),
}));

vi.mock("@/hooks/use-notifications", () => ({
  useButlerNotifications: vi.fn(() => ({ data: null, isLoading: false })),
}));

vi.mock("@/hooks/use-general", () => ({
  useRegistry: vi.fn(() => ({ data: null, isLoading: false })),
  useSetEligibility: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}));

vi.mock("@/components/chat/ChatPanel", () => ({
  ChatPanel: ({ butlerName }: { butlerName: string }) => (
    <div data-testid="chat-panel">{butlerName}</div>
  ),
}));

// Mock triggerButler so force-run button does not fire real HTTP requests.
// Spread real module exports so other symbols imported from @/api/index.ts
// remain available and do not resolve to undefined in components under test.
vi.mock("@/api/index.ts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/index.ts")>();
  return {
    ...actual,
    triggerButler: vi.fn(() => Promise.resolve({ success: true, session_id: null, output: "" })),
  };
});

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

// ---------------------------------------------------------------------------
// localStorage mock
// ---------------------------------------------------------------------------
// renderToStaticMarkup runs in Node (no real DOM), so we need to shim
// localStorage. The readPersistedMode() helper catches access errors and falls
// back to "resident", but having a controllable mock lets us assert persistence
// paths explicitly.

const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: vi.fn((key: string) => store[key] ?? null),
    setItem: vi.fn((key: string, value: string) => { store[key] = value; }),
    removeItem: vi.fn((key: string) => { delete store[key]; }),
    clear: vi.fn(() => { store = {}; }),
  };
})();

Object.defineProperty(globalThis, "localStorage", {
  value: localStorageMock,
  writable: true,
});

type UseButlerResult = ReturnType<typeof useButler>;

const BASE_BUTLER: ButlerSummary = {
  name: "general",
  status: "ok",
  port: 8001,
  type: "butler",
  sessions_24h: 0,
};

function setButlerState(butler: ButlerSummary | null, opts: Partial<UseButlerResult> = {}) {
  vi.mocked(useButler).mockReturnValue({
    data: butler ? { data: butler } : undefined,
    isLoading: false,
    error: null,
    ...opts,
  } as UseButlerResult);
}

function renderPage(): string {
  const queryClient = new QueryClient();
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ButlerDetailPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Single-H1 contract — ButlerDetailPage
// ---------------------------------------------------------------------------

describe("ButlerDetailPage — single-H1 contract", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    localStorageMock.clear();
    // Default to operator mode so the resident-only tabs don't interfere
    localStorageMock.getItem.mockImplementation((key: string) =>
      key === "butlers.detail.mode" ? "operator" : null,
    );
    setButlerState(BASE_BUTLER);
  });

  it("renders exactly one <h1> element", () => {
    const html = renderPage();
    const h1Matches = html.match(/<h1[\s>]/g) ?? [];
    expect(h1Matches).toHaveLength(1);
  });

  it("h1 contains the titleized butler name", () => {
    const html = renderPage();
    const h1Match = html.match(/<h1[^>]*>(.*?)<\/h1>/s);
    expect(h1Match).not.toBeNull();
    // Title must be titleized (first letter capitalized), not raw lowercase.
    expect(h1Match![1]).toContain("General");
  });

  it("tabs block remains inside the primary content — no second h1", () => {
    const html = renderPage();
    // Tabs render Overview, Sessions, etc. — none should generate an h1
    expect(html).toContain("Overview");
    expect(html).toContain("Sessions");
    const h1Matches = html.match(/<h1[\s>]/g) ?? [];
    expect(h1Matches).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// ChatPanel — actions slot placement
// ---------------------------------------------------------------------------
//
// spec: openspec/specs/dashboard-butler-management/spec.md §96-99
// bead: bu-sfeuw.2
//
// ChatPanel MUST be rendered exactly once via the <Page> actions slot.
// The static-markup assertions below pin single-mount placement in the heading
// row. Any duplicate or shadow render would produce more than one
// data-testid="chat-panel" occurrence.
// ---------------------------------------------------------------------------

describe("ButlerDetailPage — ChatPanel actions slot", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    localStorageMock.clear();
    localStorageMock.getItem.mockImplementation((key: string) =>
      key === "butlers.detail.mode" ? "operator" : null,
    );
    setButlerState(BASE_BUTLER);
  });

  it("renders exactly one ChatPanel instance", () => {
    const html = renderPage();
    const occurrences = (html.match(/data-testid="chat-panel"/g) ?? []).length;
    expect(occurrences).toBe(1);
  });

  it("ChatPanel receives the butler name as butlerName prop", () => {
    const html = renderPage();
    // The mock renders the butlerName as text content inside the stub element
    expect(html).toContain(
      '<div data-testid="chat-panel">general</div>',
    );
  });

  it("ChatPanel appears in the heading region alongside the h1", () => {
    const html = renderPage();
    // The heading row is a flex container with the h1 and the shrink-0 actions div.
    // Verify both the h1 and the chat-panel stub are present in the same document.
    const h1Index = html.indexOf("<h1");
    const chatPanelIndex = html.indexOf('data-testid="chat-panel"');
    expect(h1Index).toBeGreaterThanOrEqual(0);
    expect(chatPanelIndex).toBeGreaterThanOrEqual(0);
    // actions slot renders after the heading opening tag in document order
    expect(chatPanelIndex).toBeGreaterThan(h1Index);
  });
});

// ---------------------------------------------------------------------------
// Basic rendering
// ---------------------------------------------------------------------------

describe("ButlerDetailPage — rendering", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    localStorageMock.clear();
    localStorageMock.getItem.mockImplementation((key: string) =>
      key === "butlers.detail.mode" ? "operator" : null,
    );
    setButlerState(BASE_BUTLER);
  });

  it("renders the butler name as page title", () => {
    const html = renderPage();
    expect(html).toContain("general");
  });

  it("renders tabs block inside the page", () => {
    const html = renderPage();
    // Core tab labels are always present
    expect(html).toContain("Sessions");
    expect(html).toContain("Config");
    expect(html).toContain("Skills");
  });

  it("renders breadcrumbs with Overview and Butlers links", () => {
    const html = renderPage();
    expect(html).toContain("/butlers");
    expect(html).toContain("Butlers");
  });
});

// ---------------------------------------------------------------------------
// Gate-A A2 Hero contract — ButlerDetailActions in actions slot
// ---------------------------------------------------------------------------
//
// spec: openspec/changes/redesign-butler-detail-no-hero/tasks.md §2.4
// bead: bu-sfeuw.3
//
// The Page shell `actions` slot MUST contain: ChatPanel, status pill,
// force-run button, and pause button. NO Tier-2 hero block must appear.
// ---------------------------------------------------------------------------

describe("ButlerDetailPage — Gate-A A2 actions slot", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    localStorageMock.clear();
    localStorageMock.getItem.mockImplementation((key: string) =>
      key === "butlers.detail.mode" ? "operator" : null,
    );
    setButlerState(BASE_BUTLER);
  });

  it("renders the status pill in the actions slot", () => {
    const html = renderPage();
    expect(html).toContain('data-testid="butler-status-pill"');
  });

  it("renders the force-run button in the actions slot", () => {
    const html = renderPage();
    expect(html).toContain('data-testid="butler-force-run"');
  });

  it("renders the pause button in the actions slot", () => {
    const html = renderPage();
    expect(html).toContain('data-testid="butler-pause"');
  });

  it("status pill, force-run, and pause all appear after the h1 (actions slot order)", () => {
    const html = renderPage();
    const h1Index = html.indexOf("<h1");
    const statusPillIndex = html.indexOf('data-testid="butler-status-pill"');
    const forceRunIndex = html.indexOf('data-testid="butler-force-run"');
    const pauseIndex = html.indexOf('data-testid="butler-pause"');

    expect(h1Index).toBeGreaterThanOrEqual(0);
    expect(statusPillIndex).toBeGreaterThan(h1Index);
    expect(forceRunIndex).toBeGreaterThan(h1Index);
    expect(pauseIndex).toBeGreaterThan(h1Index);
  });

  it("renders exactly one ButlerDetailActions wrapper", () => {
    const html = renderPage();
    const occurrences = (html.match(/data-testid="butler-detail-actions"/g) ?? []).length;
    expect(occurrences).toBe(1);
  });

  it("does NOT render a Tier-2 hero block above the tabs", () => {
    const html = renderPage();
    // The only h1 is the Page title. A Tier-2 hero would introduce a second
    // heading-level element (h2) with the butler identity above the tabs.
    // There must be no 'hero' data-testid in the output.
    expect(html).not.toContain('data-testid="hero"');
    // The tabs block still renders
    expect(html).toContain("Overview");
    expect(html).toContain("Sessions");
  });

  it("status pill shows Up badge for ok status", () => {
    const html = renderPage();
    // BASE_BUTLER has status: "ok" → renders "Up" text inside the pill
    const pillStart = html.indexOf('data-testid="butler-status-pill"');
    const pillRegion = html.slice(pillStart, pillStart + 200);
    expect(pillRegion).toContain("Up");
  });
});

// ---------------------------------------------------------------------------
// Gate-B B2 tab vocabulary constants
// ---------------------------------------------------------------------------
//
// spec: openspec/changes/redesign-detail-page-tab-vocabulary/design.md §Decisions 2-3, 6
// bead: bu-8bayc.1
//
// BASE_TABS_OPERATOR and BASE_TABS_RESIDENT must be exported as named constants
// with the exact Gate B B2 tab sets. OPERATOR_EXTENSION_TABS covers the
// non-spec Models tab while the code still exposes it. bu-8bayc.2 will add the
// mode toggle and localStorage persistence on top of these constants.
// ---------------------------------------------------------------------------

describe("ButlerDetailPage — Gate-B B2 tab vocabulary constants", () => {
  it("BASE_TABS_OPERATOR contains exactly the 10 spec-mandated base tabs", () => {
    const expected = [
      "overview",
      "sessions",
      "config",
      "skills",
      "schedules",
      "trigger",
      "mcp",
      "state",
      "crm",
      "memory",
    ];
    expect([...BASE_TABS_OPERATOR]).toEqual(expected);
  });

  it("BASE_TABS_RESIDENT contains exactly the 7-tab Dispatch vocabulary", () => {
    const expected = [
      "overview",
      "activity",
      "logs",
      "approvals",
      "spend",
      "config",
      "memory",
    ];
    expect([...BASE_TABS_RESIDENT]).toEqual(expected);
  });

  it("OPERATOR_EXTENSION_TABS contains models (non-spec operator-only tab)", () => {
    expect([...OPERATOR_EXTENSION_TABS]).toEqual(["models"]);
  });

  it("BASE_TABS_OPERATOR does not include models (models is an extension, not a base tab)", () => {
    expect(BASE_TABS_OPERATOR).not.toContain("models");
  });

  it("BASE_TABS_RESIDENT does not include operator-only tabs", () => {
    const operatorOnly = ["sessions", "skills", "schedules", "trigger", "mcp", "state", "crm", "models"];
    for (const tab of operatorOnly) {
      expect(BASE_TABS_RESIDENT).not.toContain(tab);
    }
  });

  it("both modes share overview, config, and memory as common base tabs", () => {
    const shared = ["overview", "config", "memory"];
    for (const tab of shared) {
      expect(BASE_TABS_OPERATOR).toContain(tab);
      expect(BASE_TABS_RESIDENT).toContain(tab);
    }
  });
});

// ---------------------------------------------------------------------------
// Gate-B B2 default rendering
// ---------------------------------------------------------------------------
//
// Default mode is "resident" (localStorage empty → resident).
// Operator mode is activated by setting localStorage "butlers.detail.mode" = "operator".
// ---------------------------------------------------------------------------

describe("ButlerDetailPage — Gate-B B2 default rendering (resident mode)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    localStorageMock.clear();
    // Simulate no stored mode → default resident
    localStorageMock.getItem.mockReturnValue(null);
    setButlerState(BASE_BUTLER);
  });

  it("renders resident mode tab triggers by default (no localStorage set)", () => {
    const html = renderPage();
    // Resident tabs present
    expect(html).toContain("Overview");
    expect(html).toContain("Config");
    expect(html).toContain("Memory");
    // Operator-only tabs must NOT be present as tab triggers
    expect(html).not.toContain(">Sessions<");
    expect(html).not.toContain(">Models<");
    expect(html).not.toContain(">Skills<");
  });

  it("renders the mode toggle pill", () => {
    const html = renderPage();
    expect(html).toContain('data-testid="butler-mode-toggle"');
  });

  it("mode toggle pill shows Resident label in resident mode", () => {
    const html = renderPage();
    const toggleIndex = html.indexOf('data-testid="butler-mode-toggle"');
    const pillRegion = html.slice(toggleIndex, toggleIndex + 300);
    expect(pillRegion).toContain("Resident");
  });
});

describe("ButlerDetailPage — Gate-B B2 operator mode rendering", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    localStorageMock.clear();
    // Simulate operator mode stored in localStorage
    localStorageMock.getItem.mockImplementation((key: string) =>
      key === "butlers.detail.mode" ? "operator" : null,
    );
    setButlerState(BASE_BUTLER);
  });

  it("renders all 10 operator base tab triggers when localStorage = operator", () => {
    const html = renderPage();
    const operatorLabels = [
      "Overview",
      "Sessions",
      "Config",
      "Skills",
      "Schedules",
      "Trigger",
      "MCP",
      "State",
      "CRM",
      "Memory",
    ];
    for (const label of operatorLabels) {
      expect(html).toContain(label);
    }
  });

  it("renders Models tab trigger in operator mode", () => {
    const html = renderPage();
    expect(html).toContain("Models");
  });

  it("mode toggle pill shows Operator label in operator mode", () => {
    const html = renderPage();
    const toggleIndex = html.indexOf('data-testid="butler-mode-toggle"');
    const pillRegion = html.slice(toggleIndex, toggleIndex + 300);
    expect(pillRegion).toContain("Operator");
  });
});

// ---------------------------------------------------------------------------
// Gate-B B2 mode toggle — keyboard accessibility
// ---------------------------------------------------------------------------
//
// spec: openspec/changes/redesign-detail-page-tab-vocabulary/design.md §Decisions 2-3, 6
// bead: bu-8bayc.2
// ---------------------------------------------------------------------------

describe("ButlerDetailPage — Gate-B B2 mode toggle accessibility", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    localStorageMock.clear();
    localStorageMock.getItem.mockReturnValue(null);
    setButlerState(BASE_BUTLER);
  });

  afterEach(() => {
    localStorageMock.clear();
  });

  it("toggle button has role=switch for keyboard/AT accessibility", () => {
    const html = renderPage();
    expect(html).toContain('role="switch"');
  });

  it("toggle has an aria-label describing the mode change action", () => {
    const html = renderPage();
    // In resident mode, aria-label should describe switching to operator
    expect(html).toContain("aria-label=");
    expect(html).toContain("operator");
  });

  it("toggle aria-checked is false in resident mode", () => {
    const html = renderPage();
    // aria-checked="false" when resident (operator is unchecked)
    expect(html).toContain('aria-checked="false"');
  });

  it("toggle aria-checked is true in operator mode", () => {
    localStorageMock.getItem.mockImplementation((key: string) =>
      key === "butlers.detail.mode" ? "operator" : null,
    );
    const html = renderPage();
    expect(html).toContain('aria-checked="true"');
  });

  it("toggle is focusable (not disabled) in the rendered output", () => {
    const html = renderPage();
    // The button must not have disabled attribute
    const toggleStart = html.indexOf('data-testid="butler-mode-toggle"');
    const toggleRegion = html.slice(Math.max(0, toggleStart - 200), toggleStart + 100);
    expect(toggleRegion).not.toContain("disabled");
  });
});

// ---------------------------------------------------------------------------
// Gate-B B2 localStorage persistence paths
// ---------------------------------------------------------------------------
//
// The page reads mode on init from localStorage("butlers.detail.mode").
// Defaults to "resident" when key is absent or unknown.
// ---------------------------------------------------------------------------

describe("ButlerDetailPage — Gate-B B2 localStorage persistence", () => {
  afterEach(() => {
    localStorageMock.clear();
  });

  it("reads resident mode by default when localStorage returns null", () => {
    vi.resetAllMocks();
    localStorageMock.getItem.mockReturnValue(null);
    setButlerState(BASE_BUTLER);
    const html = renderPage();
    // Resident mode: no Sessions trigger
    expect(html).not.toContain(">Sessions<");
    // Mode toggle shows Resident label
    expect(html).toContain("Resident");
  });

  it("reads operator mode when localStorage key is set to operator", () => {
    vi.resetAllMocks();
    localStorageMock.getItem.mockImplementation((key: string) =>
      key === "butlers.detail.mode" ? "operator" : null,
    );
    setButlerState(BASE_BUTLER);
    const html = renderPage();
    expect(html).toContain("Sessions");
    expect(html).toContain("Models");
  });

  it("falls back to resident when localStorage returns an unknown value", () => {
    vi.resetAllMocks();
    localStorageMock.getItem.mockReturnValue("superuser");
    setButlerState(BASE_BUTLER);
    const html = renderPage();
    expect(html).not.toContain(">Sessions<");
  });
});

// ---------------------------------------------------------------------------
// Gate-B B2 getAllTabs / isValidTab mode-aware helpers
// ---------------------------------------------------------------------------

describe("ButlerDetailPage — Gate-B B2 getAllTabs helper", () => {
  it("operator mode returns all 10 base tabs + models for a regular butler", () => {
    const tabs = getAllTabs("general", "operator");
    expect(tabs).toContain("overview");
    expect(tabs).toContain("sessions");
    expect(tabs).toContain("skills");
    expect(tabs).toContain("models");
    expect([...tabs]).toHaveLength(11); // 10 base + 1 extension
  });

  it("resident mode returns 7 dispatch vocabulary tabs for a regular butler", () => {
    const tabs = getAllTabs("general", "resident");
    expect(tabs).toContain("overview");
    expect(tabs).toContain("activity");
    expect(tabs).toContain("logs");
    expect(tabs).toContain("approvals");
    expect(tabs).toContain("spend");
    expect(tabs).toContain("config");
    expect(tabs).toContain("memory");
    expect([...tabs]).toHaveLength(7);
  });

  it("resident mode does not include operator-only tabs", () => {
    const tabs = getAllTabs("general", "resident");
    const operatorOnly = ["sessions", "skills", "schedules", "trigger", "mcp", "state", "crm", "models"];
    for (const tab of operatorOnly) {
      expect(tabs).not.toContain(tab);
    }
  });

  it("health butler appends the health tab in both modes", () => {
    expect(getAllTabs("health", "operator")).toContain("health");
    expect(getAllTabs("health", "resident")).toContain("health");
  });

  it("switchboard butler appends routing-log and registry in both modes", () => {
    expect(getAllTabs("switchboard", "operator")).toContain("routing-log");
    expect(getAllTabs("switchboard", "operator")).toContain("registry");
    expect(getAllTabs("switchboard", "resident")).toContain("routing-log");
    expect(getAllTabs("switchboard", "resident")).toContain("registry");
  });
});

describe("ButlerDetailPage — Gate-B B2 isValidTab helper", () => {
  it("sessions is valid in operator mode", () => {
    expect(isValidTab("sessions", "general", "operator")).toBe(true);
  });

  it("sessions is NOT valid in resident mode", () => {
    expect(isValidTab("sessions", "general", "resident")).toBe(false);
  });

  it("models is valid in operator mode", () => {
    expect(isValidTab("models", "general", "operator")).toBe(true);
  });

  it("models is NOT valid in resident mode", () => {
    expect(isValidTab("models", "general", "resident")).toBe(false);
  });

  it("overview is valid in both modes", () => {
    expect(isValidTab("overview", "general", "operator")).toBe(true);
    expect(isValidTab("overview", "general", "resident")).toBe(true);
  });

  it("null is not a valid tab in either mode", () => {
    expect(isValidTab(null, "general", "operator")).toBe(false);
    expect(isValidTab(null, "general", "resident")).toBe(false);
  });

  it("unknown tab name is not valid in either mode", () => {
    expect(isValidTab("bogus-tab", "general", "operator")).toBe(false);
    expect(isValidTab("bogus-tab", "general", "resident")).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Gate-B B2 deep-link auto-promotion (Spec Decision 6)
// ---------------------------------------------------------------------------
//
// When the URL contains ?tab=<operator-only-tab> and mode is resident,
// the page must auto-promote to operator mode and persist it.
// This is tested via rendering with a mocked useSearchParams that returns
// an operator-only tab param while localStorage has no mode stored.
// ---------------------------------------------------------------------------

describe("ButlerDetailPage — Gate-B B2 deep-link auto-promotion", () => {
  // useSearchParams imported at top; vitest allows per-test mock implementation changes.

  afterEach(() => {
    localStorageMock.clear();
    vi.resetAllMocks();
  });

  it("auto-promotes resident → operator when URL has ?tab=models", () => {
    // Start with no stored mode (defaults to resident)
    localStorageMock.getItem.mockReturnValue(null);
    // Provide models tab param in URL
    vi.mocked(useSearchParams).mockReturnValue([
      new URLSearchParams("tab=models"),
      vi.fn(),
    ]);
    setButlerState(BASE_BUTLER);
    // After auto-promotion, localStorage.setItem should be called with operator
    renderPage();
    expect(localStorageMock.setItem).toHaveBeenCalledWith(
      "butlers.detail.mode",
      "operator",
    );
  });

  it("auto-promotes resident → operator when URL has ?tab=sessions", () => {
    localStorageMock.getItem.mockReturnValue(null);
    vi.mocked(useSearchParams).mockReturnValue([
      new URLSearchParams("tab=sessions"),
      vi.fn(),
    ]);
    setButlerState(BASE_BUTLER);
    renderPage();
    expect(localStorageMock.setItem).toHaveBeenCalledWith(
      "butlers.detail.mode",
      "operator",
    );
  });

  it("does NOT auto-promote when URL has a resident-valid tab", () => {
    localStorageMock.getItem.mockReturnValue(null);
    vi.mocked(useSearchParams).mockReturnValue([
      new URLSearchParams("tab=activity"),
      vi.fn(),
    ]);
    setButlerState(BASE_BUTLER);
    renderPage();
    expect(localStorageMock.setItem).not.toHaveBeenCalledWith(
      "butlers.detail.mode",
      "operator",
    );
  });

  it("does NOT auto-promote when mode is already operator", () => {
    localStorageMock.getItem.mockImplementation((key: string) =>
      key === "butlers.detail.mode" ? "operator" : null,
    );
    vi.mocked(useSearchParams).mockReturnValue([
      new URLSearchParams("tab=models"),
      vi.fn(),
    ]);
    setButlerState(BASE_BUTLER);
    renderPage();
    // setItem should not be called for mode (already operator)
    const modeSetCalls = localStorageMock.setItem.mock.calls.filter(
      (call) => call[0] === "butlers.detail.mode",
    );
    expect(modeSetCalls).toHaveLength(0);
  });
});
