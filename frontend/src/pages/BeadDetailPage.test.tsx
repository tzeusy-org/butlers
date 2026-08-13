// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ApiError } from "@/api/client";
import type { BeadDetail, BeadDetailResponse } from "@/api/types";

vi.mock("@/hooks/use-bead-detail", () => ({ useBeadDetail: vi.fn() }));

import BeadDetailPage from "./BeadDetailPage";
import { useBeadDetail } from "@/hooks/use-bead-detail";

const mockUseBeadDetail = vi.mocked(useBeadDetail);

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyMock = any;

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function bead(overrides: Partial<BeadDetail> = {}): BeadDetail {
  return {
    id: "bu-safe",
    title: "Snapshot-only Bead detail",
    status: "open",
    priority: 1,
    type: "task",
    description: "A safe description.",
    design: "A bounded reader projects first.",
    acceptance_criteria: "No raw source fields leave the API.",
    labels: ["privacy", "decision"],
    created_at: "2026-08-11T12:00:00Z",
    updated_at: "2026-08-12T12:00:00Z",
    started_at: null,
    closed_at: null,
    due_at: "2026-08-14T12:00:00Z",
    dependencies: [
      {
        id: "bu-dependency",
        title: "Bounded dependency",
        status: "blocked",
        priority: 2,
        type: "bug",
      },
    ],
    external_ref: "TRACKER-123 https://outside.invalid/issue",
    ...overrides,
  };
}

function mockDetail(
  data: BeadDetailResponse | undefined,
  overrides: Partial<AnyMock> = {},
) {
  mockUseBeadDetail.mockReturnValue({
    data,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    ...overrides,
  } as AnyMock);
}

function renderPage(initialEntry = "/beads/bu-safe") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/beads/:beadId" element={<BeadDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("BeadDetailPage", () => {
  it("renders the strict API detail as rule-separated read-only content", () => {
    mockDetail({ data: bead(), meta: { export_as_of: "2026-08-13T12:00:00Z" } });

    const { container, getByRole, getByTestId } = renderPage();

    expect(getByRole("heading", { level: 1 }).textContent).toBe("Snapshot-only Bead detail");
    expect(container.textContent).toContain("A safe description.");
    expect(container.textContent).toContain("A bounded reader projects first.");
    expect(container.textContent).toContain("No raw source fields leave the API.");
    expect(container.textContent).toContain("Snapshot export as of");
    expect(getByRole("link", { name: /bounded dependency/i }).getAttribute("href")).toBe(
      "/beads/bu-dependency",
    );
    const externalRef = getByTestId("bead-external-ref");
    expect(externalRef.tagName).not.toBe("A");
    expect(externalRef.textContent).toContain("TRACKER-123 https://outside.invalid/issue");
    expect(container.querySelector('a[href="https://outside.invalid/issue"]')).toBeNull();
    expect(container.querySelector('[data-slot="card"]')).toBeNull();
    expect(container.textContent).not.toContain("Close bead");
    expect(container.textContent).not.toContain("Edit bead");
  });

  it("uses a valid stable section reference for multi-word headings", () => {
    mockDetail({ data: bead(), meta: { export_as_of: "2026-08-13T12:00:00Z" } });

    const { getByRole } = renderPage();

    const heading = getByRole("heading", { level: 2, name: "Acceptance criteria" });
    expect(heading.id).toBe("bead-section-acceptance-criteria");
    expect(heading.parentElement?.getAttribute("aria-labelledby")).toBe(heading.id);
  });

  it("keeps an unavailable snapshot distinct and shows its known export age", () => {
    mockDetail(undefined, {
      isError: true,
      error: new ApiError(
        "BEAD_SNAPSHOT_UNAVAILABLE",
        "Bead snapshot is unavailable.",
        503,
        { reason: "export_stale", export_as_of: "2026-07-29T12:00:00Z" },
      ),
    });

    const { getByRole, getByTestId, queryByText } = renderPage();

    expect(getByTestId("bead-snapshot-unavailable").textContent).toContain(
      "Bead snapshot is unavailable.",
    );
    expect(getByTestId("bead-snapshot-unavailable").textContent).toContain("Snapshot export as of");
    expect(getByRole("button", { name: "Retry" })).toBeTruthy();
    expect(queryByText("Bead not found")).toBeNull();
  });

  it("renders an honest not-found state only for a 404", () => {
    mockDetail(undefined, {
      isError: true,
      error: new ApiError("BEAD_NOT_FOUND", "Bead not found in the current snapshot.", 404),
    });

    const { getByRole, queryByTestId } = renderPage("/beads/bu-absent");

    expect(getByRole("heading", { level: 2, name: "Bead not found" })).toBeTruthy();
    expect(queryByTestId("bead-snapshot-unavailable")).toBeNull();
  });

  it("uses the shell loading state while the detail is pending", () => {
    mockDetail(undefined, { isLoading: true });

    const { getByRole } = renderPage();

    expect(getByRole("status", { name: "Loading" })).toBeTruthy();
  });
});
