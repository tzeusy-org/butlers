// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// Google Health Passport Status Card + Test-Mode Expiry Banner [bu-hh875]
//
// Covers:
//   a. Status card renders with granted health scopes.
//   b. Status card is HIDDEN when primary lacks health scopes.
//   c. Banner is PERSISTENT orange whenever test_mode=true (even fresh token);
//      elevates to red once refresh age crosses 5d6h; carries a scope_set=health
//      re-consent link. [bu-bxu50]
//   d. Banner absent only when test_mode=false.
//
// Spec: bu-hh875 — Google Health connector status card in owner passport view
//       bu-bxu50 — reconcile banner to dashboard-google-accounts spec:
//                  §Test-Mode Pre-Verification Warning
// ---------------------------------------------------------------------------

import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import * as React from "react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import type { GoogleHealthStatusResponse } from "@/api/types.ts";

// ---------------------------------------------------------------------------
// Mocks — mirror the pattern established in bu-3gekd-health-grant.test.tsx
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
vi.mock("@/hooks/use-home-assistant.ts", () => ({
  useHomeAssistantStatus: vi.fn(() => ({
    data: { state: "connected", url_configured: true, token_configured: true, masked_url: "http://ha.local:8123" },
    isLoading: false,
    error: null,
  })),
  useConfigureHomeAssistant: vi.fn(() => ({ mutate: vi.fn(), isPending: false, reset: vi.fn(), error: null, data: null })),
  useDeleteHomeAssistantConfig: vi.fn(() => ({ mutate: vi.fn(), isPending: false, reset: vi.fn(), error: null })),
}))
vi.mock("@/hooks/use-owntracks.ts", () => ({
  useOwnTracksStatus: vi.fn(() => ({
    data: { state: "active", last_event_at: "2026-06-01T10:00:00Z", events_today: 5, token_configured: true },
    isLoading: false,
    error: null,
  })),
  useOwnTracksConfig: vi.fn(() => ({
    data: { webhook_url: "https://butlers.example.com/api/connectors/owntracks/webhook", host: "butlers.example.com" },
    isLoading: false,
    error: null,
  })),
  useOwnTracksGenerateToken: vi.fn(() => ({ mutate: vi.fn(), isPending: false, reset: vi.fn(), error: null })),
}))
vi.mock("@/hooks/use-steam.ts", () => ({
  useSteamAccounts: vi.fn(() => ({
    data: { accounts: [] },
    isLoading: false,
    error: null,
  })),
  useSteamConnect: vi.fn(() => ({ mutate: vi.fn(), isPending: false, reset: vi.fn(), error: null })),
  useSteamDisconnect: vi.fn(() => ({ mutate: vi.fn(), isPending: false, reset: vi.fn(), error: null })),
}))
vi.mock("@/hooks/use-spotify.ts", () => ({
  useSpotifyStatus: vi.fn(() => ({
    data: { state: "disconnected", connected: false, spotify_user_id: null, display_name: null, account_type: null, last_sync_at: null, error: null, needs_reauth: false, missing_scopes: [] },
    isLoading: false,
    error: null,
  })),
  useSpotifyConfig: vi.fn(() => ({ mutate: vi.fn(), isPending: false, reset: vi.fn(), error: null })),
  useSpotifyOAuthStart: vi.fn(() => ({ mutate: vi.fn(), isPending: false, reset: vi.fn(), error: null })),
  useSpotifyDisconnect: vi.fn(() => ({ mutate: vi.fn(), isPending: false, reset: vi.fn(), error: null })),
}))
vi.mock("@/hooks/use-whatsapp.ts", () => ({
  useWhatsAppStatus: vi.fn(() => ({
    data: { state: "disconnected", phone: null, paired_at: null, last_sync_at: null, bridge_running: false },
    isLoading: false,
    error: null,
  })),
  useWhatsAppPairStart: vi.fn(() => ({ mutate: vi.fn(), isPending: false, reset: vi.fn(), error: null })),
  useWhatsAppPairPoll: vi.fn(() => ({ data: null, isLoading: false, error: null })),
  useWhatsAppDisconnect: vi.fn(() => ({ mutate: vi.fn(), isPending: false, reset: vi.fn(), error: null })),
}))

// ---------------------------------------------------------------------------
// Mock the google-health hook — overridden per-test below
// ---------------------------------------------------------------------------
vi.mock("@/hooks/use-google-health.ts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/use-google-health.ts")>()
  return {
    ...actual,
    useDisconnectGoogleHealth: vi.fn(() => ({ mutate: vi.fn(), isPending: false, reset: vi.fn(), error: null })),
    // Default: no health status (simulates no health scopes → card hidden).
    useGoogleHealthStatus: vi.fn(() => ({ data: undefined, isLoading: false, error: null })),
  }
})

// Top-level imports
import * as useSecretsModule from "@/hooks/use-secrets.ts";
import * as useGoogleHealthModule from "@/hooks/use-google-health.ts";
import { GOOGLE_HEALTH_SCOPES } from "@/api/client.ts";
import { PageGoogleAccounts } from "./pages.tsx";
import {
  computeTestModeBannerVariant,
  TEST_MODE_RED_THRESHOLD_MS,
} from "@/lib/google-health-test-mode.ts";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderInRouter(element: React.ReactElement): string {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/secrets"]}>{element}</MemoryRouter>
    </QueryClientProvider>,
  );
}

/** Build a minimal GoogleHealthStatusResponse with sensible defaults. */
function makeHealthStatus(overrides: Partial<GoogleHealthStatusResponse> = {}): GoogleHealthStatusResponse {
  return {
    connected: true,
    scopes_granted: [...GOOGLE_HEALTH_SCOPES],
    last_ingest_at: "2026-06-06T10:00:00Z",
    last_token_refresh_at: "2026-06-06T10:00:00Z",
    rate_limit_remaining: null,
    test_mode: false,
    state: "healthy",
    error_message: null,
    sleep_sessions_7d: 7,
    daily_summaries_7d: 3,
    accounts: [],
    primary_account_email: "owner@example.com",
    ...overrides,
  };
}

// ── a. Status card renders with granted health scopes ────────────────────────

describe("GoogleHealthPassportStatusCard: renders with granted health scopes [bu-hh875]", () => {
  it("shows the health connector status card when primary has health scopes", () => {
    // Wire: account with all health scopes + healthy status response.
    vi.mocked(useSecretsModule.useGoogleAccounts).mockReturnValueOnce({
      data: [
        {
          id: "acc-1",
          email: "owner@example.com",
          display_name: "Owner",
          is_primary: true,
          status: "active" as const,
          granted_scopes: [...GOOGLE_HEALTH_SCOPES],
          connected_at: "2026-01-01T00:00:00Z",
          last_token_refresh_at: null,
        },
      ],
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useSecretsModule.useGoogleAccounts>);
    vi.mocked(useGoogleHealthModule.useGoogleHealthStatus).mockReturnValueOnce({
      data: makeHealthStatus({ sleep_sessions_7d: 7, daily_summaries_7d: 3 }),
      isLoading: false,
      error: null,
      isSuccess: true,
    } as unknown as ReturnType<typeof useGoogleHealthModule.useGoogleHealthStatus>);

    const html = renderInRouter(<PageGoogleAccounts />);

    expect(html).toContain('data-testid="health-passport-status-card"');
    expect(html).toContain("health connector");
    // State and counts are surfaced
    expect(html).toContain("healthy");
    expect(html).toContain("sleep · 7d");
    expect(html).toContain("summaries · 7d");
  });

  it("shows 7d counts from the status response", () => {
    vi.mocked(useSecretsModule.useGoogleAccounts).mockReturnValueOnce({
      data: [
        {
          id: "acc-1",
          email: "owner@example.com",
          display_name: "Owner",
          is_primary: true,
          status: "active" as const,
          granted_scopes: [...GOOGLE_HEALTH_SCOPES],
          connected_at: "2026-01-01T00:00:00Z",
          last_token_refresh_at: null,
        },
      ],
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useSecretsModule.useGoogleAccounts>);
    vi.mocked(useGoogleHealthModule.useGoogleHealthStatus).mockReturnValueOnce({
      data: makeHealthStatus({ sleep_sessions_7d: 14, daily_summaries_7d: 6 }),
      isLoading: false,
      error: null,
      isSuccess: true,
    } as unknown as ReturnType<typeof useGoogleHealthModule.useGoogleHealthStatus>);

    const html = renderInRouter(<PageGoogleAccounts />);

    expect(html).toContain("14");
    expect(html).toContain("6");
  });
});

// ── b. Status card hidden when primary lacks health scopes ───────────────────

describe("GoogleHealthPassportStatusCard: hidden without health scopes [bu-hh875]", () => {
  it("does NOT render the status card when account has no health scopes", () => {
    vi.mocked(useSecretsModule.useGoogleAccounts).mockReturnValueOnce({
      data: [
        {
          id: "acc-1",
          email: "owner@example.com",
          display_name: "Owner",
          is_primary: true,
          status: "active" as const,
          // Calendar only — no health scopes
          granted_scopes: ["https://www.googleapis.com/auth/calendar.readonly"],
          connected_at: "2026-01-01T00:00:00Z",
          last_token_refresh_at: null,
        },
      ],
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useSecretsModule.useGoogleAccounts>);
    // The health status hook is called unconditionally (React hooks rules), but
    // with `enabled: false` when hasHealth is false. Even if it returns data
    // the card must stay hidden when hasHealth is false.
    vi.mocked(useGoogleHealthModule.useGoogleHealthStatus).mockReturnValueOnce({
      data: makeHealthStatus(),
      isLoading: false,
      error: null,
      isSuccess: true,
    } as unknown as ReturnType<typeof useGoogleHealthModule.useGoogleHealthStatus>);

    const html = renderInRouter(<PageGoogleAccounts />);

    expect(html).not.toContain('data-testid="health-passport-status-card"');
  });

  it("does NOT render the status card when no accounts are connected", () => {
    // Default mock returns [] (empty) — hasHealth is false.
    const html = renderInRouter(<PageGoogleAccounts />);
    expect(html).not.toContain('data-testid="health-passport-status-card"');
  });
});

// ── Shared helper: wire a primary account that has health scopes ────────────

function mockPrimaryWithHealth(): void {
  vi.mocked(useSecretsModule.useGoogleAccounts).mockReturnValueOnce({
    data: [
      {
        id: "acc-1",
        email: "owner@example.com",
        display_name: "Owner",
        is_primary: true,
        status: "active" as const,
        granted_scopes: [...GOOGLE_HEALTH_SCOPES],
        connected_at: "2026-01-01T00:00:00Z",
        last_token_refresh_at: null,
      },
    ],
    isLoading: false,
    error: null,
  } as unknown as ReturnType<typeof useSecretsModule.useGoogleAccounts>);
}

function mockHealthStatus(status: GoogleHealthStatusResponse): void {
  vi.mocked(useGoogleHealthModule.useGoogleHealthStatus).mockReturnValueOnce({
    data: status,
    isLoading: false,
    error: null,
    isSuccess: true,
  } as unknown as ReturnType<typeof useGoogleHealthModule.useGoogleHealthStatus>);
}

/** Return an ISO timestamp `ms` milliseconds in the past. */
function refreshedMsAgo(ms: number): string {
  return new Date(Date.now() - ms).toISOString();
}

const ONE_DAY_MS = 24 * 60 * 60 * 1000;

// ── c. Banner is PERSISTENT orange whenever test_mode=true [bu-bxu50] ─────────

describe("TestModeExpiryBanner: persistent orange whenever test_mode=true [bu-bxu50]", () => {
  it("(a) shows the ORANGE banner when test_mode=true even with a FRESH token", () => {
    // Spec: the banner is persistent — it shows whenever test_mode=true,
    // regardless of how recently the token was refreshed.
    mockPrimaryWithHealth();
    mockHealthStatus(
      makeHealthStatus({
        test_mode: true,
        // Only 2 days old — well inside the 5d6h red threshold.
        last_token_refresh_at: refreshedMsAgo(2 * ONE_DAY_MS),
      }),
    );

    const html = renderInRouter(<PageGoogleAccounts />);

    expect(html).toContain('data-testid="test-mode-expiry-banner"');
    expect(html).toContain('data-variant="orange"');
    expect(html).toContain('data-expired="false"');
  });

  it("(a) shows the ORANGE banner when test_mode=true and last_token_refresh_at is null", () => {
    mockPrimaryWithHealth();
    mockHealthStatus(
      makeHealthStatus({ test_mode: true, last_token_refresh_at: null }),
    );

    const html = renderInRouter(<PageGoogleAccounts />);

    expect(html).toContain('data-testid="test-mode-expiry-banner"');
    expect(html).toContain('data-variant="orange"');
  });

  it("(c) the banner carries a re-consent link targeting scope_set=health", () => {
    mockPrimaryWithHealth();
    mockHealthStatus(
      makeHealthStatus({
        test_mode: true,
        last_token_refresh_at: refreshedMsAgo(2 * ONE_DAY_MS),
      }),
    );

    const html = renderInRouter(<PageGoogleAccounts />);

    expect(html).toContain('data-testid="test-mode-reconsent-link"');
    expect(html).toContain("scope_set=health");
    // Re-consent forces the Google consent screen and pre-selects the primary.
    expect(html).toContain("force_consent=true");
    expect(html).toContain("account_hint=owner%40example.com");
  });
});

// ── d. Banner elevates to RED past the 5d6h refresh-age threshold [bu-bxu50] ──

describe("TestModeExpiryBanner: red variant past 5d6h refresh age [bu-bxu50]", () => {
  it("(b) elevates to RED when refresh age is just OVER 5d6h", () => {
    mockPrimaryWithHealth();
    mockHealthStatus(
      makeHealthStatus({
        test_mode: true,
        last_token_refresh_at: refreshedMsAgo(TEST_MODE_RED_THRESHOLD_MS + 60_000),
      }),
    );

    const html = renderInRouter(<PageGoogleAccounts />);

    expect(html).toContain('data-testid="test-mode-expiry-banner"');
    expect(html).toContain('data-variant="red"');
    expect(html).toContain('data-expired="true"');
    // Red variant still links to the scope_set=health re-consent flow.
    expect(html).toContain('data-testid="test-mode-reconsent-link"');
    expect(html).toContain("scope_set=health");
  });

  it("(b) stays ORANGE when refresh age is just UNDER 5d6h", () => {
    mockPrimaryWithHealth();
    mockHealthStatus(
      makeHealthStatus({
        test_mode: true,
        last_token_refresh_at: refreshedMsAgo(TEST_MODE_RED_THRESHOLD_MS - 60_000),
      }),
    );

    const html = renderInRouter(<PageGoogleAccounts />);

    expect(html).toContain('data-testid="test-mode-expiry-banner"');
    expect(html).toContain('data-variant="orange"');
    expect(html).toContain('data-expired="false"');
  });
});

// ── e. Banner absent only when test_mode=false ───────────────────────────────

describe("TestModeExpiryBanner: absent when test_mode=false [bu-bxu50]", () => {
  it("does NOT show the banner when test_mode=false (even if token is old)", () => {
    mockPrimaryWithHealth();
    mockHealthStatus(
      makeHealthStatus({
        test_mode: false,
        last_token_refresh_at: refreshedMsAgo(8 * ONE_DAY_MS),
      }),
    );

    const html = renderInRouter(<PageGoogleAccounts />);

    expect(html).not.toContain('data-testid="test-mode-expiry-banner"');
  });
});

// ── f. computeTestModeBannerVariant unit boundary tests [bu-bxu50] ────────────

describe("computeTestModeBannerVariant: 5d6h red boundary [bu-bxu50]", () => {
  const now = new Date("2026-06-27T00:00:00.000Z");

  it("returns 'red' exactly AT the 5d6h threshold", () => {
    const at = new Date(now.getTime() - TEST_MODE_RED_THRESHOLD_MS).toISOString();
    expect(computeTestModeBannerVariant(at, now)).toBe("red");
  });

  it("returns 'red' just OVER the 5d6h threshold", () => {
    const over = new Date(now.getTime() - (TEST_MODE_RED_THRESHOLD_MS + 60_000)).toISOString();
    expect(computeTestModeBannerVariant(over, now)).toBe("red");
  });

  it("returns 'orange' just UNDER the 5d6h threshold", () => {
    const under = new Date(now.getTime() - (TEST_MODE_RED_THRESHOLD_MS - 60_000)).toISOString();
    expect(computeTestModeBannerVariant(under, now)).toBe("orange");
  });

  it("returns 'orange' (conservative default) when last_token_refresh_at is null", () => {
    expect(computeTestModeBannerVariant(null, now)).toBe("orange");
  });
});
