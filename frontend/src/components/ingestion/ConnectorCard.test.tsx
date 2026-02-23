// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";

import { ConnectorCard } from "./ConnectorCard";
import type { ConnectorSummary } from "@/api/index.ts";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

const MOCK_CONNECTOR: ConnectorSummary = {
  connector_type: "gmail",
  endpoint_identity: "user@example.com",
  liveness: "online",
  state: "healthy",
  error_message: null,
  version: "1.0",
  uptime_s: 3600,
  last_heartbeat_at: new Date(Date.now() - 60_000).toISOString(),
  first_seen_at: "2026-01-01T00:00:00Z",
  today: { messages_ingested: 42, messages_failed: 1, uptime_pct: 99.5 },
};

describe("ConnectorCard", () => {
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

  function render(
    connector: ConnectorSummary = MOCK_CONNECTOR,
    hasActiveBackfill = false,
  ) {
    act(() => {
      root.render(
        <MemoryRouter>
          <ConnectorCard
            connector={connector}
            hasActiveBackfill={hasActiveBackfill}
          />
        </MemoryRouter>,
      );
    });
  }

  it("renders connector type and identity", () => {
    render();
    expect(container.textContent).toContain("gmail");
    expect(container.textContent).toContain("user@example.com");
  });

  it("renders liveness badge", () => {
    render();
    expect(container.textContent).toContain("online");
  });

  it("renders today ingested count", () => {
    render();
    expect(container.textContent).toContain("42");
  });

  it("shows backfill active badge when hasActiveBackfill is true", () => {
    render(MOCK_CONNECTOR, true);
    expect(container.textContent).toContain("backfill active");
  });

  it("does not show backfill badge when hasActiveBackfill is false", () => {
    render(MOCK_CONNECTOR, false);
    expect(container.textContent).not.toContain("backfill active");
  });

  it("renders degraded state badge", () => {
    render({ ...MOCK_CONNECTOR, state: "degraded" });
    expect(container.textContent).toContain("degraded");
  });
});
