// @vitest-environment jsdom
/**
 * Education error-state tests (bu-occhw honesty bundle).
 *
 * QuizHistoryList and MasterySummaryCards previously rendered the same
 * empty/skeleton state on a failed query as on a genuinely empty result,
 * silently hiding load failures. These tests pin the explicit error states.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";

import QuizHistoryList from "./QuizHistoryList";
import MasterySummaryCards from "./MasterySummaryCards";

vi.mock("@/hooks/use-education", () => ({
  useQuizResponses: vi.fn(),
  useMasterySummary: vi.fn(),
  useMindMapAnalytics: vi.fn(),
}));

import {
  useQuizResponses,
  useMasterySummary,
  useMindMapAnalytics,
} from "@/hooks/use-education";

const mockUseQuizResponses = vi.mocked(useQuizResponses);
const mockUseMasterySummary = vi.mocked(useMasterySummary);
const mockUseMindMapAnalytics = vi.mocked(useMindMapAnalytics);

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  cleanup();
});

describe("QuizHistoryList error state", () => {
  it("shows an error message (not the empty state) when the query errors", () => {
    mockUseQuizResponses.mockReturnValue({
      data: undefined,
      isError: true,
    } as unknown as ReturnType<typeof useQuizResponses>);

    render(<QuizHistoryList mindMapId="mm-1" />);

    expect(screen.getByText(/couldn't load quiz responses/i)).toBeTruthy();
    expect(screen.queryByText(/no quiz responses recorded/i)).toBeNull();
  });

  it("still shows the empty state when there is no error and no data", () => {
    mockUseQuizResponses.mockReturnValue({
      data: { data: [] },
      isError: false,
    } as unknown as ReturnType<typeof useQuizResponses>);

    render(<QuizHistoryList mindMapId="mm-1" />);

    expect(screen.getByText(/no quiz responses recorded/i)).toBeTruthy();
  });
});

describe("MasterySummaryCards error state", () => {
  it("shows an error message when the mastery summary query errors", () => {
    mockUseMasterySummary.mockReturnValue({
      data: undefined,
      isError: true,
    } as unknown as ReturnType<typeof useMasterySummary>);
    mockUseMindMapAnalytics.mockReturnValue({
      data: undefined,
    } as unknown as ReturnType<typeof useMindMapAnalytics>);

    render(<MasterySummaryCards mindMapId="mm-1" />);

    expect(screen.getAllByText(/couldn't load mastery summary/i).length).toBeGreaterThan(0);
  });
});
