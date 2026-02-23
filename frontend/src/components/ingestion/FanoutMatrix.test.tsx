// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

import { FanoutMatrix } from "./FanoutMatrix";
import type { ConnectorFanout } from "@/api/index.ts";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const MOCK_FANOUT: ConnectorFanout = {
  period: "7d",
  matrix: [
    {
      connector_type: "gmail",
      endpoint_identity: "user@example.com",
      targets: { finance: 100, health: 50 },
    },
    {
      connector_type: "telegram",
      endpoint_identity: "bot-123",
      targets: { health: 200, finance: 30 },
    },
  ],
};

describe("FanoutMatrix", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it("renders table headers for each butler", () => {
    act(() => {
      root.render(<FanoutMatrix fanout={MOCK_FANOUT} isLoading={false} />);
    });
    expect(container.textContent).toContain("finance");
    expect(container.textContent).toContain("health");
  });

  it("renders connector rows", () => {
    act(() => {
      root.render(<FanoutMatrix fanout={MOCK_FANOUT} isLoading={false} />);
    });
    expect(container.textContent).toContain("gmail:user@example.com");
    expect(container.textContent).toContain("telegram:bot-123");
  });

  it("renders message counts in cells", () => {
    act(() => {
      root.render(<FanoutMatrix fanout={MOCK_FANOUT} isLoading={false} />);
    });
    expect(container.textContent).toContain("100");
    expect(container.textContent).toContain("200");
  });

  it("renders empty state when no matrix rows", () => {
    act(() => {
      root.render(
        <FanoutMatrix
          fanout={{ period: "7d", matrix: [] }}
          isLoading={false}
        />,
      );
    });
    expect(container.textContent).toContain("No fanout data available");
  });

  it("renders skeleton when loading", () => {
    act(() => {
      root.render(<FanoutMatrix fanout={undefined} isLoading={true} />);
    });
    // Should not show empty state
    expect(container.textContent).not.toContain("No fanout data available");
  });
});
