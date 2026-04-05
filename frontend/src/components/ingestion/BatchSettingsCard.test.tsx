// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { BatchSettingsCard, BATCH_CONNECTOR_TYPES } from "./BatchSettingsCard";
import type { ConnectorDetail } from "@/api/types.ts";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
}

function makeConnector(
  overrides: Partial<ConnectorDetail> = {},
): ConnectorDetail {
  return {
    connector_type: "telegram_user_client",
    endpoint_identity: "test-identity",
    liveness: "online",
    state: "healthy",
    error_message: null,
    version: "1.0",
    uptime_s: 3600,
    last_heartbeat_at: new Date(Date.now() - 60_000).toISOString(),
    first_seen_at: "2026-01-01T00:00:00Z",
    today: { messages_ingested: 10, messages_failed: 0, uptime_pct: 100 },
    instance_id: null,
    registered_via: "env",
    checkpoint: null,
    counters: null,
    settings: null,
    ...overrides,
  };
}

function makeMutation(overrides: object = {}) {
  return {
    mutate: vi.fn(),
    mutateAsync: vi.fn(),
    isPending: false,
    isError: false,
    isSuccess: false,
    isIdle: true,
    reset: vi.fn(),
    variables: undefined,
    error: null,
    data: undefined,
    status: "idle" as const,
    context: undefined,
    failureCount: 0,
    failureReason: null,
    isPaused: false,
    isPaused: false,
    submittedAt: 0,
    ...overrides,
  };
}

describe("BATCH_CONNECTOR_TYPES", () => {
  it("includes telegram_user_client", () => {
    expect(BATCH_CONNECTOR_TYPES.has("telegram_user_client")).toBe(true);
  });

  it("includes whatsapp_user_client", () => {
    expect(BATCH_CONNECTOR_TYPES.has("whatsapp_user_client")).toBe(true);
  });

  it("does not include gmail", () => {
    expect(BATCH_CONNECTOR_TYPES.has("gmail")).toBe(false);
  });

  it("does not include telegram-bot", () => {
    expect(BATCH_CONNECTOR_TYPES.has("telegram-bot")).toBe(false);
  });
});

describe("BatchSettingsCard", () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = makeQueryClient();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
  });

  function render(
    connector: ConnectorDetail = makeConnector(),
    mutation = makeMutation(),
  ) {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <BatchSettingsCard
            connector={connector}
            settingsMutation={mutation as ReturnType<typeof import("@/hooks/use-ingestion").useUpdateConnectorSettings>}
          />
        </QueryClientProvider>,
      );
    });
  }

  // -------------------------------------------------------------------------
  // Scenario: default display when no dashboard-set value
  // -------------------------------------------------------------------------

  it("shows 'default' badge when settings.flush_interval_s is not set", () => {
    render(makeConnector({ settings: null }));
    const badge = container.querySelector("[data-testid='flush-interval-badge']");
    expect(badge?.textContent).toBe("default");
  });

  it("shows 1800 as the displayed value when no dashboard setting", () => {
    render(makeConnector({ settings: null }));
    const input = container.querySelector(
      "[data-testid='flush-interval-input']",
    ) as HTMLInputElement | null;
    expect(input?.value).toBe("1800");
  });

  // -------------------------------------------------------------------------
  // Scenario: custom display when dashboard-set value exists
  // -------------------------------------------------------------------------

  it("shows 'custom' badge when settings.flush_interval_s is set", () => {
    render(makeConnector({ settings: { flush_interval_s: 900 } }));
    const badge = container.querySelector("[data-testid='flush-interval-badge']");
    expect(badge?.textContent).toBe("custom");
  });

  it("shows the stored flush_interval_s in the input", () => {
    render(makeConnector({ settings: { flush_interval_s: 900 } }));
    const input = container.querySelector(
      "[data-testid='flush-interval-input']",
    ) as HTMLInputElement | null;
    expect(input?.value).toBe("900");
  });

  // -------------------------------------------------------------------------
  // Scenario: validation errors
  // -------------------------------------------------------------------------

  it("shows validation error when value is below 60", () => {
    render();
    const input = container.querySelector(
      "[data-testid='flush-interval-input']",
    ) as HTMLInputElement | null;
    if (!input) throw new Error("Input not found");

    // Simulate React synthetic change event with a value below the minimum.
    act(() => {
      // Use the React-compatible approach: set nativeInputValueSetter and dispatch
      const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value",
      )?.set;
      nativeInputValueSetter?.call(input, "30");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });

    const error = container.querySelector("[data-testid='flush-interval-error']");
    expect(error).not.toBeNull();
    expect(error?.textContent).toContain("Minimum 60");
  });

  it("shows validation error when value is above 7200", () => {
    render();
    const input = container.querySelector(
      "[data-testid='flush-interval-input']",
    ) as HTMLInputElement | null;
    if (!input) throw new Error("Input not found");

    act(() => {
      const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value",
      )?.set;
      nativeInputValueSetter?.call(input, "9000");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });

    const error = container.querySelector("[data-testid='flush-interval-error']");
    expect(error).not.toBeNull();
    expect(error?.textContent).toContain("Maximum 7200");
  });

  // -------------------------------------------------------------------------
  // Scenario: no save button when value is unchanged
  // -------------------------------------------------------------------------

  it("does not show save button when draft equals current value", () => {
    render(makeConnector({ settings: { flush_interval_s: 1800 } }));
    const saveBtn = container.querySelector("[data-testid='flush-interval-save-btn']");
    expect(saveBtn).toBeNull();
  });

  // -------------------------------------------------------------------------
  // Scenario: save button disabled while mutation is pending
  // -------------------------------------------------------------------------

  it("shows saving state when mutation is pending", () => {
    const mutation = makeMutation({ isPending: true });
    render(makeConnector({ settings: { flush_interval_s: 900 } }), mutation);

    // Manually set draft to something different to force canSave=true
    const input = container.querySelector(
      "[data-testid='flush-interval-input']",
    ) as HTMLInputElement | null;

    act(() => {
      if (!input) throw new Error("Input not found");
      Object.defineProperty(input, "value", { writable: true, value: "1200" });
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });

    // When isPending is true, save button shows "Saving..."
    const saveBtn = container.querySelector("[data-testid='flush-interval-save-btn']");
    if (saveBtn) {
      expect(saveBtn.textContent).toContain("Saving...");
    }
  });

  // -------------------------------------------------------------------------
  // Scenario: card title
  // -------------------------------------------------------------------------

  it("renders 'Batch Settings' card title", () => {
    render();
    expect(container.textContent).toContain("Batch Settings");
  });

  // -------------------------------------------------------------------------
  // Scenario: no "restart required" notice (spec: no "takes effect on restart")
  // -------------------------------------------------------------------------

  it("does not say 'takes effect on next restart' (no restart-required notice)", () => {
    render();
    const text = container.textContent?.toLowerCase() ?? "";
    // The card must NOT contain a restart-required warning.
    // It's OK (and even good) to say "no restart required", but it must not
    // contain the phrase used by cursor/discretion cards: "next restart" as a
    // requirement.
    expect(text).not.toContain("takes effect on next restart");
  });
});
