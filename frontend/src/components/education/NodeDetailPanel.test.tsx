// @vitest-environment jsdom

import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("@/hooks/use-education", () => ({
  useMindMap: vi.fn(),
}));

vi.mock("./QuizHistoryList", () => ({ default: () => null }));

import { useMindMap } from "@/hooks/use-education";
import NodeDetailPanel from "./NodeDetailPanel";

const mockUseMindMap = vi.mocked(useMindMap);

beforeEach(() => {
  vi.clearAllMocks();
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
