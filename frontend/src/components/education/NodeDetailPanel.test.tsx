// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

vi.mock("@/hooks/use-education", () => ({
  useMindMap: vi.fn(),
}));

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
