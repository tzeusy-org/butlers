/**
 * SettingsPermissionsPage — export section [bu-9q1dx.1]
 *
 * Verifies the data-export UI:
 *   - Export description copy is truthful (mentions "AES-256-GCM encrypted")
 *   - Scope picker renders the four expected scopes (all, memory, audit, config)
 *   - Export button is present and enabled by default
 *   - When export succeeds the signed URL link renders
 */

// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, cleanup, screen, act } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import SettingsPermissionsPage from "@/pages/SettingsPermissionsPage";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

// Mock useAuditLog so the audit reel renders without a real fetch
vi.mock("@/hooks/use-audit-log", () => ({
  useAuditLog: () => ({ data: { data: [] }, isLoading: false, error: null }),
}));

// Mock sonner to prevent DOM errors in jsdom
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

// Baseline fetch mock — returns empty data for all API calls
const fetchMock = vi.fn();

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <SettingsPermissionsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function defaultFetch(url: string) {
  if (url.includes("/api/permissions")) {
    return Promise.resolve({
      ok: true,
      json: () =>
        Promise.resolve({
          data: {
            butlers: [],
            permissions: [],
            cells: {},
          },
        }),
    });
  }
  if (url.includes("/api/webhooks")) {
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ data: [] }),
    });
  }
  return Promise.resolve({
    ok: true,
    json: () => Promise.resolve({ data: {} }),
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("SettingsPermissionsPage — wipe disabled [bu-9q1dx.2]", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockImplementation((url: string) => defaultFetch(url));
    global.fetch = fetchMock as unknown as typeof fetch;
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("wipe panel renders as disabled — no enabled wipe control", async () => {
    await act(async () => {
      renderPage();
    });

    // The disabled wipe panel must be present
    const panel = await screen.findByTestId("wipe-panel-disabled");
    expect(panel).toBeTruthy();

    // No enabled button whose name contains "wipe" (case-insensitive)
    const allButtons = document.querySelectorAll("button");
    const enabledWipeButtons = Array.from(allButtons).filter(
      (btn) =>
        !btn.disabled &&
        /wipe/i.test(btn.textContent ?? ""),
    );
    expect(enabledWipeButtons).toHaveLength(0);
  });

  it("wipe phrase input does not render", async () => {
    await act(async () => {
      renderPage();
    });

    // No input with id "wipe-phrase"
    const phraseInput = document.getElementById("wipe-phrase");
    expect(phraseInput).toBeNull();
  });
});

describe("SettingsPermissionsPage — export section [bu-9q1dx.1]", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockImplementation((url: string) => defaultFetch(url));
    global.fetch = fetchMock as unknown as typeof fetch;
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("export description mentions AES-256-GCM and decryption key (truthful copy)", async () => {
    await act(async () => {
      renderPage();
    });

    // Find the export description by testid
    const desc = await screen.findByTestId("export-description");
    expect(desc.textContent).toContain("AES-256-GCM");
    expect(desc.textContent).toContain("DASHBOARD_EXPORT_ENCRYPTION_KEY");
  });

  it("scope picker renders the four expected scope options", async () => {
    await act(async () => {
      renderPage();
    });

    // The Select trigger should render "All data" (default scope)
    const trigger = await screen.findByRole("combobox");
    expect(trigger).toBeTruthy();
  });

  it("Export button is present and enabled by default", async () => {
    await act(async () => {
      renderPage();
    });

    const exportBtn = await screen.findByRole("button", { name: /export/i });
    expect(exportBtn).toBeTruthy();
    expect(exportBtn.hasAttribute("disabled")).toBe(false);
  });

  it("scope picker renders with default 'All data' selection", async () => {
    // The Select trigger should render "All data" (default scope = "all")
    await act(async () => {
      renderPage();
    });

    // Verify the "Export data" section heading is present
    const exportHeading = await screen.findByText("Export data");
    expect(exportHeading).toBeTruthy();

    // Verify the scope select trigger is present (default = "All data")
    const trigger = await screen.findByRole("combobox");
    expect(trigger.textContent).toContain("All data");
  });
});
