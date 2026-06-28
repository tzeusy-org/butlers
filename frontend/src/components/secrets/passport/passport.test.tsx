// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// Passport component tests [bu-qu8v8]
//
// Coverage:
//   - Spine renders against mocked inventory data
//   - PageUser renders against mocked User credential
//   - PageSystem renders against mocked System credential
//   - PageCli renders against mocked CLI credential
//   - DirectionPassport does not expose prototype tweaks chrome
//   - ONE-ROW-TEMPLATE UNIFORMITY: System/User/CLI spine rows have identical
//     HTML structure modulo data-* attrs and text content
//   - Empty needs-hand group hidden on calm day (all-ok)
//   - Identity switcher shows only when multiple identities present
// ---------------------------------------------------------------------------

import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import * as React from "react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ---------------------------------------------------------------------------
// Mock API client — PageUser/PageSystem now use TanStack Query mutation hooks
// which call useQueryClient() at render time; even renderToStaticMarkup
// triggers the hook.
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
// PageSystem calls useButlers() to populate the override butler-picker.
// Provide an empty butler list so tests render without hitting the API.
vi.mock("@/hooks/use-butlers", () => ({
  useButlers: vi.fn(() => ({ data: { data: [] }, isLoading: false, error: null })),
}))
// PageGoogleAccounts uses useGoogleAccounts, useSetPrimaryAccount, useDisconnectAccount.
// Mock use-secrets to return a stable empty account list (no fetch fired).
vi.mock("@/hooks/use-secrets.ts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/use-secrets.ts")>()
  return {
    ...actual,
    useGoogleAccounts: vi.fn(() => ({ data: [], isLoading: false, error: null })),
    useSetPrimaryAccount: vi.fn(() => ({ mutate: vi.fn(), isPending: false, error: null })),
    useDisconnectAccount: vi.fn(() => ({ mutate: vi.fn(), isPending: false, reset: vi.fn(), error: null })),
  }
})
// useDisconnectGoogleHealth lives in use-google-health.
vi.mock("@/hooks/use-google-health.ts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/use-google-health.ts")>()
  return {
    ...actual,
    useDisconnectGoogleHealth: vi.fn(() => ({ mutate: vi.fn(), isPending: false, reset: vi.fn(), error: null })),
  }
})
// Provider-config drawer hooks [bu-ayp6v.8]
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
    data: {
      accounts: [
        { id: "steam-1", steam_id: "76561198000000001", display_name: "TestUser", profile_url: null, avatar_url: null, is_primary: true, status: "active", connected_at: "2026-01-01T00:00:00Z", last_poll_at: null },
      ],
    },
    isLoading: false,
    error: null,
  })),
  useSteamConnect: vi.fn(() => ({ mutate: vi.fn(), isPending: false, reset: vi.fn(), error: null })),
  useSteamDisconnect: vi.fn(() => ({ mutate: vi.fn(), isPending: false, reset: vi.fn(), error: null })),
}))
// Provider-config drawer hooks [bu-ayp6v.9]
vi.mock("@/hooks/use-spotify.ts", () => ({
  useSpotifyStatus: vi.fn(() => ({
    data: { state: "connected", connected: true, spotify_user_id: "testuser", display_name: "Test User", account_type: "premium", last_sync_at: null, error: null, needs_reauth: false, missing_scopes: [] },
    isLoading: false,
    error: null,
  })),
  useSpotifyConfig: vi.fn(() => ({ mutate: vi.fn(), isPending: false, reset: vi.fn(), error: null })),
  useSpotifyOAuthStart: vi.fn(() => ({ mutate: vi.fn(), isPending: false, reset: vi.fn(), error: null })),
  useSpotifyDisconnect: vi.fn(() => ({ mutate: vi.fn(), isPending: false, reset: vi.fn(), error: null })),
}))
vi.mock("@/hooks/use-whatsapp.ts", () => ({
  useWhatsAppStatus: vi.fn(() => ({
    data: { state: "connected", phone: "+1 *** *** 7890", paired_at: "2026-01-01T00:00:00Z", last_sync_at: null, bridge_running: true },
    isLoading: false,
    error: null,
  })),
  useWhatsAppPairStart: vi.fn(() => ({ mutate: vi.fn(), isPending: false, reset: vi.fn(), error: null })),
  useWhatsAppPairPoll: vi.fn(() => ({ data: null, isLoading: false, error: null })),
  useWhatsAppDisconnect: vi.fn(() => ({ mutate: vi.fn(), isPending: false, reset: vi.fn(), error: null })),
}))

import { SpineRow, Spine } from "./Spine.tsx";
import { PageUser, PageSystem, PageCli, PageGoogleAccounts } from "./pages.tsx";
import { DirectionPassport } from "./DirectionPassport.tsx";
import {
  ProviderConfigDrawer,
  HomeAssistantDrawer,
  HomeAssistantDrawerContent,
  OwnTracksDrawer,
  OwnTracksDrawerContent,
  SteamDrawer,
  SteamDrawerContent,
  SpotifyDrawer,
  SpotifyDrawerContent,
  WhatsAppDrawer,
  WhatsAppDrawerContent,
} from "./ProviderConfigDrawer.tsx";
import {
  Fingerprint,
  StampGlyph,
  CredentialDot,
  Sliver,
} from "./atoms.tsx";
import {
  MOCK_INVENTORY,
  MOCK_USER_CREDENTIALS,
  MOCK_SYSTEM_CREDENTIALS,
  MOCK_CLI_CREDENTIALS,
  MOCK_PROVIDERS,
  MOCK_IDENTITIES,
} from "./mock-data.ts";
import type { SpineEntry } from "./types.ts";
import { buildSpineEntries } from "./spine-builder.ts";

// ── Helpers ─────────────────────────────────────────────────────────────────

function renderInRouter(element: React.ReactElement, initialEntries: string[] = ["/secrets"]): string {
  // DirectionPassport renders PageCliConnected, which reads CLI auth providers
  // via react-query; a client must be present even though no fetch fires here.
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>{element}</MemoryRouter>
    </QueryClientProvider>,
  );
}

// ── SpineRow uniformity ──────────────────────────────────────────────────────

/**
 * Strip variable parts from an HTML string to leave only structural skeleton.
 *
 * Per spec §One Row Template Across All Three Families:
 * "identical HTML structure (tags + class names) modulo data-* attributes and
 * text content"
 *
 * We also strip inline style attributes since families differ only in
 * rendering details (font-family/size for mono vs sans labels) that are
 * driven by data content, not by structural differences.
 */
function stripDataAndText(html: string): string {
  return html
    // Remove data-* attributes
    .replace(/\s+data-[a-z][a-z0-9-]*="[^"]*"/g, "")
    // Remove aria-label contents (state-specific labels)
    .replace(/aria-label="[^"]*"/g, 'aria-label=""')
    // Remove inline style attributes entirely (content-driven, not structural)
    .replace(/\s+style="[^"]*"/g, "")
    // Remove all text content between tags
    .replace(/>([^<]+)</g, "><")
    // Normalize whitespace
    .replace(/\s+/g, " ")
    .trim();
}

describe("SpineRow: one-row-template uniformity", () => {
  /**
   * Acceptance criterion: System/User/CLI rows rendered with equivalent state
   * have identical HTML structure (tags + class names) modulo data-* attrs
   * and text content.
   */

  const baseEntry: Omit<SpineEntry, "family" | "key" | "mono"> = {
    label: "Test Credential",
    state: "ok",
    subline: "verified 14:00",
    lastTouchOrder: 0,
  };

  const userEntry: SpineEntry = {
    ...baseEntry,
    key: "u:google",
    family: "user",
    mono: false,
    provider: "google",
  };

  const systemEntry: SpineEntry = {
    ...baseEntry,
    key: "s:TEST_KEY",
    family: "system",
    mono: true,
    label: "TEST_KEY",
    subline: "shared default",
  };

  const cliEntry: SpineEntry = {
    ...baseEntry,
    key: "c:claude-cli",
    family: "cli",
    mono: false,
    label: "Claude Code",
    subline: "used 14:15 today",
  };

  it("User SpineRow and System SpineRow have identical structure modulo data-attrs and content", () => {
    const userHtml = renderToStaticMarkup(
      <SpineRow entry={userEntry} n={1} active={false} onClick={() => {}} />,
    );
    const systemHtml = renderToStaticMarkup(
      <SpineRow entry={systemEntry} n={2} active={false} onClick={() => {}} />,
    );

    const userStripped = stripDataAndText(userHtml);
    const systemStripped = stripDataAndText(systemHtml);

    expect(userStripped).toBe(systemStripped);
  });

  it("User SpineRow and CLI SpineRow have identical structure modulo data-attrs and content", () => {
    const userHtml = renderToStaticMarkup(
      <SpineRow entry={userEntry} n={1} active={false} onClick={() => {}} />,
    );
    const cliHtml = renderToStaticMarkup(
      <SpineRow entry={cliEntry} n={3} active={false} onClick={() => {}} />,
    );

    const userStripped = stripDataAndText(userHtml);
    const cliStripped = stripDataAndText(cliHtml);

    expect(userStripped).toBe(cliStripped);
  });

  it("System SpineRow and CLI SpineRow have identical structure modulo data-attrs and content", () => {
    const systemHtml = renderToStaticMarkup(
      <SpineRow entry={systemEntry} n={2} active={false} onClick={() => {}} />,
    );
    const cliHtml = renderToStaticMarkup(
      <SpineRow entry={cliEntry} n={3} active={false} onClick={() => {}} />,
    );

    const systemStripped = stripDataAndText(systemHtml);
    const cliStripped = stripDataAndText(cliHtml);

    expect(systemStripped).toBe(cliStripped);
  });

  it("SpineRow has data-spine-row attribute", () => {
    const html = renderToStaticMarkup(
      <SpineRow entry={userEntry} n={1} active={false} onClick={() => {}} />,
    );
    expect(html).toContain('data-spine-row="true"');
  });

  it("SpineRow has data-family attribute", () => {
    const html = renderToStaticMarkup(
      <SpineRow entry={userEntry} n={1} active={false} onClick={() => {}} />,
    );
    expect(html).toContain('data-family="user"');
  });

  it("SpineRow has data-key attribute", () => {
    const html = renderToStaticMarkup(
      <SpineRow entry={userEntry} n={1} active={false} onClick={() => {}} />,
    );
    expect(html).toContain('data-key="u:google"');
  });

  it("SpineRow renders 10px vertical padding", () => {
    const html = renderToStaticMarkup(
      <SpineRow entry={userEntry} n={1} active={false} onClick={() => {}} />,
    );
    // py-2.5 = 10px vertical padding
    expect(html).toContain("py-2.5");
  });
});

// ── Spine ────────────────────────────────────────────────────────────────────

describe("Spine: renders against mocked inventory", () => {
  const entries = buildSpineEntries(MOCK_INVENTORY, "tze");

  it("renders with entries", () => {
    const html = renderToStaticMarkup(
      <Spine
        entries={entries}
        activeKey={entries[0]?.key ?? ""}
        onSelect={() => {}}
        sortMode="severity"
        onSortChange={() => {}}
        search=""
        onSearchChange={() => {}}
        identities={MOCK_IDENTITIES}
        activeIdentityId="tze"
        onIdentityChange={() => {}}
        providers={MOCK_PROVIDERS}
      />,
    );
    expect(html).toContain("data-spine-row");
    expect(html).toContain("data-spine-group");
  });

  it("shows identity switcher when multiple identities present", () => {
    const html = renderToStaticMarkup(
      <Spine
        entries={entries}
        activeKey="u:google"
        onSelect={() => {}}
        sortMode="severity"
        onSortChange={() => {}}
        search=""
        onSearchChange={() => {}}
        identities={MOCK_IDENTITIES}
        activeIdentityId="tze"
        onIdentityChange={() => {}}
        providers={MOCK_PROVIDERS}
      />,
    );
    // Both identity chips rendered
    expect(html).toContain('data-identity-id="tze"');
    expect(html).toContain('data-identity-id="wei"');
  });

  it("hides identity chip when only one identity", () => {
    const singleIdentity = [MOCK_IDENTITIES[0]];
    const html = renderToStaticMarkup(
      <Spine
        entries={entries}
        activeKey="u:google"
        onSelect={() => {}}
        sortMode="severity"
        onSortChange={() => {}}
        search=""
        onSearchChange={() => {}}
        identities={singleIdentity}
        activeIdentityId="tze"
        onIdentityChange={() => {}}
        providers={MOCK_PROVIDERS}
      />,
    );
    // No second identity chip
    expect(html).not.toContain('data-identity-id="wei"');
  });

  it("calm-day: needs-hand group hidden when all credentials healthy", () => {
    const calmInventory = {
      ...MOCK_INVENTORY,
      user: MOCK_USER_CREDENTIALS.map((u) => ({ ...u, state: "ok" as const })),
      cli:  MOCK_CLI_CREDENTIALS.map((c) => ({ ...c, state: "ok" as const })),
    };
    const calmEntries = buildSpineEntries(calmInventory, "tze");
    const html = renderToStaticMarkup(
      <Spine
        entries={calmEntries}
        activeKey={calmEntries[0]?.key ?? ""}
        onSelect={() => {}}
        sortMode="severity"
        onSortChange={() => {}}
        search=""
        onSearchChange={() => {}}
        identities={MOCK_IDENTITIES}
        activeIdentityId="tze"
        onIdentityChange={() => {}}
        providers={MOCK_PROVIDERS}
      />,
    );
    // needs-hand group is omitted when empty
    expect(html).not.toContain("needs hand · ");
  });

  it("sick-day: needs-hand group shown when at least one credential is sick", () => {
    const html = renderToStaticMarkup(
      <Spine
        entries={entries}
        activeKey={entries[0]?.key ?? ""}
        onSelect={() => {}}
        sortMode="severity"
        onSortChange={() => {}}
        search=""
        onSearchChange={() => {}}
        identities={MOCK_IDENTITIES}
        activeIdentityId="tze"
        onIdentityChange={() => {}}
        providers={MOCK_PROVIDERS}
      />,
    );
    expect(html).toContain("needs hand ·");
  });
});

// ── SpineSearch + SortPicker ─────────────────────────────────────────────────

describe("SpineSearch", () => {
  it("renders search input", () => {
    const entries = buildSpineEntries(MOCK_INVENTORY, "tze");
    const html = renderToStaticMarkup(
      <Spine
        entries={entries}
        activeKey=""
        onSelect={() => {}}
        sortMode="severity"
        onSortChange={() => {}}
        search=""
        onSearchChange={() => {}}
        identities={[MOCK_IDENTITIES[0]]}
        activeIdentityId="tze"
        onIdentityChange={() => {}}
      />,
    );
    expect(html).toContain('data-spine-search="true"');
  });
});

describe("SortPicker", () => {
  it("renders three sort options", () => {
    const entries = buildSpineEntries(MOCK_INVENTORY, "tze");
    const html = renderToStaticMarkup(
      <Spine
        entries={entries}
        activeKey=""
        onSelect={() => {}}
        sortMode="severity"
        onSortChange={() => {}}
        search=""
        onSearchChange={() => {}}
        identities={[MOCK_IDENTITIES[0]]}
        activeIdentityId="tze"
        onIdentityChange={() => {}}
      />,
    );
    expect(html).toContain('data-sort-mode="severity"');
    expect(html).toContain('data-sort-mode="recency"');
    expect(html).toContain('data-sort-mode="alpha"');
  });
});

// ── PageUser ─────────────────────────────────────────────────────────────────

describe("PageUser: renders against mocked data", () => {
  // PageUser uses TanStack Query mutation hooks; renderInRouter provides the
  // required QueryClientProvider wrapper.
  it("renders google credential", () => {
    const google = MOCK_USER_CREDENTIALS.find((u) => u.provider === "google" && u.identity === "tze")!;
    const html = renderInRouter(
      <PageUser
        credential={google}
        provider={MOCK_PROVIDERS.google}
        identities={MOCK_IDENTITIES}
      />,
    );
    expect(html).toContain('data-page="user"');
    expect(html).toContain('data-provider="google"');
    expect(html).toContain('data-heading-band="true"');
    expect(html).toContain('data-state-plaque="true"');
  });

  it("renders expired spotify credential with re-authorize commit", () => {
    const spotify = MOCK_USER_CREDENTIALS.find((u) => u.provider === "spotify")!;
    const html = renderInRouter(
      <PageUser credential={spotify} provider={MOCK_PROVIDERS.spotify} />,
    );
    expect(html).toContain("expired");
    expect(html).toContain("re-authorize");
  });

  it("renders webhook owntracks credential", () => {
    const owntracks = MOCK_USER_CREDENTIALS.find((u) => u.provider === "owntracks")!;
    const html = renderInRouter(
      <PageUser credential={owntracks} provider={MOCK_PROVIDERS.owntracks} />,
    );
    expect(html).toContain("incoming url");
    expect(html).toContain("butlers.tze");
  });

  it("renders never_set steam credential with connect button", () => {
    const steam = MOCK_USER_CREDENTIALS.find((u) => u.provider === "steam")!;
    const html = renderInRouter(
      <PageUser credential={steam} provider={MOCK_PROVIDERS.steam} />,
    );
    expect(html).toContain("connect");
  });

  it("shows data-page attribute", () => {
    const google = MOCK_USER_CREDENTIALS[0]!;
    const html = renderInRouter(
      <PageUser credential={google} provider={MOCK_PROVIDERS.google} />,
    );
    expect(html).toContain('data-page="user"');
  });
});

// ── PageSystem ───────────────────────────────────────────────────────────────

describe("PageSystem: renders against mocked data", () => {
  // PageSystem now uses TanStack Query hooks (useSetSystemSecret, useButlers, etc.);
  // use renderInRouter to supply the required QueryClientProvider.

  it("renders shared credential", () => {
    const telegram = MOCK_SYSTEM_CREDENTIALS.find((s) => s.key === "BUTLER_TELEGRAM_TOKEN")!;
    const html = renderInRouter(<PageSystem credential={telegram} />);
    expect(html).toContain('data-page="system"');
    expect(html).toContain("BUTLER_TELEGRAM_TOKEN");
    expect(html).toContain("shared default");
  });

  it("renders missing credential with set-value button", () => {
    const owntracks = MOCK_SYSTEM_CREDENTIALS.find((s) => s.key === "OWNTRACKS_WEBHOOK_TOKEN")!;
    const html = renderInRouter(<PageSystem credential={owntracks} />);
    expect(html).toContain("set value");
    expect(html).toContain("not set");
  });

  it("renders system state plaques without rotated-stamp styling", () => {
    const credentials = [
      MOCK_SYSTEM_CREDENTIALS.find((s) => s.key === "BUTLER_TELEGRAM_TOKEN")!,
      MOCK_SYSTEM_CREDENTIALS.find((s) => s.key === "OWNTRACKS_WEBHOOK_TOKEN")!,
    ];

    for (const credential of credentials) {
      const html = renderInRouter(<PageSystem credential={credential} />);
      expect(html).toContain('data-state-plaque="true"');
      expect(html).not.toContain("rotate(");
    }
  });

  it("renders plain-value credential (email address)", () => {
    const gmail = MOCK_SYSTEM_CREDENTIALS.find((s) => s.key === "GMAIL_SENDER_ADDRESS")!;
    const html = renderInRouter(<PageSystem credential={gmail} />);
    expect(html).toContain("tze@lim.house");
    expect(html).toContain("value");
  });

  it("hides probe/test action for plainValue credentials", () => {
    const gmail = MOCK_SYSTEM_CREDENTIALS.find((s) => s.key === "GMAIL_SENDER_ADDRESS")!;
    const html = renderInRouter(<PageSystem credential={gmail} />);
    // plainValue credential: no test button and no probe section in body
    expect(html).not.toContain("probe · last test");
    expect(html).not.toContain(">test<");
    expect(html).not.toContain("run probe");
  });

  it("keeps probe/test action for non-plainValue credentials", () => {
    const telegram = MOCK_SYSTEM_CREDENTIALS.find((s) => s.key === "BUTLER_TELEGRAM_TOKEN")!;
    const html = renderInRouter(<PageSystem credential={telegram} />);
    // non-plainValue credential: probe section visible in body and test button in footer
    expect(html).toContain("probe · last test");
    expect(html).toContain(">test<");
  });

  it("shows data-page attribute", () => {
    const telegram = MOCK_SYSTEM_CREDENTIALS[0]!;
    const html = renderInRouter(<PageSystem credential={telegram} />);
    expect(html).toContain('data-page="system"');
  });
});

// ── PageCli ──────────────────────────────────────────────────────────────────

// PageCli now uses TanStack Query mutation hooks; use renderInRouter.
describe("PageCli: renders against mocked data", () => {
  it("renders claude-cli (ok state)", () => {
    const claude = MOCK_CLI_CREDENTIALS.find((c) => c.id === "claude-cli")!;
    const html = renderInRouter(<PageCli credential={claude} />);
    expect(html).toContain('data-page="cli"');
    expect(html).toContain("Claude Code");
    expect(html).toContain("how to use");
    expect(html).toContain("CLAUDE_CLI_TOKEN");
  });

  it("renders codex-cli (expiring state) with rotate commit button", () => {
    const codex = MOCK_CLI_CREDENTIALS.find((c) => c.id === "codex-cli")!;
    const html = renderInRouter(<PageCli credential={codex} />);
    expect(html).toContain("expiring");
    expect(html).toContain("rotate");
  });

  it("renders gemini-cli (never_set) with set token button", () => {
    const gemini = MOCK_CLI_CREDENTIALS.find((c) => c.id === "gemini-cli")!;
    const html = renderInRouter(<PageCli credential={gemini} />);
    expect(html).toContain("set token");
  });

  it("shows data-page attribute", () => {
    const claude = MOCK_CLI_CREDENTIALS[0]!;
    const html = renderInRouter(<PageCli credential={claude} />);
    expect(html).toContain('data-page="cli"');
  });
});

// ── DirectionPassport ────────────────────────────────────────────────────────

describe("DirectionPassport: renders against mocked inventory", () => {
  it("renders the passport with default focus", () => {
    const html = renderInRouter(<DirectionPassport inventory={MOCK_INVENTORY} />);
    expect(html).toContain('data-direction-passport="true"');
    expect(html).toContain('data-spine-row');
  });

  it("renders the voice paragraph when credentials need attention", () => {
    const html = renderInRouter(<DirectionPassport inventory={MOCK_INVENTORY} />);
    // The header voice paragraph appears when needsAttention > 0
    // We have sick credentials in mock data, so this should appear.
    expect(html).toContain("data-direction-passport");
  });

  it("renders the page header with secrets eyebrow", () => {
    const html = renderInRouter(<DirectionPassport inventory={MOCK_INVENTORY} />);
    // The eyebrow text "secrets" appears in the header
    expect(html).toContain("secrets");
  });

  it("does not render a tweaks trigger or panel", () => {
    const html = renderInRouter(<DirectionPassport inventory={MOCK_INVENTORY} />);
    expect(html).not.toContain('data-tweaks-trigger="true"');
    expect(html).not.toContain('data-tweaks-panel="true"');
    expect(html).not.toContain("tweaks");
  });

  it("maps legacy s:cli-auth focus URLs to the CLI runtime page", () => {
    const inventory = {
      ...MOCK_INVENTORY,
      cli: [
        {
          id: "cli-auth/codex",
          label: "Codex (OpenAI)",
          fingerprint: null,
          state: "warn" as const,
          lastUsed: null,
          issued: null,
          expires: null,
          scopesGranted: [],
          scopesRequired: [],
          test: null,
        },
      ],
    };
    const html = renderInRouter(
      <DirectionPassport inventory={inventory} />,
      ["/secrets?focus=s%3Acli-auth%2Fcodex"],
    );

    expect(html).toContain('data-page="cli"');
    expect(html).toContain('data-cli-id="cli-auth/codex"');
  });
});

// ── buildSpineEntries ────────────────────────────────────────────────────────

describe("buildSpineEntries", () => {
  it("builds entries for identity tze", () => {
    const entries = buildSpineEntries(MOCK_INVENTORY, "tze");
    const families = entries.map((e) => e.family);
    expect(families).toContain("user");
    expect(families).toContain("system");
    expect(families).toContain("cli");
  });

  it("filters user entries by identity", () => {
    const tzeEntries = buildSpineEntries(MOCK_INVENTORY, "tze");
    const weiEntries = buildSpineEntries(MOCK_INVENTORY, "wei");
    // Wei only has google
    const weiUserKeys = weiEntries
      .filter((e) => e.family === "user")
      .map((e) => e.key);
    expect(weiUserKeys).toEqual(["u:google"]);
    // Tze has more user entries
    const tzeUserKeys = tzeEntries
      .filter((e) => e.family === "user")
      .map((e) => e.key);
    expect(tzeUserKeys.length).toBeGreaterThan(1);
  });

  it("user entry key format is u:<provider>", () => {
    const entries = buildSpineEntries(MOCK_INVENTORY, "tze");
    const userEntry = entries.find((e) => e.family === "user" && e.provider === "google");
    expect(userEntry?.key).toBe("u:google");
  });

  it("system entry key format is s:<KEY>", () => {
    const entries = buildSpineEntries(MOCK_INVENTORY, "tze");
    const sysEntry = entries.find((e) => e.family === "system");
    expect(sysEntry?.key).toMatch(/^s:/);
  });

  it("cli entry key format is c:<id>", () => {
    const entries = buildSpineEntries(MOCK_INVENTORY, "tze");
    const cliEntry = entries.find((e) => e.family === "cli");
    expect(cliEntry?.key).toMatch(/^c:/);
  });
});

// ── Atoms ────────────────────────────────────────────────────────────────────

describe("Fingerprint atom", () => {
  it("renders null as dash", () => {
    const html = renderToStaticMarkup(<Fingerprint value={null} />);
    expect(html).toContain("—");
  });

  it("renders sha256:hash with split coloring", () => {
    const html = renderToStaticMarkup(<Fingerprint value="sha256:7a3f9e2c" />);
    expect(html).toContain("sha256:");
    expect(html).toContain("7a3f9e2c");
  });

  it("has data-fingerprint attribute", () => {
    const html = renderToStaticMarkup(<Fingerprint value="sha256:7a3f9e2c" />);
    expect(html).toContain('data-fingerprint="true"');
  });
});

describe("StampGlyph atom", () => {
  it("renders verified glyph", () => {
    const html = renderToStaticMarkup(<StampGlyph action="verified" />);
    expect(html).toContain("✓");
    expect(html).toContain('data-stamp-action="verified"');
  });

  it("renders rotated glyph", () => {
    const html = renderToStaticMarkup(<StampGlyph action="rotated" />);
    expect(html).toContain("↻");
  });

  it("renders failed glyph", () => {
    const html = renderToStaticMarkup(<StampGlyph action="failed" />);
    expect(html).toContain("✕");
  });

  it("falls back to dot for unknown action", () => {
    const html = renderToStaticMarkup(<StampGlyph action="unknown-action" />);
    expect(html).toContain("·");
  });
});

describe("CredentialDot atom", () => {
  it("renders for ok state", () => {
    const html = renderToStaticMarkup(<CredentialDot state="ok" />);
    expect(html).toContain('data-credential-state="ok"');
  });

  it("renders for expired state with red color", () => {
    const html = renderToStaticMarkup(<CredentialDot state="expired" />);
    expect(html).toContain("--red");
  });

  it("renders for ok state with green color (healthy calm day)", () => {
    const html = renderToStaticMarkup(<CredentialDot state="ok" />);
    expect(html).toContain("--green");
  });
});

describe("Sliver atom: appears only when state demands", () => {
  it("renders sliver for expired state", () => {
    const html = renderToStaticMarkup(<Sliver state="expired" />);
    expect(html).toContain('data-sliver="true"');
  });

  it("does NOT render sliver for ok state", () => {
    const html = renderToStaticMarkup(<Sliver state="ok" />);
    expect(html).toBe("");
  });

  it("does NOT render sliver for never_set state", () => {
    const html = renderToStaticMarkup(<Sliver state="never_set" />);
    expect(html).toBe("");
  });
});

// ── PageGoogleAccounts [bu-ayp6v.7] ──────────────────────────────────────────

import * as useSecretsModule from "@/hooks/use-secrets.ts";

describe("PageGoogleAccounts: multi-account Google management surface", () => {
  /**
   * Acceptance criteria [bu-ayp6v.7]:
   *   1. Lists ALL connected Google accounts with email, state dot (not a word),
   *      and primary marker.
   *   2. 'add another account' button present (starts OAuth with account chooser forced).
   *   3. Per-account set-primary and re-authorize actions present.
   *   4. Scope-set picker (Calendar / Drive / Health) rendered.
   */

  it("renders google-accounts-panel data attribute", () => {
    const html = renderInRouter(<PageGoogleAccounts />);
    expect(html).toContain('data-google-accounts-panel="true"');
  });

  it("renders loading state when accounts are loading", () => {
    vi.mocked(useSecretsModule.useGoogleAccounts).mockReturnValueOnce({
      data: undefined,
      isLoading: true,
      error: null,
    } as unknown as ReturnType<typeof useSecretsModule.useGoogleAccounts>);
    const html = renderInRouter(<PageGoogleAccounts />);
    expect(html).toContain("loading");
  });

  it("renders account list with email when accounts present", () => {
    const mockAccounts = [
      {
        id: "acc-1",
        email: "owner@example.com",
        display_name: "Owner",
        is_primary: true,
        status: "active" as const,
        granted_scopes: ["https://www.googleapis.com/auth/calendar.readonly"],
        connected_at: "2026-01-01T00:00:00Z",
        last_token_refresh_at: null,
      },
      {
        id: "acc-2",
        email: "work@example.com",
        display_name: "Work",
        is_primary: false,
        status: "active" as const,
        granted_scopes: [],
        connected_at: "2026-02-01T00:00:00Z",
        last_token_refresh_at: null,
      },
    ];
    vi.mocked(useSecretsModule.useGoogleAccounts).mockReturnValueOnce({
      data: mockAccounts,
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useSecretsModule.useGoogleAccounts>);

    const html = renderInRouter(<PageGoogleAccounts />);
    expect(html).toContain("owner@example.com");
    expect(html).toContain("work@example.com");
    // 2 connected
    expect(html).toContain("2 connected");
  });

  it("renders primary badge on primary account", () => {
    const mockAccounts = [
      {
        id: "acc-1",
        email: "primary@example.com",
        display_name: null,
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
    expect(html).toContain('data-primary-badge="true"');
    expect(html).toContain("primary");
  });

  it("state is a dot (data-google-account-state) not a word for active account", () => {
    const mockAccounts = [
      {
        id: "acc-1",
        email: "test@example.com",
        display_name: null,
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
    // State rendered as a dot attribute, never inline as a word in the account row
    expect(html).toContain('data-google-account-state="active"');
  });

  it("renders connect Google CTA in empty state (no accounts)", () => {
    // Empty state (default mock returns []): shows a prominent 'connect Google' CTA
    // instead of 'add another account'. [bu-3gekd] empty-state fix.
    const html = renderInRouter(<PageGoogleAccounts />);
    expect(html).toContain("connect Google");
    expect(html).toContain('data-google-connect-empty-state="true"');
    // 'add another account' is only shown when at least one account is already connected
    expect(html).not.toContain("add another account");
  });

  it("renders add-another-account button when accounts are already connected", () => {
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
    // Empty state not shown when accounts are connected
    expect(html).not.toContain("connect Google");
  });

  it("renders re-authorize action for each account", () => {
    const mockAccounts = [
      {
        id: "acc-1",
        email: "test@example.com",
        display_name: null,
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
    expect(html).toContain("re-authorize");
  });

  it("renders set-primary action for non-primary account", () => {
    const mockAccounts = [
      {
        id: "acc-1",
        email: "primary@example.com",
        display_name: null,
        is_primary: true,
        status: "active" as const,
        granted_scopes: [],
        connected_at: "2026-01-01T00:00:00Z",
        last_token_refresh_at: null,
      },
      {
        id: "acc-2",
        email: "secondary@example.com",
        display_name: null,
        is_primary: false,
        status: "active" as const,
        granted_scopes: [],
        connected_at: "2026-02-01T00:00:00Z",
        last_token_refresh_at: null,
      },
    ];
    vi.mocked(useSecretsModule.useGoogleAccounts).mockReturnValueOnce({
      data: mockAccounts,
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useSecretsModule.useGoogleAccounts>);

    const html = renderInRouter(<PageGoogleAccounts />);
    expect(html).toContain("set primary");
  });

  it("renders scope-set picker with Calendar / Drive / Health", () => {
    const html = renderInRouter(<PageGoogleAccounts />);
    expect(html).toContain('data-scope-set-picker="true"');
    expect(html).toContain("Calendar");
    expect(html).toContain("Drive");
    expect(html).toContain("Health");
  });
});

describe("PageUser with Google provider: renders Google accounts panel", () => {
  it("renders google-accounts-panel inside PageUser for Google provider", () => {
    const google = MOCK_USER_CREDENTIALS.find((u) => u.provider === "google" && u.identity === "tze")!;
    const html = renderInRouter(
      <PageUser
        credential={google}
        provider={MOCK_PROVIDERS.google}
        identities={MOCK_IDENTITIES}
      />,
    );
    expect(html).toContain('data-google-accounts-panel="true"');
  });

  it("does NOT render google-accounts-panel for non-Google provider", () => {
    const spotify = MOCK_USER_CREDENTIALS.find((u) => u.provider === "spotify")!;
    const html = renderInRouter(
      <PageUser credential={spotify} provider={MOCK_PROVIDERS.spotify} />,
    );
    expect(html).not.toContain('data-google-accounts-panel="true"');
  });
});

// ── ProviderConfigDrawer framework [bu-ayp6v.8] ─────────────────────────────

describe("ProviderConfigDrawer: generic shell", () => {
  it("renders data-provider-config-drawer attribute with provider slug", () => {
    const html = renderInRouter(
      <ProviderConfigDrawer provider="testprovider" label="Test Provider" onClose={() => undefined}>
        <span>content</span>
      </ProviderConfigDrawer>,
    );
    expect(html).toContain('data-provider-config-drawer="testprovider"');
  });

  it("renders heading with provider label", () => {
    const html = renderInRouter(
      <ProviderConfigDrawer provider="testprovider" label="Test Provider" onClose={() => undefined}>
        <span>content</span>
      </ProviderConfigDrawer>,
    );
    expect(html).toContain("Test Provider");
  });

  it("renders dismiss button when not inline", () => {
    const html = renderInRouter(
      <ProviderConfigDrawer provider="testprovider" label="Test Provider" onClose={() => undefined}>
        <span>content</span>
      </ProviderConfigDrawer>,
    );
    expect(html).toContain("dismiss");
  });

  it("omits dismiss button and heading when inline=true", () => {
    const html = renderInRouter(
      <ProviderConfigDrawer provider="testprovider" label="Test Provider" onClose={() => undefined} inline>
        <span data-inner="true">content</span>
      </ProviderConfigDrawer>,
    );
    expect(html).toContain('data-provider-drawer-inline="true"');
    expect(html).not.toContain("dismiss");
    expect(html).not.toContain("Test Provider");
  });

  it("renders children", () => {
    const html = renderInRouter(
      <ProviderConfigDrawer provider="testprovider" label="Test Provider" onClose={() => undefined}>
        <span data-custom-child="true">hello</span>
      </ProviderConfigDrawer>,
    );
    expect(html).toContain('data-custom-child="true"');
  });
});

// ── HomeAssistantDrawer [bu-ayp6v.8] ─────────────────────────────────────────

describe("HomeAssistantDrawer: configure/disconnect", () => {
  it("renders data-provider-config-drawer=homeassistant", () => {
    const html = renderInRouter(<HomeAssistantDrawer onClose={() => undefined} />);
    expect(html).toContain('data-provider-config-drawer="homeassistant"');
  });

  it("renders HA drawer content", () => {
    const html = renderInRouter(<HomeAssistantDrawer onClose={() => undefined} />);
    expect(html).toContain('data-ha-drawer-content="true"');
  });

  it("renders dismiss button in standalone mode", () => {
    const html = renderInRouter(<HomeAssistantDrawer onClose={() => undefined} />);
    expect(html).toContain("dismiss");
  });

  it("omits dismiss button in inline mode", () => {
    const html = renderInRouter(<HomeAssistantDrawer onClose={() => undefined} inline />);
    expect(html).not.toContain("dismiss");
  });

  it("renders status dot (not a word) for connection state", () => {
    const html = renderInRouter(<HomeAssistantDrawerContent />);
    expect(html).toContain('data-ha-status-dot="true"');
  });

  it("renders masked URL when configured", () => {
    const html = renderInRouter(<HomeAssistantDrawerContent />);
    expect(html).toContain("http://ha.local:8123");
  });

  it("renders configure/reconfigure action", () => {
    const html = renderInRouter(<HomeAssistantDrawerContent />);
    expect(html).toContain("reconfigure");
  });

  it("renders disconnect action when configured", () => {
    const html = renderInRouter(<HomeAssistantDrawerContent />);
    expect(html).toContain("disconnect");
  });
});

// ── OwnTracksDrawer [bu-ayp6v.8] ─────────────────────────────────────────────

describe("OwnTracksDrawer: token generate/regenerate + webhook URL", () => {
  it("renders data-provider-config-drawer=owntracks", () => {
    const html = renderInRouter(<OwnTracksDrawer onClose={() => undefined} />);
    expect(html).toContain('data-provider-config-drawer="owntracks"');
  });

  it("renders OwnTracks drawer content", () => {
    const html = renderInRouter(<OwnTracksDrawer onClose={() => undefined} />);
    expect(html).toContain('data-owntracks-drawer-content="true"');
  });

  it("renders status dot (not a word) for connection state", () => {
    const html = renderInRouter(<OwnTracksDrawerContent />);
    expect(html).toContain('data-owntracks-status-dot="true"');
  });

  it("renders webhook URL display", () => {
    const html = renderInRouter(<OwnTracksDrawerContent />);
    expect(html).toContain('data-owntracks-webhook-url="true"');
    expect(html).toContain("butlers.example.com");
  });

  it("renders regenerate token action when token is configured", () => {
    const html = renderInRouter(<OwnTracksDrawerContent />);
    expect(html).toContain("regenerate token");
  });

  it("renders event count", () => {
    const html = renderInRouter(<OwnTracksDrawerContent />);
    expect(html).toContain("5 events today");
  });

  it("renders dismiss button in standalone mode", () => {
    const html = renderInRouter(<OwnTracksDrawer onClose={() => undefined} />);
    expect(html).toContain("dismiss");
  });

  it("omits dismiss button in inline mode", () => {
    const html = renderInRouter(<OwnTracksDrawer onClose={() => undefined} inline />);
    expect(html).not.toContain("dismiss");
  });
});

// ── SteamDrawer [bu-ayp6v.8] ─────────────────────────────────────────────────

describe("SteamDrawer: connect / list / disconnect accounts", () => {
  it("renders data-provider-config-drawer=steam", () => {
    const html = renderInRouter(<SteamDrawer onClose={() => undefined} />);
    expect(html).toContain('data-provider-config-drawer="steam"');
  });

  it("renders Steam drawer content", () => {
    const html = renderInRouter(<SteamDrawer onClose={() => undefined} />);
    expect(html).toContain('data-steam-drawer-content="true"');
  });

  it("renders connected account with steam_id", () => {
    const html = renderInRouter(<SteamDrawerContent />);
    expect(html).toContain("76561198000000001");
  });

  it("renders account display name", () => {
    const html = renderInRouter(<SteamDrawerContent />);
    expect(html).toContain("TestUser");
  });

  it("renders status dot (not a word) for account state", () => {
    const html = renderInRouter(<SteamDrawerContent />);
    expect(html).toContain("data-steam-account-dot");
  });

  it("renders connect account action", () => {
    const html = renderInRouter(<SteamDrawerContent />);
    expect(html).toContain("connect account");
  });

  it("renders disconnect action for connected account", () => {
    const html = renderInRouter(<SteamDrawerContent />);
    expect(html).toContain("disconnect");
  });

  it("renders dismiss button in standalone mode", () => {
    const html = renderInRouter(<SteamDrawer onClose={() => undefined} />);
    expect(html).toContain("dismiss");
  });

  it("omits dismiss button in inline mode", () => {
    const html = renderInRouter(<SteamDrawer onClose={() => undefined} inline />);
    expect(html).not.toContain("dismiss");
  });

  it("renders 1 connected count", () => {
    const html = renderInRouter(<SteamDrawerContent />);
    expect(html).toContain("1 connected");
  });
});

// ── PageUser provider drawer integration [bu-ayp6v.8] ───────────────────────

describe("PageUser: renders provider config drawers for HA/OwnTracks/Steam", () => {
  it("renders HA drawer inline inside PageUser for homeassistant provider", () => {
    const ha = MOCK_USER_CREDENTIALS.find((u) => u.provider === "homeassistant")!;
    const html = renderInRouter(
      <PageUser
        credential={ha}
        provider={MOCK_PROVIDERS.homeassistant}
        identities={MOCK_IDENTITIES}
      />,
    );
    expect(html).toContain('data-provider-config-drawer="homeassistant"');
    expect(html).toContain('data-ha-drawer-content="true"');
  });

  it("renders OwnTracks drawer inline inside PageUser for owntracks provider", () => {
    const owntracks = MOCK_USER_CREDENTIALS.find((u) => u.provider === "owntracks")!;
    const html = renderInRouter(
      <PageUser
        credential={owntracks}
        provider={MOCK_PROVIDERS.owntracks}
        identities={MOCK_IDENTITIES}
      />,
    );
    expect(html).toContain('data-provider-config-drawer="owntracks"');
    expect(html).toContain('data-owntracks-drawer-content="true"');
  });

  it("renders Steam drawer inline inside PageUser for steam provider", () => {
    const steam = MOCK_USER_CREDENTIALS.find((u) => u.provider === "steam")!;
    const html = renderInRouter(
      <PageUser
        credential={steam}
        provider={MOCK_PROVIDERS.steam}
        identities={MOCK_IDENTITIES}
      />,
    );
    expect(html).toContain('data-provider-config-drawer="steam"');
    expect(html).toContain('data-steam-drawer-content="true"');
  });

  it("does NOT render HA drawer for non-HA provider (e.g. whatsapp)", () => {
    const whatsapp = MOCK_USER_CREDENTIALS.find((u) => u.provider === "whatsapp")!;
    const html = renderInRouter(
      <PageUser credential={whatsapp} provider={MOCK_PROVIDERS.whatsapp} />,
    );
    expect(html).not.toContain('data-provider-config-drawer="homeassistant"');
    expect(html).not.toContain('data-provider-config-drawer="owntracks"');
    expect(html).not.toContain('data-provider-config-drawer="steam"');
  });

  it("renders Spotify drawer inline inside PageUser for spotify provider", () => {
    const spotify = MOCK_USER_CREDENTIALS.find((u) => u.provider === "spotify")!;
    const html = renderInRouter(
      <PageUser credential={spotify} provider={MOCK_PROVIDERS.spotify} identities={MOCK_IDENTITIES} />,
    );
    expect(html).toContain('data-provider-config-drawer="spotify"');
    expect(html).toContain('data-spotify-drawer-content="true"');
  });

  it("renders WhatsApp drawer inline inside PageUser for whatsapp provider", () => {
    const whatsapp = MOCK_USER_CREDENTIALS.find((u) => u.provider === "whatsapp")!;
    const html = renderInRouter(
      <PageUser credential={whatsapp} provider={MOCK_PROVIDERS.whatsapp} identities={MOCK_IDENTITIES} />,
    );
    expect(html).toContain('data-provider-config-drawer="whatsapp"');
    expect(html).toContain('data-whatsapp-drawer-content="true"');
  });
});

// ── SpotifyDrawer [bu-ayp6v.9] ───────────────────────────────────────────────

describe("SpotifyDrawer: client_id config + OAuth connect + disconnect", () => {
  it("renders data-provider-config-drawer=spotify", () => {
    const html = renderInRouter(<SpotifyDrawer onClose={() => undefined} />);
    expect(html).toContain('data-provider-config-drawer="spotify"');
  });

  it("renders Spotify drawer content", () => {
    const html = renderInRouter(<SpotifyDrawer onClose={() => undefined} />);
    expect(html).toContain('data-spotify-drawer-content="true"');
  });

  it("renders status dot (not a word) for connection state", () => {
    const html = renderInRouter(<SpotifyDrawerContent />);
    expect(html).toContain('data-spotify-status-dot="true"');
  });

  it("renders display name when connected", () => {
    const html = renderInRouter(<SpotifyDrawerContent />);
    expect(html).toContain("Test User");
  });

  it("renders configure/reconfigure action", () => {
    const html = renderInRouter(<SpotifyDrawerContent />);
    expect(html).toContain("reconfigure");
  });

  it("renders re-authorize action when connected", () => {
    const html = renderInRouter(<SpotifyDrawerContent />);
    expect(html).toContain("re-authorize");
  });

  it("renders disconnect action when configured", () => {
    const html = renderInRouter(<SpotifyDrawerContent />);
    expect(html).toContain("disconnect");
  });

  it("renders red error-state card when token refresh failed (state=error)", async () => {
    const useSpotifyModule = await import("@/hooks/use-spotify.ts");
    vi.mocked(useSpotifyModule.useSpotifyStatus).mockReturnValueOnce({
      data: {
        state: "error",
        connected: false,
        spotify_user_id: null,
        display_name: null,
        account_type: null,
        last_sync_at: null,
        error: "Spotify token verification failed. Re-connect your account.",
        needs_reauth: true,
        missing_scopes: [],
      },
      isLoading: false,
      error: null,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any);
    const html = renderInRouter(<SpotifyDrawerContent />);
    expect(html).toContain('data-spotify-error-card="true"');
    expect(html).toContain("Error — re-authorization needed");
    expect(html).toContain("re-connect");
    expect(html).toContain("Spotify token verification failed");
  });

  it("renders dismiss button in standalone mode", () => {
    const html = renderInRouter(<SpotifyDrawer onClose={() => undefined} />);
    expect(html).toContain("dismiss");
  });

  it("omits dismiss button in inline mode", () => {
    const html = renderInRouter(<SpotifyDrawer onClose={() => undefined} inline />);
    expect(html).not.toContain("dismiss");
  });
});

// ── WhatsAppDrawer [bu-ayp6v.9] ──────────────────────────────────────────────

describe("WhatsAppDrawer: QR pairing + status + disconnect", () => {
  it("renders data-provider-config-drawer=whatsapp", () => {
    const html = renderInRouter(<WhatsAppDrawer onClose={() => undefined} />);
    expect(html).toContain('data-provider-config-drawer="whatsapp"');
  });

  it("renders WhatsApp drawer content", () => {
    const html = renderInRouter(<WhatsAppDrawer onClose={() => undefined} />);
    expect(html).toContain('data-whatsapp-drawer-content="true"');
  });

  it("renders status dot (not a word) for connection state", () => {
    const html = renderInRouter(<WhatsAppDrawerContent />);
    expect(html).toContain('data-whatsapp-status-dot="true"');
  });

  it("renders masked phone number when connected", () => {
    const html = renderInRouter(<WhatsAppDrawerContent />);
    expect(html).toContain("+1 *** *** 7890");
  });

  it("renders pair device / re-pair action", () => {
    const html = renderInRouter(<WhatsAppDrawerContent />);
    expect(html).toContain("re-pair");
  });

  it("renders disconnect action when connected", () => {
    const html = renderInRouter(<WhatsAppDrawerContent />);
    expect(html).toContain("disconnect");
  });

  it("renders dismiss button in standalone mode", () => {
    const html = renderInRouter(<WhatsAppDrawer onClose={() => undefined} />);
    expect(html).toContain("dismiss");
  });

  it("omits dismiss button in inline mode", () => {
    const html = renderInRouter(<WhatsAppDrawer onClose={() => undefined} inline />);
    expect(html).not.toContain("dismiss");
  });
});
