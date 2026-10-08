// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";

vi.mock("@/hooks/use-education", () => ({
  useMindMaps: vi.fn(),
  useMindMap: vi.fn(() => ({ data: undefined })),
  useUpdateMindMapStatus: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
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

vi.mock("@/components/education/QuizHistoryList", () => ({ default: () => null }));
vi.mock("@/components/education/MasterySummaryCards", () => ({ default: () => null }));
vi.mock("@/components/education/MasteryTrendChart", () => ({ default: () => null }));
vi.mock("@/components/education/CrossTopicChart", () => ({ default: () => null }));
vi.mock("@/components/education/RequestCurriculumDialog", () => ({ default: () => null }));

import { useMindMap, useMindMaps, useUpdateMindMapStatus } from "@/hooks/use-education";
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
  vi.mocked(useMindMap).mockReturnValue({ data: undefined } as unknown as ReturnType<typeof useMindMap>);
  vi.mocked(useUpdateMindMapStatus).mockReturnValue({ mutate: vi.fn(), isPending: false } as unknown as ReturnType<typeof useUpdateMindMapStatus>);
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


it("keeps drafts visible and selects an active map first, falling back to a lone draft", async () => {
  for (const maps of [
    [{ id: "draft", title: "Setup", status: "draft" }, { id: "active", title: "Learning", status: "active" }],
    [{ id: "draft", title: "Setup", status: "draft" }],
  ]) {
    mockUseMindMaps.mockReturnValue({ data: { data: maps }, isLoading: false } as unknown as ReturnType<typeof useMindMaps>);
    const view = renderPage();
    await waitFor(() => expect(screen.getByRole("combobox").textContent).toContain(maps.length === 2 ? "Learning" : "Setup"));
    if (maps.length === 1) expect(screen.getByRole("combobox").textContent).toContain("Setting up");
    expect(screen.getByRole("tab", { name: "Curriculum" })).toBeTruthy();
    view.unmount();
  }

  // The real list endpoint applies status before its default twenty-row page.
  // A newer terminal page must not hide older active maps or setup drafts.
  const storedMaps = [
    ...Array.from({ length: 20 }, (_, index) => ({
      id: `terminal-${index}`, title: `Finished ${index}`, status: index % 2 ? "completed" : "abandoned",
    })),
    { id: "older-active", title: "Older learning", status: "active" },
    { id: "older-draft", title: "Older setup", status: "draft" },
  ];
  mockUseMindMaps.mockImplementation((params) => ({
    data: { data: storedMaps.filter((map) => !params?.status || map.status === params.status).slice(0, params?.limit ?? 20) },
    isLoading: false, isError: false, refetch: vi.fn(),
  }) as unknown as ReturnType<typeof useMindMaps>);
  const user = userEvent.setup();
  const view = renderPage();
  await waitFor(() => expect(screen.getByRole("combobox").textContent).toContain("Older learning"));
  screen.getByRole("combobox").focus();
  await user.keyboard("{Enter}");
  expect(screen.getByRole("option", { name: "Older setup (Setting up)" })).toBeTruthy();
  expect(screen.queryByRole("option", { name: "Finished 0" })).toBeNull();
  expect(mockUseMindMaps).toHaveBeenCalledWith({ status: "active" });
  expect(mockUseMindMaps).toHaveBeenCalledWith({ status: "draft" });
  view.unmount();

  const activeRetry = vi.fn();
  const draftRetry = vi.fn();
  vi.mocked(useMindMap).mockImplementation((id) => ({
    data: id === storedMaps[20].id ? { ...storedMaps[20], nodes: [] } : undefined,
    isLoading: false, isError: false, refetch: vi.fn(),
  }) as unknown as ReturnType<typeof useMindMap>);
  mockUseMindMaps.mockImplementation((params) => ({
    data: params?.status === "active" ? { data: [storedMaps[20]] } : undefined,
    isLoading: false, isError: params?.status === "draft",
    refetch: params?.status === "active" ? activeRetry : draftRetry,
  }) as unknown as ReturnType<typeof useMindMaps>);
  const partial = renderPage();
  await waitFor(() => expect(screen.getByRole("combobox").textContent).toContain("Older learning"));
  expect(screen.getByRole("alert").textContent).toContain("Setting-up curricula");
  expect(screen.queryByText("No curriculums yet.")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Retry" }));
  expect(activeRetry).toHaveBeenCalledOnce();
  expect(draftRetry).toHaveBeenCalledOnce();
  partial.unmount();

  // A confirmed status mutation invalidates BOTH existing list/detail query
  // prefixes. The authoritative detail remains selected after the eligible
  // filtered lists refresh and no longer contain the now-abandoned record.
  const current = { id: "selected", title: "Selected learning", status: "active", nodes: [{ id: "concept" }] };
  const mutate = vi.fn(({ status }: { status: string }) => { current.status = status; });
  mockUseMindMaps.mockImplementation((params) => ({
    data: { data: current.status === params?.status ? [current] : [] },
    isLoading: false, isError: false, refetch: vi.fn(),
  }) as unknown as ReturnType<typeof useMindMaps>);
  vi.mocked(useMindMap).mockImplementation((id) => ({ data: id === current.id ? current : undefined }) as unknown as ReturnType<typeof useMindMap>);
  vi.mocked(useUpdateMindMapStatus).mockReturnValue({ mutate, isPending: false } as unknown as ReturnType<typeof useUpdateMindMapStatus>);
  const selected = renderPage();
  await waitFor(() => expect(screen.getByText("active")).toBeTruthy());
  await user.click(screen.getByRole("button", { name: "Abandon" }));
  await user.click(screen.getByRole("button", { name: "Confirm" }));
  expect(mutate).toHaveBeenCalledWith({ mindMapId: current.id, status: "abandoned" });
  selected.rerender(<MemoryRouter><EducationPage /></MemoryRouter>);
  expect(screen.queryByText("No curriculums yet.")).toBeNull();
  expect(screen.getByText("abandoned")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Re-activate" }).hasAttribute("disabled")).toBe(false);
  await user.click(screen.getByRole("button", { name: "Re-activate" }));
  await user.click(screen.getByRole("button", { name: "Confirm" }));
  expect(mutate).toHaveBeenLastCalledWith({ mindMapId: current.id, status: "active" });
  selected.rerender(<MemoryRouter><EducationPage /></MemoryRouter>);
  expect(screen.getByText("active")).toBeTruthy();

  // After removal from both eligible lists, an unresolved or wrong-ID detail
  // cannot supply another curriculum's badge, actions or a calm empty state.
  current.status = "abandoned";
  vi.mocked(useMindMap).mockReturnValue({ data: undefined, isLoading: true, isError: false } as unknown as ReturnType<typeof useMindMap>);
  selected.rerender(<MemoryRouter><EducationPage /></MemoryRouter>);
  expect(screen.getByText("Loading...")).toBeTruthy();
  expect(screen.queryByText("No curriculums yet.")).toBeNull();
  const detailRetry = vi.fn();
  vi.mocked(useMindMap).mockReturnValue({
    data: { ...current, id: "different-map", title: "Wrong curriculum" },
    isLoading: false, isError: false, refetch: detailRetry,
  } as unknown as ReturnType<typeof useMindMap>);
  selected.rerender(<MemoryRouter><EducationPage /></MemoryRouter>);
  expect(screen.getByTestId("education-error")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Re-activate" })).toBeNull();
  expect(screen.queryByText("Wrong curriculum")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Retry" }));
  expect(detailRetry).toHaveBeenCalledOnce();

  // A background error retains an identity-matched last-good detail while
  // naming its degraded source; successful recovery clears that notice.
  vi.mocked(useMindMap).mockReturnValue({ data: current, isLoading: false, isError: true, refetch: detailRetry } as unknown as ReturnType<typeof useMindMap>);
  selected.rerender(<MemoryRouter><EducationPage /></MemoryRouter>);
  expect(screen.getByText("abandoned")).toBeTruthy();
  expect(screen.getByRole("alert").textContent).toContain("Selected curriculum");
  vi.mocked(useMindMap).mockReturnValue({ data: current, isLoading: false, isError: false, refetch: detailRetry } as unknown as ReturnType<typeof useMindMap>);
  selected.rerender(<MemoryRouter><EducationPage /></MemoryRouter>);
  expect(screen.queryByRole("alert")).toBeNull();
  selected.unmount();
});
