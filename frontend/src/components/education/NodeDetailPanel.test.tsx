// @vitest-environment jsdom

import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("@/hooks/use-education", () => ({
  useMindMap: vi.fn(),
  useEducationSources: vi.fn(),
}));

vi.mock("./QuizHistoryList", () => ({ default: () => null }));

import { useEducationSources, useMindMap } from "@/hooks/use-education";
import NodeDetailPanel from "./NodeDetailPanel";

const mockUseMindMap = vi.mocked(useMindMap);
const mockUseEducationSources = vi.mocked(useEducationSources);

/** Default registry outcome: resolved, holding whatever sources are passed. */
function registryResolved(sources: unknown[] = []) {
  return { data: sources, isLoading: false, isError: false } as unknown as ReturnType<
    typeof useEducationSources
  >;
}

beforeEach(() => {
  vi.clearAllMocks();
  mockUseEducationSources.mockReturnValue(registryResolved());
});

afterEach(cleanup);

describe("NodeDetailPanel selection guard", () => {
  it("renders nothing when the selected node is absent from the selected map", () => {
    mockUseMindMap.mockReturnValue({
      data: {
        nodes: [{ id: "node-on-map-a", label: "Map A concept" }],
      },
    } as unknown as ReturnType<typeof useMindMap>);

    const { container } = render(
      <NodeDetailPanel
        mindMapId="map-a"
        nodeId="node-on-map-b"
        onClose={vi.fn()}
      />,
    );

    expect(container.childElementCount).toBe(0);
    expect(screen.queryByText("Map A concept")).toBeNull();
    expect(screen.queryByText("Click a node to view details")).toBeNull();
  });
});

describe("NodeDetailPanel focus choreography (bu-x7syp)", () => {
  const NODE = {
    id: "node-1",
    label: "Photosynthesis",
    mastery_status: "learning",
    mastery_score: 0.4,
    ease_factor: 2.3,
    repetitions: 2,
  };

  beforeEach(() => {
    mockUseMindMap.mockReturnValue({
      data: { nodes: [NODE] },
    } as unknown as ReturnType<typeof useMindMap>);
  });

  // Mirrors how EducationPage actually mounts the panel: a trigger control
  // (ReviewEntryRow / MindMapGraph node / StrugglingNodesCard row) sets
  // selection state, and the panel is conditionally rendered below it —
  // unmounting entirely on close rather than toggling its own visibility.
  function Harness() {
    const [open, setOpen] = useState(false);
    return (
      <div>
        <button type="button" onClick={() => setOpen(true)}>
          Open node
        </button>
        {open && (
          <NodeDetailPanel
            mindMapId="map-a"
            nodeId="node-1"
            onClose={() => setOpen(false)}
          />
        )}
      </div>
    );
  }

  it("moves focus into the panel via an accessible heading naming the node when it opens", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(screen.getByRole("button", { name: "Open node" }));

    const heading = screen.getByRole("heading", {
      level: 2,
      name: "Node details: Photosynthesis",
    });
    expect(document.activeElement).toBe(heading);
  });

  it("returns focus to the triggering control when the panel closes", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    const openButton = screen.getByRole("button", { name: "Open node" });
    await user.click(openButton);
    expect(document.activeElement).toBe(
      screen.getByRole("heading", { level: 2, name: "Node details: Photosynthesis" }),
    );

    await user.click(screen.getByRole("button", { name: "Close node details" }));

    expect(screen.queryByRole("heading", { level: 2 })).toBeNull();
    expect(document.activeElement).toBe(openButton);
  });

  it("returns focus to the trigger when Escape is pressed inside the panel", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    const openButton = screen.getByRole("button", { name: "Open node" });
    await user.click(openButton);

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("heading", { level: 2 })).toBeNull();
    expect(document.activeElement).toBe(openButton);
  });
});

describe("NodeDetailPanel source annotations and concept type (bu-istke.5)", () => {
  const SOURCE = {
    source_id: "src-1",
    title: "Structure and Interpretation of Computer Programs",
    authors: ["Harold Abelson"],
    type: "book",
    url: "https://example.test/sicp",
    registered_at: "2026-08-21T00:00:00+00:00",
  };

  function mountNode(metadata: Record<string, unknown>) {
    mockUseMindMap.mockReturnValue({
      data: {
        nodes: [
          {
            id: "node-1",
            label: "Recursion",
            mastery_status: "learning",
            mastery_score: 0.4,
            ease_factor: 2.3,
            repetitions: 2,
            metadata,
          },
        ],
      },
    } as unknown as ReturnType<typeof useMindMap>);

    return render(
      <NodeDetailPanel mindMapId="map-a" nodeId="node-1" onClose={vi.fn()} />,
    );
  }

  it("renders a registered, source-read ref as a citation with its title and link", () => {
    mockUseEducationSources.mockReturnValue(registryResolved([SOURCE]));
    mountNode({
      source_refs: [
        { source_id: "src-1", location: "chapter 1.2", provenance: "referenced" },
      ],
    });

    expect(screen.getByText("Referenced")).toBeTruthy();
    expect(screen.getByText(SOURCE.title)).toBeTruthy();
    expect(screen.getByText("chapter 1.2")).toBeTruthy();
    expect(
      screen.getByRole("link", { name: "Open registered source" }).getAttribute("href"),
    ).toBe("https://example.test/sicp");
  });

  it("labels a model-recalled ref as recalled and withholds the citation link even when its source is registered", () => {
    mockUseEducationSources.mockReturnValue(registryResolved([SOURCE]));
    mountNode({
      source_refs: [
        { source_id: "src-1", location: "chapter 1.2", provenance: "model-recalled" },
      ],
    });

    expect(screen.getByText("Model-recalled")).toBeTruthy();
    expect(screen.queryByText("Referenced")).toBeNull();
    expect(screen.getByText(`Recalled from ${SOURCE.title}`)).toBeTruthy();
    expect(
      screen.getByText(/did not read the source, so treat this location as unverified/),
    ).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Open registered source" })).toBeNull();
  });

  it("labels a ref with no source_id as model-recalled", () => {
    mountNode({
      source_refs: [{ source_id: null, location: "the standard proof" }],
    });

    expect(screen.getByText("Model-recalled")).toBeTruthy();
    expect(screen.getByText("No registered source named")).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("says a dangling ref is no longer registered instead of showing a title", () => {
    mockUseEducationSources.mockReturnValue(registryResolved([]));
    mountNode({
      source_refs: [
        { source_id: "src-removed", location: "chapter 4", provenance: "referenced" },
      ],
    });

    expect(screen.getByText("Source no longer registered")).toBeTruthy();
    expect(screen.queryByText("Referenced")).toBeNull();
    expect(screen.getByText("Reference ID: src-removed")).toBeTruthy();
    expect(screen.getByText(/is not a citation/)).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("declines to classify a ref while the registry is still loading", () => {
    mockUseEducationSources.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
    } as unknown as ReturnType<typeof useEducationSources>);
    mountNode({
      source_refs: [
        { source_id: "src-1", location: "chapter 1.2", provenance: "referenced" },
      ],
    });

    expect(screen.getByText("Not checked against the registry")).toBeTruthy();
    expect(screen.getByText(/Checking this reference against the source registry/)).toBeTruthy();
    expect(screen.queryByText("Source no longer registered")).toBeNull();
    expect(screen.queryByText(SOURCE.title)).toBeNull();
  });

  it("reports an unreachable registry rather than calling the ref unregistered", () => {
    mockUseEducationSources.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    } as unknown as ReturnType<typeof useEducationSources>);
    mountNode({
      source_refs: [
        { source_id: "src-1", location: "chapter 1.2", provenance: "referenced" },
      ],
    });

    expect(screen.getByText("Not checked against the registry")).toBeTruthy();
    expect(screen.getByText(/could not be reached/)).toBeTruthy();
    expect(screen.queryByText("Source no longer registered")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("renders the concept type as a tag", () => {
    mountNode({ concept_type: "procedural" });

    expect(screen.getByLabelText("Concept type: Procedural")).toBeTruthy();
  });

  it("leaves a node with neither annotation unchanged", () => {
    mountNode({});

    expect(screen.queryByText("Sources")).toBeNull();
    expect(screen.queryByText("Model-recalled")).toBeNull();
    expect(screen.queryByText("Referenced")).toBeNull();
    // The pre-existing panel content still renders.
    expect(screen.getByText("Recursion")).toBeTruthy();
    expect(screen.getByText("learning")).toBeTruthy();
  });
});
