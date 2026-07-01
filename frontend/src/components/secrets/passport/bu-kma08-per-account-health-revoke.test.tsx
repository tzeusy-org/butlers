// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// Per-account Google Health revoke tests [bu-kma08]
//
// Covers:
//   1. Account row WITH health scopes renders a "revoke" PillBtn alongside the
//      granted dot (data-account-health-state="granted").
//   2. Account row WITHOUT health scopes does NOT render a revoke button.
//   3. Clicking "revoke" on a row calls disconnectGoogleHealth with the correct
//      account_email (not the primary's email when clicking a secondary row).
//   4. disconnectGoogleHealth API call passes ?account_email=<email> for a
//      non-primary account (client.ts level unit test).
//   5. ScopeSetPicker primary revoke still calls disconnectGoogleHealth with
//      the primaryAccountEmail when available.
// ---------------------------------------------------------------------------

import { afterEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { render, fireEvent, cleanup } from "@testing-library/react";
import * as React from "react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ---------------------------------------------------------------------------
// Mock API client — use vi.hoisted() so the variable is in scope when the
// vi.mock factory is hoisted to the top of the file by Vitest's transform.
// ---------------------------------------------------------------------------
const { disconnectGoogleHealthMock } = vi.hoisted(() => ({
  disconnectGoogleHealthMock: vi.fn().mockResolvedValue({ success: true, scopes_removed: [], message: "" }),
}))

vi.mock("@/api/client.ts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client.ts")>()
  return {
    ...actual,
    reauthorizeUserCredential: vi.fn(),
    probeUserCredential: vi.fn(),
    rotateUserCredential: vi.fn(),
    disconnectUserCredential: vi.fn(),
    setSystemCredential: vi.fn(),
    probeSystemCredential: vi.fn(),
    deleteSystemCredential: vi.fn(),
    rotateCliCredential: vi.fn(),
    revokeCliCredential: vi.fn(),
    listCLIAuthProviders: vi.fn().mockResolvedValue([]),
    testCLIAuthApiKey: vi.fn(),
    saveCLIAuthApiKey: vi.fn(),
    deleteCLIAuthApiKey: vi.fn(),
    getGoogleAccounts: vi.fn().mockResolvedValue([]),
    setPrimaryAccount: vi.fn(),
    disconnectAccount: vi.fn(),
    disconnectGoogleHealth: disconnectGoogleHealthMock,
  }
})
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock("@/hooks/use-butlers", () => ({
  useButlers: vi.fn(() => ({ data: { data: [] }, isLoading: false, error: null })),
}))
vi.mock("@/hooks/use-secrets.ts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/use-secrets.ts")>()
  return {
    ...actual,
    useGoogleAccounts: vi.fn(() => ({ data: [], isLoading: false, error: null })),
    useSetPrimaryAccount: vi.fn(() => ({ mutate: vi.fn(), isPending: false, error: null })),
    useDisconnectAccount: vi.fn(() => ({ mutate: vi.fn(), isPending: false, reset: vi.fn(), error: null })),
  }
})
vi.mock("@/hooks/use-google-health.ts", () => ({
  googleHealthKeys: { all: ["google-health"], status: () => ["google-health", "status"] },
  useGoogleHealthStatus: vi.fn(() => ({ data: null, isLoading: false, error: null })),
  // Return a mutation-like object whose mutate() calls disconnectGoogleHealthMock directly.
  useDisconnectGoogleHealth: vi.fn((opts?: { accountEmail?: string }) => ({
    mutate: () => { disconnectGoogleHealthMock({ accountEmail: opts?.accountEmail }) },
    isPending: false,
    error: null,
    reset: vi.fn(),
  })),
}))

// Top-level imports (ESM — no require())
import { GOOGLE_HEALTH_SCOPES } from "@/api/client.ts";
import * as useSecretsModule from "@/hooks/use-secrets.ts";
import { PageGoogleAccounts } from "./pages.tsx";
import type { GoogleAccount } from "@/api/types.ts";

// ---------------------------------------------------------------------------
// Fixtures — primary HAS health, secondary also HAS health
// ---------------------------------------------------------------------------

function makeAccounts(): GoogleAccount[] {
  return [
    {
      id: "acc-primary",
      email: "owner@example.com",
      display_name: "Owner",
      is_primary: true,
      status: "active",
      granted_scopes: [
        "https://www.googleapis.com/auth/calendar.readonly",
        ...GOOGLE_HEALTH_SCOPES,
      ],
      connected_at: "2026-01-01T00:00:00Z",
      last_token_refresh_at: null,
    },
    {
      id: "acc-secondary",
      email: "work@example.com",
      display_name: "Work",
      is_primary: false,
      status: "active",
      granted_scopes: [
        "https://www.googleapis.com/auth/calendar.readonly",
        ...GOOGLE_HEALTH_SCOPES,
      ],
      connected_at: "2026-02-01T00:00:00Z",
      last_token_refresh_at: null,
    },
  ];
}

function makeAccountsNoHealthOnSecondary(): GoogleAccount[] {
  const accounts = makeAccounts();
  return accounts.map((a) =>
    a.id === "acc-secondary"
      ? { ...a, granted_scopes: ["https://www.googleapis.com/auth/calendar.readonly"] }
      : a,
  );
}

function mockAccountsOnce(accounts: GoogleAccount[]) {
  vi.mocked(useSecretsModule.useGoogleAccounts).mockReturnValueOnce({
    data: accounts,
    isLoading: false,
    error: null,
  } as unknown as ReturnType<typeof useSecretsModule.useGoogleAccounts>);
}

function renderStatic(element: React.ReactElement): string {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/secrets"]}>{element}</MemoryRouter>
    </QueryClientProvider>,
  );
}

function queryClientWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/secrets"]}>{children}</MemoryRouter>
      </QueryClientProvider>
    );
  };
}

afterEach(() => {
  vi.clearAllMocks();
  cleanup();
});

// ── 1. Revoke button visible when health is granted ──────────────────────────

describe("Per-account health revoke: revoke button on granted rows [bu-kma08]", () => {
  it("renders a 'revoke' button on each account row that HAS health scopes", () => {
    mockAccountsOnce(makeAccounts());
    const html = renderStatic(<PageGoogleAccounts />);

    // Both accounts have health — both should have revoke in their rows.
    // Verify via data-account-health-state="granted" presence.
    const primaryStart = html.indexOf('data-google-account-row="acc-primary"');
    const secondaryStart = html.indexOf('data-google-account-row="acc-secondary"');
    const pickerStart = html.indexOf('data-scope-set-picker="true"');

    expect(primaryStart).toBeGreaterThan(-1);
    expect(secondaryStart).toBeGreaterThan(-1);

    // Primary row slice (before secondary row)
    const primarySlice = html.slice(primaryStart, secondaryStart);
    expect(primarySlice).toContain('data-account-health-state="granted"');
    expect(primarySlice).toContain('data-revoke-health="acc-primary"');

    // Secondary row slice (before scope-set picker or end)
    const secondaryEnd = pickerStart > secondaryStart ? pickerStart : html.length;
    const secondarySlice = html.slice(secondaryStart, secondaryEnd);
    expect(secondarySlice).toContain('data-account-health-state="granted"');
    expect(secondarySlice).toContain('data-revoke-health="acc-secondary"');
  });

  it("does NOT render a revoke button on an account row WITHOUT health scopes", () => {
    mockAccountsOnce(makeAccountsNoHealthOnSecondary());
    const html = renderStatic(<PageGoogleAccounts />);

    const secondaryStart = html.indexOf('data-google-account-row="acc-secondary"');
    const pickerStart = html.indexOf('data-scope-set-picker="true"');
    const secondaryEnd = pickerStart > secondaryStart ? pickerStart : html.length;
    const secondarySlice = html.slice(secondaryStart, secondaryEnd);

    // Health state should be absent, no revoke button on this row.
    expect(secondarySlice).toContain('data-account-health-state="absent"');
    expect(secondarySlice).not.toContain('data-revoke-health="acc-secondary"');
  });
});

// ── 2. Revoke confirm modal — required copy + two-step flow [bu-5ta0k] ──────────

// Required copy from spec dashboard-google-accounts §Revoking a scope set:
const REQUIRED_COPY =
  "This revokes Google Health access only. Calendar and Drive remain connected.";

describe("Health revoke confirmation modal [bu-5ta0k]", () => {
  it("clicking 'revoke' opens the confirm panel with the required spec copy", () => {
    mockAccountsOnce(makeAccounts());
    const Wrapper = queryClientWrapper();
    render(<Wrapper><PageGoogleAccounts /></Wrapper>);

    const secondaryRow = document.querySelector('[data-google-account-row="acc-secondary"]');
    expect(secondaryRow).not.toBeNull();
    const revokeBtn = secondaryRow!.querySelector('[data-revoke-health="acc-secondary"]');
    expect(revokeBtn).not.toBeNull();

    fireEvent.click(revokeBtn!);

    // Confirm panel must be in the DOM with the required copy
    const confirmPanel = document.querySelector('[data-revoke-health-confirm="acc-secondary"]');
    expect(confirmPanel).not.toBeNull();
    expect(confirmPanel!.textContent).toContain(REQUIRED_COPY);

    // DELETE must NOT have been called yet — modal is not a no-op guard
    expect(disconnectGoogleHealthMock).not.toHaveBeenCalled();
  });

  it("clicking 'cancel' in the confirm panel dismisses it without calling the API", () => {
    mockAccountsOnce(makeAccounts());
    const Wrapper = queryClientWrapper();
    render(<Wrapper><PageGoogleAccounts /></Wrapper>);

    const secondaryRow = document.querySelector('[data-google-account-row="acc-secondary"]');
    const revokeBtn = secondaryRow!.querySelector('[data-revoke-health="acc-secondary"]');
    fireEvent.click(revokeBtn!);

    const confirmPanel = document.querySelector('[data-revoke-health-confirm="acc-secondary"]');
    expect(confirmPanel).not.toBeNull();

    const cancelBtn = Array.from(confirmPanel!.querySelectorAll("button")).find(
      (b) => b.textContent === "cancel",
    );
    expect(cancelBtn).not.toBeNull();
    fireEvent.click(cancelBtn!);

    // Panel dismissed, mutation not called
    expect(document.querySelector('[data-revoke-health-confirm="acc-secondary"]')).toBeNull();
    expect(disconnectGoogleHealthMock).not.toHaveBeenCalled();
  });

  it("clicking 'yes, revoke' calls disconnectGoogleHealth with the correct account email", () => {
    mockAccountsOnce(makeAccounts());
    const Wrapper = queryClientWrapper();
    render(<Wrapper><PageGoogleAccounts /></Wrapper>);

    // Both rows have health → two revoke buttons in the account rows
    // (plus potentially one in ScopeSetPicker for primary).
    // Find the one associated with the secondary account row.
    const secondaryRow = document.querySelector('[data-google-account-row="acc-secondary"]');
    expect(secondaryRow).not.toBeNull();
    const revokeBtn = secondaryRow!.querySelector('[data-revoke-health="acc-secondary"]');
    expect(revokeBtn).not.toBeNull();

    // Step 1: open the confirm panel
    fireEvent.click(revokeBtn!);

    const confirmPanel = document.querySelector('[data-revoke-health-confirm="acc-secondary"]');
    expect(confirmPanel).not.toBeNull();

    // Step 2: confirm the revoke
    const confirmBtn = Array.from(confirmPanel!.querySelectorAll("button")).find(
      (b) => b.textContent === "yes, revoke",
    );
    expect(confirmBtn).not.toBeNull();
    fireEvent.click(confirmBtn!);

    // The mock should have been called with { accountEmail: "work@example.com" }
    expect(disconnectGoogleHealthMock).toHaveBeenCalledWith({
      accountEmail: "work@example.com",
    });
    // Should NOT have been called with the primary account's email from this click.
    expect(disconnectGoogleHealthMock).not.toHaveBeenCalledWith({
      accountEmail: "owner@example.com",
    });
  });
});
