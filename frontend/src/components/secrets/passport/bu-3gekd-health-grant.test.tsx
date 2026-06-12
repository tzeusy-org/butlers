// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// Health Grant CTA wiring tests [bu-3gekd]
//
// Covers:
//   1. Health grant CTA builds the correct OAuth URL with scope_set=health AND
//      account_hint=<primary account email> AND force_consent=true.
//   2. Owner-default view renders Google spine entry without requiring a
//      manual ?identity= param (owner-default discoverability).
//   3. Empty-state CTA: 'connect Google' shown when no accounts connected.
//
// Spec: bu-3gekd — Owner-default Google Health grant wiring
// ---------------------------------------------------------------------------

import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
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
    revealSecret: vi.fn(),
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
    useDisconnectGoogleHealth: vi.fn(() => ({ mutate: vi.fn(), isPending: false, reset: vi.fn(), error: null })),
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

// Top-level imports (ESM — no require())
import { getGoogleOAuthStartUrl, GOOGLE_HEALTH_SCOPES } from "@/api/client.ts";
import * as useSecretsModule from "@/hooks/use-secrets.ts";
import { buildSpineEntries } from "./spine-builder.ts";
import { DirectionPassport } from "./DirectionPassport.tsx";
import { PageGoogleAccounts } from "./pages.tsx";
import type { InventoryResponse, UserCredential, Identity } from "./types.ts";
import { MOCK_PROVIDERS } from "./mock-data.ts";

function renderInRouter(element: React.ReactElement, initialEntries: string[] = ["/secrets"]): string {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>{element}</MemoryRouter>
    </QueryClientProvider>,
  );
}

/** Extract query params from a (possibly relative) URL string. */
function parseQueryParams(url: string): URLSearchParams {
  const idx = url.indexOf("?");
  return new URLSearchParams(idx >= 0 ? url.slice(idx + 1) : "");
}

// ── 1. Health grant URL contract ─────────────────────────────────────────────

describe("Health grant CTA: OAuth URL contains scope_set=health + account_hint + force_consent [bu-3gekd]", () => {
  /**
   * The ScopeSetPicker must pass the primary account email as account_hint so
   * the Health grant OAuth flow lands on the correct Google account.
   *
   * Backend contract (confirmed from src/butlers/api/routers/oauth.py):
   *   GET /api/oauth/google/start?scope_set=health&force_consent=true&account_hint=<email>
   */

  it("getGoogleOAuthStartUrl with scopeSet=health, forceConsent, accountHint generates correct URL", () => {
    const url = getGoogleOAuthStartUrl({
      scopeSet: "health",
      forceConsent: true,
      accountHint: "owner@example.com",
      pageOfOrigin: "secrets",
    });
    const params = parseQueryParams(url);

    // The URL must contain all three required params for Health grant [bu-3gekd]
    expect(params.get("scope_set")).toBe("health");
    expect(params.get("account_hint")).toBe("owner@example.com");
    expect(params.get("force_consent")).toBe("true");
  });

  it("getGoogleOAuthStartUrl correctly encodes email with special chars in account_hint", () => {
    const url = getGoogleOAuthStartUrl({
      scopeSet: "health",
      forceConsent: true,
      accountHint: "user+alias@example.com",
    });
    const params = parseQueryParams(url);

    // Decoding should recover the original email
    expect(params.get("account_hint")).toBe("user+alias@example.com");
    expect(params.get("scope_set")).toBe("health");
    expect(params.get("force_consent")).toBe("true");
  });

  it("getGoogleOAuthStartUrl without accountHint omits account_hint param", () => {
    const url = getGoogleOAuthStartUrl({
      scopeSet: "health",
      forceConsent: true,
    });
    const params = parseQueryParams(url);

    // When no accountHint provided, param should be absent
    expect(params.has("account_hint")).toBe(false);
    expect(params.get("scope_set")).toBe("health");
  });

  it("health grant CTA lives on the account row; picker shows per-account hint [bu-kg2nl]", () => {
    // When useGoogleAccounts returns an account without health scopes,
    // the account row shows a 'grant health' button and the picker's health
    // tile points at the per-account controls instead of a primary-only grant.
    const mockAccounts = [
      {
        id: "acc-1",
        email: "owner@example.com",
        display_name: "Owner",
        is_primary: true,
        status: "active" as const,
        // No health scopes
        granted_scopes: ["https://www.googleapis.com/auth/calendar.readonly"],
        connected_at: "2026-01-01T00:00:00Z",
        last_token_refresh_at: null,
      },
    ];
    vi.mocked(useSecretsModule.useGoogleAccounts).mockReturnValueOnce({
      data: mockAccounts,
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useSecretsModule.useGoogleAccounts>);

    const html = renderInRouter(<PageGoogleAccounts />);

    expect(html).toContain('data-scope-set-picker="true"');
    expect(html).toContain("Health");
    // Per-account grant control on the row [bu-kg2nl]
    expect(html).toContain("grant health");
    expect(html).toContain('data-account-health-state="absent"');
    // Picker no longer renders a primary-only health grant button
    expect(html).toContain("grant per account above");
  });

  it("ScopeSetPicker shows 'revoke' for health when health scopes already granted", () => {
    const mockAccounts = [
      {
        id: "acc-1",
        email: "owner@example.com",
        display_name: "Owner",
        is_primary: true,
        status: "active" as const,
        // Has health scopes
        granted_scopes: [...GOOGLE_HEALTH_SCOPES],
        connected_at: "2026-01-01T00:00:00Z",
        last_token_refresh_at: null,
      },
    ];
    vi.mocked(useSecretsModule.useGoogleAccounts).mockReturnValueOnce({
      data: mockAccounts,
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useSecretsModule.useGoogleAccounts>);

    const html = renderInRouter(<PageGoogleAccounts />);

    // When health is already granted, show revoke instead of grant
    expect(html).toContain("revoke");
  });
});

// ── 2. Owner-default discoverability ─────────────────────────────────────────

describe("Owner-default discoverability: Google spine entry visible without ?identity= [bu-3gekd]", () => {
  /**
   * When the backend returns a primary Google companion entity in the owner-
   * default response, the spine should show the Google credential entry without
   * requiring the user to manually switch identity chips.
   *
   * The fix in buildSpineEntries: when all identities are passed (owner-default),
   * credentials from ALL returned identities are included.
   */

  const OWNER_ENTITY_ID = "owner-entity-uuid";
  const GOOGLE_COMPANION_ENTITY_ID = "google-companion-entity-uuid";

  const ownerIdentity: Identity = {
    id: OWNER_ENTITY_ID,
    label: "Tze",
    role: "owner",
    hue: "oklch(0.78 0.13 30)",
  };

  const googleCompanionIdentity: Identity = {
    id: GOOGLE_COMPANION_ENTITY_ID,
    label: "owner@example.com",
    role: "member", // companion entities are not "owner"
  };

  const googleCredential: UserCredential = {
    provider: "google",
    // The Google credential is anchored to the companion entity, NOT the owner
    identity: GOOGLE_COMPANION_ENTITY_ID,
    state: "ok",
    fingerprint: "sha256:abc123",
    issued: "2026-01-01",
    expires: null,
    lastVerified: "today",
    lastUsed: "today",
    scopesRequired: [],
    scopesGranted: ["https://www.googleapis.com/auth/calendar.readonly"],
    feeds: [],
    breaks: [],
    test: null,
    audit: [],
  };

  const ownerDefaultInventory: InventoryResponse = {
    user: [googleCredential],
    system: [],
    cli: [],
    // Owner-default: both owner entity AND companion entity returned
    identities: [ownerIdentity, googleCompanionIdentity],
    providers: { google: MOCK_PROVIDERS.google },
    ownerEntityId: OWNER_ENTITY_ID,
  };

  it("buildSpineEntries with all identities includes companion entity credentials", () => {
    // Owner-default: pass ALL identity IDs (as returned by the backend)
    const allIdentityIds = [OWNER_ENTITY_ID, GOOGLE_COMPANION_ENTITY_ID];
    const entries = buildSpineEntries(ownerDefaultInventory, allIdentityIds);

    // Google credential (from companion entity) should appear in the spine
    const googleEntry = entries.find((e) => e.key === "u:google");
    expect(googleEntry).toBeDefined();
    expect(googleEntry?.family).toBe("user");
    expect(googleEntry?.provider).toBe("google");
  });

  it("buildSpineEntries with owner-only identity does NOT include companion entity credentials", () => {
    // When only the owner identity is passed (explicit ?identity= chip selection),
    // companion entity credentials should NOT appear.
    const entries = buildSpineEntries(ownerDefaultInventory, OWNER_ENTITY_ID);

    const googleEntry = entries.find((e) => e.key === "u:google");
    expect(googleEntry).toBeUndefined();
  });

  it("DirectionPassport owner-default renders Google spine entry", () => {
    // Render with no ?identity= param (owner-default) — Google should appear in spine
    const html = renderInRouter(
      <DirectionPassport inventory={ownerDefaultInventory} />,
      ["/secrets"],
    );
    // Google entry should be in the spine
    expect(html).toContain('data-key="u:google"');
    expect(html).toContain('data-family="user"');
  });

  it("DirectionPassport with explicit ?identity=owner does NOT render Google from companion", () => {
    // When the owner identity chip is explicitly selected (sets ?identity= param),
    // only owner entity credentials appear — companion entity creds are excluded.
    const html = renderInRouter(
      <DirectionPassport inventory={ownerDefaultInventory} />,
      [`/secrets?identity=${OWNER_ENTITY_ID}`],
    );
    // Google entry (from companion entity) should NOT appear when viewing owner-only
    expect(html).not.toContain('data-key="u:google"');
  });

  it("DirectionPassport owner-default: ?focus=u:google renders PageUser for google", () => {
    // With ?focus=u:google set, PageUser for the Google provider should render.
    // Even though Google credential is on the companion entity, the page resolver
    // must find it because spineIdentityIds includes the companion entity.
    const html = renderInRouter(
      <DirectionPassport inventory={ownerDefaultInventory} />,
      ["/secrets?focus=u:google"],
    );
    // PageUser for google should be rendered
    expect(html).toContain('data-page="user"');
    expect(html).toContain('data-provider="google"');
  });
});

// ── 3. Owner-default discoverability: expired primary + non-primary exclusion ──
//
// Spec: §Owner-Default Inventory Surfaces Primary Google Account (butler-secrets)
// "This includes status='expired' accounts so the owner can reach the scope-set
// picker and reauth CTA without needing a manual ?identity= parameter."
// Spec: §Multi-Account Leak Prevention (dashboard-google-accounts)
// "Non-primary Google accounts SHALL NOT appear in the owner-default projection."

describe("Owner-default discoverability: expired primary still surfaces; non-primary excluded [bu-1sz6w]", () => {
  const OWNER_ENTITY_ID = "owner-entity-uuid";
  const PRIMARY_GOOGLE_ENTITY_ID = "google-primary-entity-uuid";
  const NONPRIMARY_GOOGLE_ENTITY_ID = "google-nonprimary-entity-uuid";

  const ownerIdentity: Identity = {
    id: OWNER_ENTITY_ID,
    label: "Tze",
    role: "owner",
    hue: "oklch(0.78 0.13 30)",
  };

  const primaryGoogleIdentity: Identity = {
    id: PRIMARY_GOOGLE_ENTITY_ID,
    label: "primary@example.com",
    role: "member",
  };

  const nonPrimaryGoogleIdentity: Identity = {
    id: NONPRIMARY_GOOGLE_ENTITY_ID,
    label: "secondary@example.com",
    role: "member",
  };

  // An EXPIRED primary credential — backend includes it at priority 1 regardless
  const expiredPrimaryCredential: UserCredential = {
    provider: "google",
    identity: PRIMARY_GOOGLE_ENTITY_ID,
    state: "expired",
    fingerprint: "sha256:expired1",
    issued: "2026-01-01",
    expires: "2026-06-01",
    lastVerified: "5 days ago",
    lastUsed: "5 days ago",
    scopesRequired: [],
    scopesGranted: ["https://www.googleapis.com/auth/calendar.readonly"],
    feeds: [],
    breaks: [],
    test: null,
    audit: [],
  };

  // A non-primary credential — backend should NOT include it in owner-default
  const nonPrimaryCredential: UserCredential = {
    provider: "google",
    identity: NONPRIMARY_GOOGLE_ENTITY_ID,
    state: "ok",
    fingerprint: "sha256:nonprimary1",
    issued: "2026-02-01",
    expires: null,
    lastVerified: "today",
    lastUsed: "today",
    scopesRequired: [],
    scopesGranted: [],
    feeds: [],
    breaks: [],
    test: null,
    audit: [],
  };

  it("expired primary credential still surfaces u:google in owner-default spine [bu-1sz6w spec §Owner-Default Inventory]", () => {
    // Simulate owner-default inventory where the backend returns an expired primary
    const inventory: InventoryResponse = {
      user: [expiredPrimaryCredential],
      system: [],
      cli: [],
      identities: [ownerIdentity, primaryGoogleIdentity],
      providers: { google: MOCK_PROVIDERS.google },
      ownerEntityId: OWNER_ENTITY_ID,
    };

    // Pass all identities (owner-default mode — no ?identity= chip selected)
    const allIdentityIds = [OWNER_ENTITY_ID, PRIMARY_GOOGLE_ENTITY_ID];
    const entries = buildSpineEntries(inventory, allIdentityIds);

    const googleEntry = entries.find((e) => e.key === "u:google");
    expect(googleEntry).toBeDefined();
    expect(googleEntry?.family).toBe("user");
    // The entry state should reflect the expired credential
    expect(googleEntry?.state).toBe("expired");
  });

  it("expired primary credential: DirectionPassport renders Google spine entry in needs-hand group [bu-1sz6w spec §Owner-Default Inventory]", () => {
    const inventory: InventoryResponse = {
      user: [expiredPrimaryCredential],
      system: [],
      cli: [],
      identities: [ownerIdentity, primaryGoogleIdentity],
      providers: { google: MOCK_PROVIDERS.google },
      ownerEntityId: OWNER_ENTITY_ID,
    };

    const html = renderInRouter(
      <DirectionPassport inventory={inventory} />,
      ["/secrets"],
    );
    // Expired primary must still appear in the spine
    expect(html).toContain('data-key="u:google"');
  });

  it("non-primary account: buildSpineEntries excludes non-primary when only primary identity passed [bu-1sz6w spec §Multi-Account Leak Prevention]", () => {
    // Inventory that includes BOTH credentials; allIdentityIds only contains the
    // primary identity — so buildSpineEntries must filter out the non-primary one.
    const inventory: InventoryResponse = {
      user: [expiredPrimaryCredential, nonPrimaryCredential],
      system: [],
      cli: [],
      // Owner-default: backend only returns primary companion entity, NOT non-primary
      identities: [ownerIdentity, primaryGoogleIdentity],
      providers: { google: MOCK_PROVIDERS.google },
      ownerEntityId: OWNER_ENTITY_ID,
    };

    const allIdentityIds = [OWNER_ENTITY_ID, PRIMARY_GOOGLE_ENTITY_ID];
    const entries = buildSpineEntries(inventory, allIdentityIds);

    // Exactly one u:google entry — the expired primary; non-primary (state "ok") excluded
    const googleEntries = entries.filter((e) => e.key === "u:google");
    expect(googleEntries).toHaveLength(1);
    const [googleEntry] = googleEntries;
    expect(googleEntry.state).toBe("expired");
  });

  it("non-primary account accessible under explicit ?identity= lens [bu-1sz6w spec §Multi-Account Leak Prevention]", () => {
    // Simulate what happens when backend returns non-primary under ?identity=<nonprimary_entity_id>
    const identityScopedInventory: InventoryResponse = {
      user: [nonPrimaryCredential],
      system: [],
      cli: [],
      identities: [ownerIdentity, nonPrimaryGoogleIdentity],
      providers: { google: MOCK_PROVIDERS.google },
      ownerEntityId: OWNER_ENTITY_ID,
    };

    // When the ?identity= chip is set to the non-primary entity, its credential appears
    const entries = buildSpineEntries(identityScopedInventory, NONPRIMARY_GOOGLE_ENTITY_ID);

    const googleEntry = entries.find((e) => e.key === "u:google");
    expect(googleEntry).toBeDefined();
    // Non-primary credential is accessible; spine entry exists for the non-primary account
    expect(googleEntry?.family).toBe("user");
    expect(googleEntry?.provider).toBe("google");
  });
});

// ── 4. Empty-state Connect CTA ────────────────────────────────────────────────

describe("PageGoogleAccounts: empty-state 'connect Google' CTA [bu-3gekd]", () => {
  /**
   * When no Google accounts are connected, the passport shows a clear
   * 'connect Google' CTA rather than nothing, so the owner can initiate
   * the OAuth dance without navigating away.
   */

  it("shows 'connect Google' CTA when no accounts connected", () => {
    // Default mock returns [] (empty)
    const html = renderInRouter(<PageGoogleAccounts />);

    expect(html).toContain("connect Google");
    expect(html).toContain('data-google-connect-empty-state="true"');
  });

  it("does NOT show 'add another account' when no accounts connected", () => {
    const html = renderInRouter(<PageGoogleAccounts />);

    // Empty state: only 'connect Google', not 'add another account'
    expect(html).not.toContain("add another account");
  });

  it("shows 'add another account' when at least one account is already connected", () => {
    const mockAccounts = [
      {
        id: "acc-1",
        email: "owner@example.com",
        display_name: "Owner",
        is_primary: true,
        status: "active" as const,
        granted_scopes: [],
        connected_at: "2026-01-01T00:00:00Z",
        last_token_refresh_at: null,
      },
    ];
    vi.mocked(useSecretsModule.useGoogleAccounts).mockReturnValueOnce({
      data: mockAccounts,
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useSecretsModule.useGoogleAccounts>);

    const html = renderInRouter(<PageGoogleAccounts />);

    expect(html).toContain("add another account");
    expect(html).not.toContain("connect Google");
  });
});
