// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// Per-account Google Health grant tests [bu-kg2nl]
//
// Covers:
//   1. Account row WITHOUT health scopes renders a "grant health" PillBtn
//      (data-account-health-state="absent").
//   2. Account row WITH health scopes renders the granted dot state
//      (data-account-health-state="granted") and no grant button.
//   3. Clicking "grant health" navigates to the OAuth start URL carrying
//      scope_set=health + account_hint=<THAT account's email> + force_consent.
//   4. ScopeSetPicker health row: revoke (primary-only backend) stays in the
//      picker when the primary has health; when no health is granted the
//      picker shows the per-account hint instead of a primary-only grant CTA.
//
// Spec: bu-kg2nl — per-account Health grant in the secrets passport
// Mock pattern mirrors bu-3gekd-health-grant.test.tsx.
// ---------------------------------------------------------------------------

import { afterEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import * as React from "react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ---------------------------------------------------------------------------
// Mock API client — use actual getGoogleOAuthStartUrl for URL assertion
// ---------------------------------------------------------------------------
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
    disconnectGoogleHealth: vi.fn(),
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
vi.mock("@/hooks/use-google-health.ts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/use-google-health.ts")>()
  return {
    ...actual,
    useGoogleHealthStatus: vi.fn(() => ({ data: null, isLoading: false, error: null })),
    useDisconnectGoogleHealth: vi.fn(() => ({ mutate: vi.fn(), isPending: false, reset: vi.fn(), error: null })),
  }
})

// Top-level imports (ESM — no require())
import { GOOGLE_HEALTH_SCOPES } from "@/api/client.ts";
import * as useSecretsModule from "@/hooks/use-secrets.ts";
import { PageGoogleAccounts } from "./pages.tsx";
import type { GoogleAccount } from "@/api/types.ts";

// ---------------------------------------------------------------------------
// Fixtures — live topology: primary HAS health, secondary does NOT
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
      granted_scopes: ["https://www.googleapis.com/auth/calendar.readonly"],
      connected_at: "2026-02-01T00:00:00Z",
      last_token_refresh_at: null,
    },
  ];
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

/** Slice the rendered html down to a single account row's markup. */
function rowSlice(html: string, accountId: string): string {
  const start = html.indexOf(`data-google-account-row="${accountId}"`);
  expect(start).toBeGreaterThan(-1);
  const rest = html.slice(start + 1);
  const nextRow = rest.indexOf("data-google-account-row=");
  return nextRow >= 0 ? rest.slice(0, nextRow) : rest;
}

afterEach(() => {
  vi.clearAllMocks();
  cleanup();
});

// ── 1+2. Per-account grant state rendering ───────────────────────────────────

describe("Per-account health grant state on account rows [bu-kg2nl]", () => {
  it("renders 'grant health' on the row of an account WITHOUT health scopes", () => {
    mockAccountsOnce(makeAccounts());
    const html = renderStatic(<PageGoogleAccounts />);

    const secondary = rowSlice(html, "acc-secondary");
    expect(secondary).toContain('data-account-health-state="absent"');
    expect(secondary).toContain("grant health");
  });

  it("renders granted dot state on the row of an account WITH health scopes", () => {
    mockAccountsOnce(makeAccounts());
    const html = renderStatic(<PageGoogleAccounts />);

    const primary = rowSlice(html, "acc-primary");
    expect(primary).toContain('data-account-health-state="granted"');
    expect(primary).not.toContain("grant health");
  });

  it("renders an independent health control per account (both attrs present)", () => {
    mockAccountsOnce(makeAccounts());
    const html = renderStatic(<PageGoogleAccounts />);

    expect(html).toContain('data-account-health="acc-primary"');
    expect(html).toContain('data-account-health="acc-secondary"');
  });
});

// ── 3. Grant navigation carries the row account's account_hint ───────────────

describe("Per-account 'grant health' navigates with THAT account's account_hint [bu-kg2nl]", () => {
  it("clicking 'grant health' on the secondary row builds the OAuth URL for the secondary account", () => {
    // Patch window.location (jsdom won't actually navigate)
    const locationDescriptor = Object.getOwnPropertyDescriptor(window, "location");
    const assignSpy = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...window.location, assign: assignSpy },
    });

    try {
      mockAccountsOnce(makeAccounts());
      const client = new QueryClient({
        defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
      });
      render(
        <QueryClientProvider client={client}>
          <MemoryRouter initialEntries={["/secrets"]}>
            <PageGoogleAccounts />
          </MemoryRouter>
        </QueryClientProvider>,
      );

      // Only the secondary account lacks health → exactly one grant button.
      const [btn] = screen.getAllByText("grant health");
      fireEvent.click(btn);

      expect(assignSpy).toHaveBeenCalledOnce();
      const url = assignSpy.mock.calls[0][0] as string;
      const params = new URLSearchParams(url.slice(url.indexOf("?") + 1));
      expect(params.get("scope_set")).toBe("health");
      expect(params.get("account_hint")).toBe("work@example.com");
      expect(params.get("force_consent")).toBe("true");
      expect(params.get("page_of_origin")).toBe("secrets");
    } finally {
      if (locationDescriptor) {
        Object.defineProperty(window, "location", locationDescriptor);
      }
    }
  });
});

// ── 4. ScopeSetPicker health row: revoke stays primary-only ─────────────────

describe("ScopeSetPicker health row after per-account grant move [bu-kg2nl]", () => {
  it("keeps revoke (primary) in the picker when the primary has health scopes", () => {
    mockAccountsOnce(makeAccounts());
    const html = renderStatic(<PageGoogleAccounts />);

    const picker = html.slice(html.indexOf('data-scope-set-picker="true"'));
    expect(picker).toContain("revoke");
    expect(picker).toContain("primary");
    // No primary-only health grant CTA inside the picker
    expect(picker).not.toContain("grant per account above");
  });

  it("shows the per-account hint in the picker when NO health is granted anywhere", () => {
    const accounts = makeAccounts().map((a) => ({
      ...a,
      granted_scopes: a.granted_scopes.filter(
        (s) => !(GOOGLE_HEALTH_SCOPES as readonly string[]).includes(s),
      ),
    }));
    mockAccountsOnce(accounts);
    const html = renderStatic(<PageGoogleAccounts />);

    const picker = html.slice(html.indexOf('data-scope-set-picker="true"'));
    expect(picker).toContain("grant per account above");
    expect(picker).not.toContain("revoke");
    // Calendar / Drive grants remain primary-targeted in the picker
    expect(picker).toContain("grant");
  });
});
