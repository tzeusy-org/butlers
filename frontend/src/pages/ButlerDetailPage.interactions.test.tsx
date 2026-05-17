// @vitest-environment jsdom
/**
 * ButlerDetailPage — interactive mode-toggle tests.
 *
 * Uses @testing-library/react + fireEvent to exercise the click/keyboard
 * interaction that drives onModeChange + localStorage persistence.
 * This complements the static-markup coverage in ButlerDetailPage.test.tsx.
 *
 * Bead: bu-km64s (follow-up from bu-8bayc.2 review)
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, cleanup, fireEvent } from "@testing-library/react";
import { MemoryRouter, useSearchParams } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import ButlerDetailPage from "@/pages/ButlerDetailPage";
import { useButler } from "@/hooks/use-butlers";
import type { ButlerSummary } from "@/api/types";

// ---------------------------------------------------------------------------
// Mocks — mirror the ones in ButlerDetailPage.test.tsx
// ---------------------------------------------------------------------------

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
  useButlerModules: vi.fn(() => ({ data: null, isLoading: false })),
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

vi.mock("@/hooks/use-spend", () => ({
  useSpendSummary: vi.fn(() => ({ data: null, isLoading: false })),
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

const localStorageMock = (() => {
  let store: Record<string, string | null> = {};
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

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

type UseButlerResult = ReturnType<typeof useButler>;

const BASE_BUTLER: ButlerSummary = {
  name: "general",
  status: "ok",
  port: 8001,
  type: "butler",
  sessions_24h: 0,
};

function setButlerState(butler: ButlerSummary | null) {
  vi.mocked(useButler).mockReturnValue({
    data: butler ? { data: butler } : undefined,
    isLoading: false,
    error: null,
  } as UseButlerResult);
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ButlerDetailPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  localStorageMock.clear();
  vi.resetAllMocks();
});

// ---------------------------------------------------------------------------
// Interactive mode-toggle tests
// ---------------------------------------------------------------------------
//
// These tests use fireEvent to exercise the click interaction that drives
// onModeChange + localStorage persistence. The toggle switches mode state
// in React so the rendered output changes (sessions tab appears/disappears).
//
// Spec: openspec/changes/redesign-detail-page-tab-vocabulary/design.md §Decisions 2-3
// Bead: bu-8bayc.2
// ---------------------------------------------------------------------------

describe("ButlerDetailPage — interactive mode toggle click", () => {
  beforeEach(() => {
    localStorageMock.getItem.mockReturnValue(null); // start resident
    vi.mocked(useSearchParams).mockReturnValue([new URLSearchParams(), vi.fn()]);
    setButlerState(BASE_BUTLER);
  });

  it("clicking the toggle from resident switches to operator and persists mode", () => {
    const { getByTestId, queryByText } = renderPage();

    // Initially in resident mode — Sessions tab not visible
    expect(queryByText("Sessions")).toBeNull();

    // Click the mode toggle
    const toggle = getByTestId("butler-mode-toggle");
    fireEvent.click(toggle);

    // After click, Sessions tab should appear (operator mode)
    expect(queryByText("Sessions")).not.toBeNull();

    // localStorage should have been written with the new mode
    expect(localStorageMock.setItem).toHaveBeenCalledWith(
      "butlers.detail.mode",
      "operator",
    );
  });

  it("clicking the toggle from operator switches to resident and persists mode", () => {
    localStorageMock.getItem.mockImplementation((key: string) =>
      key === "butlers.detail.mode" ? "operator" : null,
    );
    const { getByTestId, queryByText } = renderPage();

    // Initially in operator mode — Sessions visible
    expect(queryByText("Sessions")).not.toBeNull();

    fireEvent.click(getByTestId("butler-mode-toggle"));

    // After click: resident mode — Sessions hidden, Activity visible
    expect(queryByText("Sessions")).toBeNull();
    expect(queryByText("Activity")).not.toBeNull();

    expect(localStorageMock.setItem).toHaveBeenCalledWith(
      "butlers.detail.mode",
      "resident",
    );
  });

  it("toggle clears stale operator-only tab param when switching to resident", () => {
    // Start in operator mode with a sessions tab active
    localStorageMock.getItem.mockImplementation((key: string) =>
      key === "butlers.detail.mode" ? "operator" : null,
    );
    const setSearchParamsMock = vi.fn();
    vi.mocked(useSearchParams).mockReturnValue([
      new URLSearchParams("tab=sessions"),
      setSearchParamsMock,
    ]);
    const { getByTestId } = renderPage();

    fireEvent.click(getByTestId("butler-mode-toggle"));

    // setSearchParams should have been called to clear the stale tab param
    expect(setSearchParamsMock).toHaveBeenCalledWith({}, { replace: true });
  });
});
