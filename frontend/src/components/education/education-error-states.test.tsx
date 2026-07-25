// @vitest-environment jsdom
/**
 * Education error-state tests (bu-occhw honesty bundle; bu-ep4ks.5 extends
 * this file to the four core widgets that previously rendered "still
 * building it" copy on a fetch failure instead of an error state).
 *
 * QuizHistoryList and MasterySummaryCards previously rendered the same
 * empty/skeleton state on a failed query as on a genuinely empty result,
 * silently hiding load failures. These tests pin the explicit error states.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";

import QuizHistoryList from "./QuizHistoryList";
import MasterySummaryCards from "./MasterySummaryCards";
import MindMapGraph from "./MindMapGraph";
import MasteryTrendChart from "./MasteryTrendChart";
import StrugglingNodesCard from "./StrugglingNodesCard";

vi.mock("@/hooks/use-education", () => ({
  useQuizResponses: vi.fn(),
  useMasterySummary: vi.fn(),
  useMindMapAnalytics: vi.fn(),
  useMindMap: vi.fn(),
  useFrontierNodes: vi.fn(),
}));

import {
  useQuizResponses,
  useMasterySummary,
  useMindMapAnalytics,
  useMindMap,
  useFrontierNodes,
} from "@/hooks/use-education";

const mockUseQuizResponses = vi.mocked(useQuizResponses);
const mockUseMasterySummary = vi.mocked(useMasterySummary);
const mockUseMindMapAnalytics = vi.mocked(useMindMapAnalytics);
const mockUseMindMap = vi.mocked(useMindMap);
const mockUseFrontierNodes = vi.mocked(useFrontierNodes);

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

// ---------------------------------------------------------------------------
// bu-ep4ks.5 — fabricated-calm leaf sweep: MindMapGraph, MasteryTrendChart,
// StrugglingNodesCard previously rendered a calm "no data yet" / "still
// building it" empty state on a fetch failure, indistinguishable from a
// mind map that genuinely has no concepts yet.
// ---------------------------------------------------------------------------

describe("MindMapGraph error state", () => {
  it("shows a degraded note (not the 'still building it' empty state) when the query errors", () => {
    mockUseMindMap.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMindMap>);
    mockUseFrontierNodes.mockReturnValue({
      data: undefined,
    } as unknown as ReturnType<typeof useFrontierNodes>);

    render(<MindMapGraph mindMapId="mm-1" onSelectNode={vi.fn()} />);

    expect(screen.getByTestId("mind-map-graph-degraded")).toBeTruthy();
    expect(screen.queryByText(/still building it/i)).toBeNull();
  });

  it("still shows the 'still building it' empty state when there is no error and no nodes", () => {
    mockUseMindMap.mockReturnValue({
      data: { nodes: [], edges: [] },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMindMap>);
    mockUseFrontierNodes.mockReturnValue({
      data: undefined,
    } as unknown as ReturnType<typeof useFrontierNodes>);

    render(<MindMapGraph mindMapId="mm-1" onSelectNode={vi.fn()} />);

    expect(screen.getByText(/still building it/i)).toBeTruthy();
  });
});

describe("MasteryTrendChart error state", () => {
  it("shows a degraded note (not the 'analytics will appear' empty state) when the query errors", () => {
    mockUseMindMapAnalytics.mockReturnValue({
      data: undefined,
      isError: true,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMindMapAnalytics>);

    render(<MasteryTrendChart mindMapId="mm-1" />);

    expect(screen.getByTestId("mastery-trend-chart-degraded")).toBeTruthy();
    expect(screen.queryByText(/analytics will appear/i)).toBeNull();
  });

  it("still shows the 'analytics will appear' empty state when there is no error and no trend", () => {
    mockUseMindMapAnalytics.mockReturnValue({
      data: { trend: [] },
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMindMapAnalytics>);

    render(<MasteryTrendChart mindMapId="mm-1" />);

    expect(screen.getByText(/analytics will appear/i)).toBeTruthy();
  });
});

describe("StrugglingNodesCard error state", () => {
  it("shows a degraded note (not a silent null render) when the query errors", () => {
    mockUseMindMapAnalytics.mockReturnValue({
      data: undefined,
      isError: true,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMindMapAnalytics>);

    render(<StrugglingNodesCard mindMapId="mm-1" onSelectNode={vi.fn()} />);

    expect(screen.getByTestId("struggling-nodes-degraded")).toBeTruthy();
  });

  it("still renders nothing when there is no error and no struggling nodes", () => {
    mockUseMindMapAnalytics.mockReturnValue({
      data: { metrics: { struggling_nodes: [] } },
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMindMapAnalytics>);

    const { container } = render(
      <StrugglingNodesCard mindMapId="mm-1" onSelectNode={vi.fn()} />,
    );

    expect(container.firstChild).toBeNull();
  });
});
