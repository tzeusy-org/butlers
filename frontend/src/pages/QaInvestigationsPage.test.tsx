// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PaginatedResponse, QaCaseSummary } from "@/api/types";
import QaInvestigationsPage from "@/pages/QaInvestigationsPage";
import { useButlers } from "@/hooks/use-butlers";
import { useQaCases } from "@/hooks/use-qa";

const navigate = vi.fn();

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return {
    ...actual,
    useNavigate: () => navigate,
  };
});

vi.mock("@/hooks/use-qa", () => ({
  useQaCases: vi.fn(),
}));

vi.mock("@/hooks/use-butlers", () => ({
  useButlers: vi.fn(),
}));

type UseQaCasesResult = ReturnType<typeof useQaCases>;
type UseButlersResult = ReturnType<typeof useButlers>;

const BASE_CASES: QaCaseSummary[] = [
  {
    id: "attempt-finance",
    short_id: "#101",
    sev: "high",
    butler: "finance",
    headline: "Finance reconciliation failed",
    detected: "2026-05-14T09:00:00Z",
    age_seconds: 120,
    state: "pr",
    pr_state: "open",
    pr_url: "https://github.com/Tzeusy/butlers/pull/1",
  },
  {
    id: "attempt-health",
    short_id: "#202",
    sev: "medium",
    butler: "health",
    headline: "Health sync stalled",
    detected: "2026-05-14T10:00:00Z",
    age_seconds: 3600,
    state: "diagnose",
    pr_state: null,
    pr_url: null,
  },
];

function page(cases: QaCaseSummary[], total = cases.length): PaginatedResponse<QaCaseSummary> {
  return {
    data: cases,
    meta: {
      total,
      offset: 0,
      limit: cases.length || 50,
      has_more: total > cases.length,
    },
  };
}

function setCasesState(response: PaginatedResponse<QaCaseSummary>) {
  vi.mocked(useQaCases).mockReturnValue({
    data: response,
    isLoading: false,
    isError: false,
    error: null,
  } as UseQaCasesResult);
}

function setButlersState(names = ["finance", "health", "qa"]) {
  vi.mocked(useButlers).mockReturnValue({
    data: {
      data: names.map((name) => ({
        name,
        status: "ok",
        port: 9000,
        type: name === "qa" ? "staffer" : "butler",
        description: null,
        sessions_24h: 0,
      })),
      meta: {},
    },
    isLoading: false,
    isError: false,
    error: null,
  } as UseButlersResult);
}

function renderPage() {
  return render(
    <MemoryRouter>
      <QaInvestigationsPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.resetAllMocks();
  navigate.mockReset();
  setCasesState(page(BASE_CASES));
  setButlersState();
});

afterEach(() => {
  cleanup();
});

describe("QaInvestigationsPage", () => {
  it("renders cases through the Dispatch CaseList without table or chart chrome", () => {
    const { container } = renderPage();

    expect(screen.getByLabelText("QA cases")).toBeTruthy();
    expect(screen.getByText("Finance reconciliation failed")).toBeTruthy();
    expect(container.querySelector("table")).toBeNull();
    expect(container.innerHTML).not.toContain("recharts");
    expect(container.innerHTML).not.toContain("Kanban");
  });

  it("filters by state, severity, butler, and time range", () => {
    renderPage();

    fireEvent.change(screen.getByLabelText("State"), { target: { value: "pr_open" } });
    expect(screen.getByText("Finance reconciliation failed")).toBeTruthy();
    expect(screen.queryByText("Health sync stalled")).toBeNull();

    fireEvent.change(screen.getByLabelText("Severity"), { target: { value: "medium" } });
    expect(screen.getByText("Nothing matches.")).toBeTruthy();
    expect(vi.mocked(useQaCases)).toHaveBeenCalledWith(
      expect.objectContaining({
        limit: 50,
        offset: 0,
        sev: "medium",
        since: "7d",
      }),
    );

    fireEvent.change(screen.getByLabelText("State"), { target: { value: "all" } });
    expect(screen.getByText("Health sync stalled")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /butlers/i }));
    fireEvent.click(screen.getByRole("menuitemcheckbox", { name: "health" }));
    expect(screen.queryByText("Finance reconciliation failed")).toBeNull();
    expect(screen.getByText("Health sync stalled")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Time range"), { target: { value: "24h" } });
    expect(vi.mocked(useQaCases)).toHaveBeenCalledWith(
      expect.objectContaining({
        since: "24h",
      }),
    );
  });

  it("passes the all-time range explicitly instead of falling back to the backend default", () => {
    renderPage();

    fireEvent.change(screen.getByLabelText("Time range"), { target: { value: "all" } });

    expect(vi.mocked(useQaCases)).toHaveBeenCalledWith(
      expect.objectContaining({
        since: "all",
      }),
    );
  });

  it("renders the exact filtered empty state line", () => {
    setCasesState(page([]));
    const { container } = renderPage();

    expect(screen.getByText("Nothing matches.")).toBeTruthy();
    expect(container.innerHTML).not.toContain("No investigations found");
  });

  it("navigates to the investigation dossier when a row is clicked", () => {
    renderPage();

    fireEvent.click(screen.getByTestId("qa-case-row-attempt-finance"));

    expect(navigate).toHaveBeenCalledWith("/qa/investigations/attempt-finance");
  });

  it("loads more cases by expanding the cases query limit from offset zero", () => {
    setCasesState(page(BASE_CASES, 75));
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "Load more" }));

    expect(vi.mocked(useQaCases)).toHaveBeenCalledWith(
      expect.objectContaining({
        limit: 100,
        offset: 0,
      }),
    );
  });
});
