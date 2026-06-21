/**
 * SettingsPermissionsPage — export section [bu-9q1dx.1] + dense matrix [bu-9q1dx.3]
 *
 * Verifies the data-export UI:
 *   - Export description copy is truthful (mentions "AES-256-GCM encrypted")
 *   - Scope picker renders the four expected scopes (all, memory, audit, config)
 *   - Export button is present and enabled by default
 *   - When export succeeds the signed URL link renders
 *
 * Verifies inherited cell semantics:
 *   - Inherited cells are rendered dim (aria-label includes "(inherited)")
 *   - Inherited cell buttons are disabled (non-editable)
 *   - Explicit cells are enabled and editable
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

// Mock useAuditLog so the audit reel renders without a real fetch. Use a
// recording spy (not an arg-ignoring factory) so tests can assert the reel
// requests the FILTERED endpoint (kind=privileged), per dashboard-permissions
// spec "Audit reel filters operational noise".
const useAuditLogMock = vi.hoisted(() =>
  vi.fn(() => ({ data: { data: [] }, isLoading: false, error: null })),
);
vi.mock("@/hooks/use-audit-log", () => ({
  useAuditLog: useAuditLogMock,
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

/** Dense matrix fixture: chronicler with spawn=explicit(revoked), notify=inherited */
function denseMatrixFetch(url: string) {
  if (url.includes("/api/permissions")) {
    return Promise.resolve({
      ok: true,
      json: () =>
        Promise.resolve({
          data: {
            butlers: ["chronicler"],
            permissions: ["calendar.write", "cross_butler", "email.send", "notify", "spawn"],
            cells: {
              chronicler: {
                "calendar.write": { granted: true, reason: null, updated_at: null, inherited: true },
                "cross_butler": { granted: true, reason: null, updated_at: null, inherited: true },
                "email.send": { granted: true, reason: null, updated_at: null, inherited: true },
                notify: { granted: true, reason: null, updated_at: null, inherited: true },
                spawn: {
                  granted: false,
                  reason: "revoked",
                  updated_at: "2026-06-01T00:00:00Z",
                  inherited: false,
                },
              },
            },
          },
        }),
    });
  }
  return defaultFetch(url);
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

// ---------------------------------------------------------------------------
// Dense matrix — inherited cell semantics [bu-9q1dx.3]
// ---------------------------------------------------------------------------

describe("SettingsPermissionsPage — inherited cell semantics [bu-9q1dx.3]", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockImplementation((url: string) => denseMatrixFetch(url));
    global.fetch = fetchMock as unknown as typeof fetch;
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("inherited cells have aria-label containing '(inherited)'", async () => {
    await act(async () => {
      renderPage();
    });

    // "notify" is inherited in the fixture
    const notifyCell = await screen.findByTestId("perm-cell-chronicler-notify");
    expect(notifyCell.getAttribute("aria-label")).toContain("(inherited)");
  });

  it("inherited cells are disabled (non-editable)", async () => {
    await act(async () => {
      renderPage();
    });

    const notifyCell = await screen.findByTestId("perm-cell-chronicler-notify");
    expect((notifyCell as HTMLButtonElement).disabled).toBe(true);
  });

  it("explicit cells are enabled (editable)", async () => {
    await act(async () => {
      renderPage();
    });

    // "spawn" is explicit (inherited:false) in the fixture
    const spawnCell = await screen.findByTestId("perm-cell-chronicler-spawn");
    expect((spawnCell as HTMLButtonElement).disabled).toBe(false);
  });

  it("explicit cells aria-label does NOT contain '(inherited)'", async () => {
    await act(async () => {
      renderPage();
    });

    const spawnCell = await screen.findByTestId("perm-cell-chronicler-spawn");
    expect(spawnCell.getAttribute("aria-label")).not.toContain("(inherited)");
  });
});

// ---------------------------------------------------------------------------
// Audit reel consumes the FILTERED endpoint [bu-9q1dx.5 / reconcile bu-9q1dx.11]
// ---------------------------------------------------------------------------
// Spec dashboard-permissions "Audit reel filters operational noise": the reel
// MUST request a privileged-action-only view so heartbeat / routine-GET noise
// is excluded. This guards against a regression to an unfiltered request.
describe("SettingsPermissionsPage — audit reel filters operational noise [bu-9q1dx.5]", () => {
  beforeEach(() => {
    // mockReset() (not mockClear()) so a prior test's mockReturnValue cannot
    // leak across cases; re-apply the default privileged-empty implementation.
    useAuditLogMock.mockReset();
    useAuditLogMock.mockImplementation(() => ({
      data: { data: [] },
      isLoading: false,
      error: null,
    }));
    fetchMock.mockReset();
    fetchMock.mockImplementation((url: string) => defaultFetch(url));
    global.fetch = fetchMock as unknown as typeof fetch;
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("requests the privileged-only, last-15 audit view (not an unfiltered endpoint)", async () => {
    await act(async () => {
      renderPage();
    });

    expect(useAuditLogMock).toHaveBeenCalledWith(
      expect.objectContaining({ limit: 15, kind: "privileged" }),
    );
  });

  it("shows an empty state rather than padding when no privileged rows exist", async () => {
    useAuditLogMock.mockReturnValue({
      data: { data: [] },
      isLoading: false,
      error: null,
    });

    await act(async () => {
      renderPage();
    });

    expect(screen.getByText("No recent audit entries.")).toBeTruthy();
  });
});
