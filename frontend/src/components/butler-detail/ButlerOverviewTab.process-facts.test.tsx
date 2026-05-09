/**
 * ButlerOverviewTab — process facts card regression tests.
 *
 * Asserts:
 *   1. The process facts card renders all four expected labels:
 *      Container, Port, Registered, Config.
 *   2. Values from the mock data are visible in the rendered output.
 *   3. No "pid" label or value appears anywhere in the rendered card.
 *
 * Bead: bu-8hbph.2
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import ButlerOverviewTab from "@/components/butler-detail/ButlerOverviewTab";
import { useButler } from "@/hooks/use-butlers";
import type { ButlerDetail } from "@/api/types";

// ---------------------------------------------------------------------------
// Mocks — silence heavy hooks that are out of scope for this test
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-butlers", () => ({
  useButler: vi.fn(),
  useButlerModules: vi.fn(() => ({ data: null, isLoading: false })),
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

// Stub components that require full DOM / router context not needed here.
vi.mock("@/components/notifications/notification-feed", () => ({
  NotificationFeed: () => <div data-testid="notification-feed" />,
}));

vi.mock("@/components/butler-detail/EligibilityTimeline", () => ({
  default: () => <div data-testid="eligibility-timeline" />,
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

type UseButlerResult = ReturnType<typeof useButler>;

const BASE_BUTLER: ButlerDetail = {
  name: "general",
  status: "ok",
  port: 8001,
  type: "butler",
  sessions_24h: 3,
  modules: [],
  schedules: [],
  skills: [],
  process_facts: {
    container_name: "butlers-up",
    port: 8001,
    registered_duration_seconds: 3723,
    config_path: "roster/general/butler.toml",
  },
};

function setButlerState(butler: ButlerDetail | null, opts: Partial<UseButlerResult> = {}) {
  vi.mocked(useButler).mockReturnValue({
    data: butler ? { data: butler, meta: {} } : undefined,
    isLoading: false,
    error: null,
    ...opts,
  } as UseButlerResult);
}

function renderTab(): string {
  const queryClient = new QueryClient();
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ButlerOverviewTab butlerName="general" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ButlerOverviewTab — process facts card", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setButlerState(BASE_BUTLER);
  });

  it("renders all four process fact labels", () => {
    const html = renderTab();
    expect(html).toContain("Container");
    expect(html).toContain("Port");
    expect(html).toContain("Registered");
    expect(html).toContain("Config");
  });

  it("renders the container_name value", () => {
    const html = renderTab();
    expect(html).toContain("butlers-up");
  });

  it("renders the port value", () => {
    const html = renderTab();
    // Port 8001 should appear at least once in the process facts section
    expect(html).toContain("8001");
  });

  it("renders a human-readable registered duration", () => {
    const html = renderTab();
    // 3723 seconds = 1h 2m
    expect(html).toContain("1h 2m");
  });

  it("renders the config_path value", () => {
    const html = renderTab();
    expect(html).toContain("roster/general/butler.toml");
  });

  it("does NOT render any pid label or value", () => {
    const html = renderTab();
    // Check for common pid representations (case-insensitive via lowercase)
    expect(html.toLowerCase()).not.toContain("pid");
    expect(html.toLowerCase()).not.toContain("process id");
    expect(html.toLowerCase()).not.toContain("process-id");
  });

  it("renders '--' placeholders when process_facts is null", () => {
    setButlerState({ ...BASE_BUTLER, process_facts: null });
    const html = renderTab();
    // Each of the 4 fact rows should show the unavailable placeholder
    const placeholderCount = (html.match(/--/g) ?? []).length;
    expect(placeholderCount).toBeGreaterThanOrEqual(4);
  });

  it("renders '--' for registered when registered_duration_seconds is null", () => {
    setButlerState({
      ...BASE_BUTLER,
      process_facts: {
        container_name: "butlers-up",
        port: 8001,
        registered_duration_seconds: null,
        config_path: "roster/general/butler.toml",
      },
    });
    const html = renderTab();
    expect(html).toContain("--");
  });
});
