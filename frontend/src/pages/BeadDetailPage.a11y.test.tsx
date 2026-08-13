// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { axe, toHaveNoViolations } from "jest-axe";

import { ApiError } from "@/api/client";
import type { BeadDetail, BeadDetailResponse } from "@/api/types";

expect.extend(toHaveNoViolations);

vi.mock("@/hooks/use-bead-detail", () => ({ useBeadDetail: vi.fn() }));

import BeadDetailPage from "./BeadDetailPage";
import { useBeadDetail } from "@/hooks/use-bead-detail";

const mockUseBeadDetail = vi.mocked(useBeadDetail);

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyMock = any;

const detail: BeadDetail = {
  id: "bu-safe",
  title: "Accessible snapshot detail",
  status: "open",
  priority: 1,
  type: "task",
  description: "A safe description.",
  design: null,
  acceptance_criteria: null,
  labels: ["privacy"],
  created_at: "2026-08-11T12:00:00Z",
  updated_at: null,
  started_at: null,
  closed_at: null,
  due_at: null,
  dependencies: [{ id: "bu-dependency", title: "Dependency", status: "open", priority: 2, type: "bug" }],
  external_ref: "TRACKER-123",
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function checkA11y(response: BeadDetailResponse | undefined, overrides: Partial<AnyMock> = {}) {
  mockUseBeadDetail.mockReturnValue({
    data: response,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    ...overrides,
  } as AnyMock);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { container } = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/beads/bu-safe"]}>
        <Routes>
          <Route path="/beads/:beadId" element={<BeadDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const results = await axe(container, { rules: { "color-contrast": { enabled: false } } });
  expect(results).toHaveNoViolations();
}

describe("BeadDetailPage accessibility", () => {
  it("has no axe violations for an available detail", async () => {
    await checkA11y({ data: detail, meta: { export_as_of: "2026-08-13T12:00:00Z" } });
  });

  it("has no axe violations for an unavailable snapshot", async () => {
    await checkA11y(undefined, {
      isError: true,
      error: new ApiError(
        "BEAD_SNAPSHOT_UNAVAILABLE",
        "Bead snapshot is unavailable.",
        503,
        { export_as_of: "2026-07-29T12:00:00Z" },
      ),
    });
  });

  it("has no axe violations for an honest not-found state", async () => {
    await checkA11y(undefined, {
      isError: true,
      error: new ApiError("BEAD_NOT_FOUND", "Bead not found in the current snapshot.", 404),
    });
  });
});
