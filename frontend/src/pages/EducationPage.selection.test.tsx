// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";

vi.mock("@/hooks/use-education", () => ({
  useMindMaps: vi.fn(),
  // The receipt panel (bu-6jv4m.10) reads this on every branch of the page.
  // A readable, empty receipt store renders nothing, which keeps these
  // state-contract assertions about the mind-map branches alone.
  useCurriculumRequestReceipt: vi.fn(() => ({
    data: { receipts_available: true, receipt: null },
    isError: false,
    isLoading: false,
    refetch: vi.fn(),
  })),
}));

vi.mock("@/lib/command-registry", () => ({
  useRegisterCommands: vi.fn(),
}));

vi.mock("@/components/education/MindMapGraph", () => ({
  default: ({
    onNodeClick,
    onSelectNode,
  }: {
    onNodeClick?: (nodeId: string) => void;
    onSelectNode?: (selection: { mindMapId: string; nodeId: string }) => void;
  }) => (
    <button
      type="button"
      onClick={() =>
        onSelectNode?.({ mindMapId: "map-a", nodeId: "curriculum-node" }) ??
        onNodeClick?.("curriculum-node")
      }
    >
      Select curriculum node
    </button>
  ),
}));

vi.mock("@/components/education/ReviewTimeline", () => ({
  default: ({
    onSelectNode,
  }: {
    onSelectNode?: (selection: { mindMapId: string; nodeId: string }) => void;
  }) => (
    <button
      type="button"
      onClick={() => onSelectNode?.({ mindMapId: "map-b", nodeId: "review-node" })}
    >
      Open cross-map review
    </button>
  ),
}));

vi.mock("@/components/education/StrugglingNodesCard", () => ({
  default: ({
    onNodeClick,
    onSelectNode,
  }: {
    onNodeClick?: (nodeId: string) => void;
    onSelectNode?: (selection: { mindMapId: string; nodeId: string }) => void;
  }) => (
    <button
      type="button"
      onClick={() =>
        onSelectNode?.({ mindMapId: "map-a", nodeId: "analytics-node" }) ??
        onNodeClick?.("analytics-node")
      }
    >
      Select struggling concept
    </button>
  ),
}));

vi.mock("@/components/education/NodeDetailPanel", () => ({
  default: ({
    mindMapId,
    nodeId,
    onClose,
  }: {
    mindMapId: string | null;
    nodeId: string | null;
    onClose: () => void;
  }) => {
    if (!mindMapId || !nodeId) return null;

    return (
      <aside data-testid="node-detail">
        {mindMapId}:{nodeId}
        <button type="button" onClick={onClose}>
          Close node details
        </button>
      </aside>
    );
  },
}));

vi.mock("@/components/education/CurriculumActions", () => ({ default: () => null }));
vi.mock("@/components/education/QuizHistoryList", () => ({ default: () => null }));
vi.mock("@/components/education/MasterySummaryCards", () => ({ default: () => null }));
vi.mock("@/components/education/MasteryTrendChart", () => ({ default: () => null }));
vi.mock("@/components/education/CrossTopicChart", () => ({ default: () => null }));
vi.mock("@/components/education/RequestCurriculumDialog", () => ({ default: () => null }));

import { useMindMaps } from "@/hooks/use-education";
import EducationPage from "./EducationPage";

const mockUseMindMaps = vi.mocked(useMindMaps);

function renderPage() {
  return render(
    <MemoryRouter>
      <EducationPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockUseMindMaps.mockReturnValue({
    data: {
      data: [
        { id: "map-a", title: "Alpha", status: "active" },
        { id: "map-b", title: "Beta", status: "active" },
      ],
    },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useMindMaps>);
});

afterEach(cleanup);

describe("EducationPage shared node selection", () => {
  it("keeps the one selected panel visible when curriculum and analytics select nodes", async () => {
    const user = userEvent.setup();
    renderPage();

    await waitFor(() => {
      expect(screen.getByRole("combobox").textContent).toContain("Alpha");
    });

    await user.click(screen.getByRole("button", { name: "Select curriculum node" }));
    expect(screen.getByTestId("node-detail").textContent).toContain("map-a:curriculum-node");

    await user.click(screen.getByRole("tab", { name: "Analytics" }));
    expect(screen.getByTestId("node-detail").closest('[role="tabpanel"]')).toBeNull();

    await user.click(screen.getByRole("button", { name: "Select struggling concept" }));
    expect(screen.getByTestId("node-detail").textContent).toContain("map-a:analytics-node");
  });

  it("selects the review map and node without leaving Reviews, then closes only the node", async () => {
    const user = userEvent.setup();
    renderPage();

    await waitFor(() => {
      expect(screen.getByRole("combobox").textContent).toContain("Alpha");
    });

    const reviewsTab = screen.getByRole("tab", { name: "Reviews" });
    await user.click(reviewsTab);
    await user.click(screen.getByRole("button", { name: "Open cross-map review" }));

    expect(screen.getByRole("combobox").textContent).toContain("Beta");
    expect(screen.getByTestId("node-detail").textContent).toContain("map-b:review-node");
    expect(reviewsTab.getAttribute("data-state")).toBe("active");

    await user.click(screen.getByRole("button", { name: "Close node details" }));

    expect(screen.queryByTestId("node-detail")).toBeNull();
    expect(screen.getByRole("combobox").textContent).toContain("Beta");
    expect(reviewsTab.getAttribute("data-state")).toBe("active");
  });
});
