import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import ButlerDetailPage, {
  BASE_TABS_OPERATOR,
  BASE_TABS_RESIDENT,
  OPERATOR_EXTENSION_TABS,
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
    setButlerState(BASE_BUTLER);
  });

  it("renders exactly one <h1> element", () => {
    const html = renderPage();
    const h1Matches = html.match(/<h1[\s>]/g) ?? [];
    expect(h1Matches).toHaveLength(1);
  });

  it("h1 contains the butler name", () => {
    const html = renderPage();
    const h1Match = html.match(/<h1[^>]*>(.*?)<\/h1>/s);
    expect(h1Match).not.toBeNull();
    expect(h1Match![1]).toContain("general");
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
// Gate-B B2 default rendering (operator as current default until bu-8bayc.2)
// ---------------------------------------------------------------------------
//
// The page currently renders operator mode tabs by default.
// bu-8bayc.2 will add the mode toggle; until then operator tabs are always shown.
// ---------------------------------------------------------------------------

describe("ButlerDetailPage — Gate-B B2 default rendering", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setButlerState(BASE_BUTLER);
  });

  it("renders all 10 operator base tab triggers by default", () => {
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

  it("renders Models tab trigger (operator extension tab, currently exposed)", () => {
    const html = renderPage();
    expect(html).toContain("Models");
  });
});
