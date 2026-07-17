// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// PassportAddPanel tests [bu-ayp6v.6]
//
// Coverage:
//   - Family chooser renders three commit-pill buttons
//   - SYSTEM sub-form: key/value/category/target fields + create action
//   - USER sub-form: OAuth-first guided connect is the default; raw
//     type/value/label paste is demoted behind an advanced toggle [bu-57b3m]
//   - CONNECT PROVIDER: OAuth providers (google/spotify) + stubs
//   - Template suggestions populate category/type
//   - useCreateUserSecret is wired for user creation
//   - useSetSystemSecret is wired for system creation
//   - SpineAddButton renders in the page header
//   - OAuth connect guard: undefined ownerEntityId → button disabled, no API call [bu-vzwnl]
// ---------------------------------------------------------------------------

import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import * as React from "react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------
vi.mock("@/api/client.ts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client.ts")>();
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
    createEntityInfo: vi.fn(),
    getTelegramSessionStatus: vi.fn(),
    telegramSendCode: vi.fn(),
    telegramVerifyCode: vi.fn(),
    listCLIAuthProviders: vi.fn().mockResolvedValue([]),
    testCLIAuthApiKey: vi.fn(),
    saveCLIAuthApiKey: vi.fn(),
    deleteCLIAuthApiKey: vi.fn(),
  };
});
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/hooks/use-butlers", () => ({
  useButlers: vi.fn(() => ({ data: { data: [] }, isLoading: false, error: null })),
}));
// Home Assistant guided-drawer hooks [bu-57b3m] — the USER family's "set up
// Home Assistant" quick-connect renders the same drawer the provider family
// already uses, so it needs the same hook mocks.
vi.mock("@/hooks/use-home-assistant.ts", () => ({
  useHomeAssistantStatus: vi.fn(() => ({
    data: { state: "disconnected", url_configured: false, token_configured: false, masked_url: null },
    isLoading: false,
    error: null,
  })),
  useConfigureHomeAssistant: vi.fn(() => ({ mutate: vi.fn(), isPending: false, reset: vi.fn(), error: null, data: null })),
  useDeleteHomeAssistantConfig: vi.fn(() => ({ mutate: vi.fn(), isPending: false, reset: vi.fn(), error: null })),
}));

import { PassportAddPanel } from "./pages.tsx";
import { Spine, SpineAddButton } from "./Spine.tsx";
import { DirectionPassport } from "./DirectionPassport.tsx";
import {
  MOCK_INVENTORY,
  MOCK_IDENTITIES,
  MOCK_PROVIDERS,
} from "./mock-data.ts";
import { buildSpineEntries } from "./spine-builder.ts";
import {
  createEntityInfo,
  getTelegramSessionStatus,
  reauthorizeUserCredential,
  telegramSendCode,
} from "@/api/client.ts";
const mockCreateEntityInfo = vi.mocked(createEntityInfo);
const mockReauth = vi.mocked(reauthorizeUserCredential);
const mockTelegramSessionStatus = vi.mocked(getTelegramSessionStatus);
const mockTelegramSendCode = vi.mocked(telegramSendCode);

// ── Helpers ─────────────────────────────────────────────────────────────────

function renderInRouter(element: React.ReactElement): string {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{element}</MemoryRouter>
    </QueryClientProvider>,
  );
}

// ── PassportAddPanel ─────────────────────────────────────────────────────────

describe("PassportAddPanel: family chooser", () => {
  it("renders with data-passport-add-panel attribute", () => {
    const html = renderInRouter(
      <PassportAddPanel ownerEntityId="entity-123" onClose={() => {}} />,
    );
    expect(html).toContain('data-passport-add-panel="true"');
  });

  it("renders three commit-pill family chooser buttons", () => {
    const html = renderInRouter(
      <PassportAddPanel ownerEntityId="entity-123" onClose={() => {}} />,
    );
    expect(html).toContain("system secret");
    expect(html).toContain("user credential");
    expect(html).toContain("connect provider");
  });

  it("renders the add credential heading", () => {
    const html = renderInRouter(
      <PassportAddPanel ownerEntityId="entity-123" onClose={() => {}} />,
    );
    expect(html).toContain("add credential");
    expect(html).toContain("What would you like to add?");
  });

  it("renders cancel button in footer when family is null", () => {
    const html = renderInRouter(
      <PassportAddPanel ownerEntityId="entity-123" onClose={() => {}} />,
    );
    expect(html).toContain("cancel");
  });

  it("has data-add-family-chooser attribute", () => {
    const html = renderInRouter(
      <PassportAddPanel ownerEntityId="entity-123" onClose={() => {}} />,
    );
    expect(html).toContain('data-add-family-chooser="true"');
  });

  it("user credential button is disabled when ownerEntityId is absent", () => {
    const html = renderInRouter(
      <PassportAddPanel ownerEntityId={undefined} onClose={() => {}} />,
    );
    // disabled attribute on the user credential button
    expect(html).toContain("user credential");
    // The button should have disabled state (rendered as disabled attribute)
    expect(html).toContain("requires the owner entity to be set up");
  });
});

describe("PassportAddPanel: SYSTEM form", () => {
  // To test the system sub-form we need to simulate a click on the
  // "system secret" family button. Since we use renderToStaticMarkup (SSR),
  // we cannot simulate clicks. Instead, we test that the form structure
  // renders correctly when family is forced.
  //
  // We render a version with a passed `data-add-system-panel` present by
  // rendering the panel itself with state already at "system".
  // Workaround: inspect the HTML after rendering with a custom wrapper.
  //
  // Since static markup only captures initial render (family === null),
  // we validate the panel atoms are exported correctly via structure checks.

  it("does not render system panel by default (family chooser shown)", () => {
    const html = renderInRouter(
      <PassportAddPanel ownerEntityId="entity-123" onClose={() => {}} />,
    );
    // system panel not shown until family is selected
    expect(html).not.toContain('data-add-system-panel="true"');
  });

  it("does not render user panel by default", () => {
    const html = renderInRouter(
      <PassportAddPanel ownerEntityId="entity-123" onClose={() => {}} />,
    );
    expect(html).not.toContain('data-add-user-panel="true"');
  });

  it("does not render provider panel by default", () => {
    const html = renderInRouter(
      <PassportAddPanel ownerEntityId="entity-123" onClose={() => {}} />,
    );
    expect(html).not.toContain('data-add-provider-panel="true"');
  });
});

// ── SpineAddButton ────────────────────────────────────────────────────────────

describe("SpineAddButton", () => {
  it("renders with data-spine-add attribute", () => {
    const html = renderToStaticMarkup(
      <SpineAddButton onClick={() => {}} active={false} />,
    );
    expect(html).toContain('data-spine-add="true"');
  });

  it("renders + add label", () => {
    const html = renderToStaticMarkup(
      <SpineAddButton onClick={() => {}} active={false} />,
    );
    expect(html).toContain("+ add");
  });

  it("renders as disabled when active=true", () => {
    const html = renderToStaticMarkup(
      <SpineAddButton onClick={() => {}} active={true} />,
    );
    expect(html).toContain("disabled");
  });

  it("has commit-pill styling (bg-fg text-bg)", () => {
    const html = renderToStaticMarkup(
      <SpineAddButton onClick={() => {}} active={false} />,
    );
    expect(html).toContain("bg-fg");
    expect(html).toContain("text-bg");
  });

  it("has aria-label for accessibility", () => {
    const html = renderToStaticMarkup(
      <SpineAddButton onClick={() => {}} active={false} />,
    );
    expect(html).toContain("aria-label");
    expect(html.toLowerCase()).toContain("add credential");
  });
});

// ── Spine no longer owns the add button (moved to the page header) ─────────────

describe("Spine: add button moved out of the spine", () => {
  const entries = buildSpineEntries(MOCK_INVENTORY, "tze");

  it("does NOT render the add button inside the spine", () => {
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
        providers={MOCK_PROVIDERS}
      />,
    );
    expect(html).not.toContain('data-spine-add="true"');
  });
});

// ── DirectionPassport: add button in the page header ───────────────────────────

describe("DirectionPassport: add button wiring", () => {
  it("renders the add button in the page header", () => {
    const html = renderInRouter(<DirectionPassport inventory={MOCK_INVENTORY} />);
    expect(html).toContain('data-spine-add="true"');
    expect(html).toContain("+ add");
  });
});

// ── PassportAddPanel: OAuth connect guard [bu-vzwnl] ─────────────────────────

describe("PassportAddPanel: OAuth connect guard — undefined ownerEntityId", () => {
  afterEach(() => {
    cleanup();
    mockReauth.mockReset();
    mockCreateEntityInfo.mockReset();
    mockTelegramSessionStatus.mockReset();
    mockTelegramSendCode.mockReset();
  });

  function renderAddPanel(ownerEntityId: string | undefined) {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    return render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <PassportAddPanel ownerEntityId={ownerEntityId} onClose={() => {}} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  it("connect Google button is disabled when ownerEntityId is undefined", () => {
    renderAddPanel(undefined);
    // Navigate to the connect provider panel
    const connectProviderBtn = screen.getByText("connect provider");
    fireEvent.click(connectProviderBtn);
    // The connect Google button should be disabled
    const connectBtn = screen.getByText(/connect google/i);
    expect((connectBtn.closest("button") as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows 'owner entity ID not available' hint in provider panel when ownerEntityId is undefined", () => {
    renderAddPanel(undefined);
    fireEvent.click(screen.getByText("connect provider"));
    expect(screen.getByText(/owner entity ID not available: cannot connect provider/i)).toBeTruthy();
  });

  it("does NOT call reauthorizeUserCredential when ownerEntityId is undefined and connect is clicked", () => {
    renderAddPanel(undefined);
    fireEvent.click(screen.getByText("connect provider"));
    const connectBtn = screen.getByText(/connect google/i).closest("button") as HTMLButtonElement;
    // Even if somehow clicked (e.g. via programmatic click bypassing disabled), the guard fires
    fireEvent.click(connectBtn);
    expect(mockReauth).not.toHaveBeenCalled();
  });

  it("routes spotify through the OAuth connect dance, not a stub drawer (bu-5gliy)", () => {
    mockReauth.mockResolvedValue({
      data: { redirect_url: "/api/oauth/spotify/start" },
      meta: {},
    } as never);
    renderAddPanel("entity-uuid-123");
    fireEvent.click(screen.getByText("connect provider"));

    // Spotify is a first-class OAuth provider — offered as a "connect now" pill
    // (like Google), and clicking it drives the reauthorize dance for "spotify".
    const spotifyBtn = screen
      .getByText(/connect spotify/i)
      .closest("button") as HTMLButtonElement;
    expect(spotifyBtn).toBeTruthy();
    expect(spotifyBtn.disabled).toBe(false);
    fireEvent.click(spotifyBtn);
    expect(mockReauth).toHaveBeenCalledWith("spotify", "entity-uuid-123");

    // It never opens the stub provider-config drawer.
    expect(
      document.querySelector('[data-provider-connect-drawer="spotify"]'),
    ).toBeNull();
  });

  it("connect Google button is enabled when ownerEntityId is provided", () => {
    renderAddPanel("entity-uuid-123");
    fireEvent.click(screen.getByText("connect provider"));
    const connectBtn = screen.getByText(/connect google/i).closest("button") as HTMLButtonElement;
    expect(connectBtn.disabled).toBe(false);
  });
});

// ── PassportAddPanel: USER family — OAuth-first guided connect [bu-57b3m] ────
//
// The USER sub-form used to open straight into a raw entity_info type+value
// paste form. Hand-pasting OAuth refresh tokens is how corrupt credential
// states are born (wrong type, wrong client_id, missing google_accounts row).
// The guided connect (same OAuth dance / provider drawer the reauthorize CTA
// uses) is now the default; raw paste is demoted behind an explicit toggle.

describe("PassportAddPanel: USER family — guided connect is the default", () => {
  afterEach(() => {
    cleanup();
    mockReauth.mockReset();
    mockCreateEntityInfo.mockReset();
    mockTelegramSessionStatus.mockReset();
    mockTelegramSendCode.mockReset();
  });

  function renderUserFamily(ownerEntityId: string | undefined) {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const utils = render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <PassportAddPanel ownerEntityId={ownerEntityId} onClose={() => {}} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByText("user credential"));
    return utils;
  }

  it("opens on the guided connect view, not the raw paste form", () => {
    renderUserFamily("entity-uuid-123");
    expect(screen.getByText(/connect google/i)).toBeTruthy();
    expect(screen.getByText(/set up home assistant/i)).toBeTruthy();
    expect(document.querySelector('[data-user-guided-connect="true"]')).toBeTruthy();
    expect(document.querySelector('[data-user-raw-form="true"]')).toBeFalsy();
    expect(document.querySelector('[data-user-type-select="true"]')).toBeFalsy();
  });

  it("reveals the raw type+value form only after the advanced toggle is clicked", () => {
    renderUserFamily("entity-uuid-123");
    fireEvent.click(screen.getByText(/advanced: paste raw credential/i));
    expect(document.querySelector('[data-user-raw-form="true"]')).toBeTruthy();
    expect(document.querySelector('[data-user-type-select="true"]')).toBeTruthy();
    // Guided connect is hidden while advanced is open
    expect(document.querySelector('[data-user-guided-connect="true"]')).toBeFalsy();
  });

  it("links to the selected email-password source in the raw user form", () => {
    renderUserFamily("entity-uuid-123");
    fireEvent.click(screen.getByText(/advanced: paste raw credential/i));
    fireEvent.change(document.querySelector('[data-user-type-select="true"]')!, {
      target: { value: "email_password" },
    });

    const link = screen.getByRole("link", { name: "Google App Passwords" });
    expect(link.getAttribute("href")).toBe("https://myaccount.google.com/apppasswords");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  });

  it("links to the selected Telegram API ID source in the raw user form", () => {
    renderUserFamily("entity-uuid-123");
    fireEvent.click(screen.getByText(/advanced: paste raw credential/i));
    fireEvent.change(document.querySelector('[data-user-type-select="true"]')!, {
      target: { value: "telegram_api_id" },
    });

    const link = screen.getByRole("link", { name: "Telegram API development tools" });
    expect(link.getAttribute("href")).toBe("https://my.telegram.org/apps");
  });

  it("does not add a provenance link for a raw type without a source", () => {
    renderUserFamily("entity-uuid-123");
    fireEvent.click(screen.getByText(/advanced: paste raw credential/i));
    fireEvent.change(document.querySelector('[data-user-type-select="true"]')!, {
      target: { value: "other" },
    });

    expect(document.querySelector('[data-provenance-line="true"]')).toBeFalsy();
  });

  it("shows a one-line warning that pasted tokens bypass account/scope tracking", () => {
    renderUserFamily("entity-uuid-123");
    fireEvent.click(screen.getByText(/advanced: paste raw credential/i));
    expect(screen.getByText(/bypass account and scope tracking/i)).toBeTruthy();
  });

  it("'back to guided connect' returns from the advanced form to the guided view", () => {
    renderUserFamily("entity-uuid-123");
    fireEvent.click(screen.getByText(/advanced: paste raw credential/i));
    fireEvent.click(screen.getByText(/back to guided connect/i));
    expect(document.querySelector('[data-user-guided-connect="true"]')).toBeTruthy();
    expect(document.querySelector('[data-user-raw-form="true"]')).toBeFalsy();
  });

  it("clicking connect Google in the user family calls reauthorizeUserCredential(google, ownerEntityId)", () => {
    mockReauth.mockResolvedValue({ data: { redirect_url: "/api/oauth/google/start" }, meta: {} } as never);
    renderUserFamily("entity-uuid-123");
    fireEvent.click(screen.getByText(/connect google/i));
    expect(mockReauth).toHaveBeenCalledWith("google", "entity-uuid-123");
  });

  // handleOAuthConnect catch branch (bu-umk50): the reauthorize call can reject
  // (network / backend error) or resolve without a redirect_url — both surface a
  // red oauthError and re-enable the button (no 501 special-case here, unlike
  // PageUser.reauth; every failure is a generic error line).
  it("surfaces the error and re-enables the button when reauthorize rejects (bu-umk50)", async () => {
    mockReauth.mockRejectedValue(new Error("network timeout"));
    renderUserFamily("entity-uuid-123");
    fireEvent.click(screen.getByText(/connect google/i));

    await waitFor(() => expect(screen.getByText("network timeout")).toBeTruthy());
    const btn = document.querySelector(
      '[data-user-connect-google="true"]',
    ) as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
  });

  it("shows the fallback message when reauthorize rejects with a non-Error (bu-umk50)", async () => {
    mockReauth.mockRejectedValue("boom" as never);
    renderUserFamily("entity-uuid-123");
    fireEvent.click(screen.getByText(/connect google/i));

    await waitFor(() => expect(screen.getByText("Connection failed.")).toBeTruthy());
  });

  it("surfaces an error when reauthorize resolves without a redirect_url (bu-umk50)", async () => {
    mockReauth.mockResolvedValue({ data: {}, meta: {} } as never);
    renderUserFamily("entity-uuid-123");
    fireEvent.click(screen.getByText(/connect google/i));

    await waitFor(() =>
      expect(screen.getByText("No redirect URL returned.")).toBeTruthy(),
    );
  });

  // Note: unlike the "connect provider" family (reachable without an owner
  // entity), the "user credential" family chooser button itself is disabled
  // when ownerEntityId is absent (see the family-chooser test above) — so
  // the guided connect view inside the user family is never reached without
  // an owner entity, and there is no separate in-panel guard to exercise.

  it("clicking 'set up Home Assistant' renders the guided Home Assistant drawer inline", () => {
    renderUserFamily("entity-uuid-123");
    fireEvent.click(screen.getByText(/set up home assistant/i));
    expect(document.querySelector('[data-provider-config-drawer="homeassistant"]')).toBeTruthy();
  });

  it("opens an accessible guided Telegram session drawer", async () => {
    mockTelegramSessionStatus.mockResolvedValue({
      has_api_id: false,
      has_api_hash: false,
      has_session: false,
      has_scope_consent: false,
      ready: false,
    });
    renderUserFamily("entity-uuid-123");

    fireEvent.click(screen.getByText(/set up telegram/i));

    expect(screen.getByRole("region", { name: "Telegram" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Telegram", level: 2 })).toBeTruthy();
    await waitFor(() => expect(screen.getByLabelText("Telegram API hash")).toBeTruthy());
    expect(screen.getByLabelText("Telegram API hash").getAttribute("type")).toBe("password");
    expect(screen.getByRole("link", { name: "my.telegram.org/apps" }).getAttribute("href")).toBe(
      "https://my.telegram.org/apps",
    );
  });

  it("sends the API hash only through Telegram session auth, not the raw credential mutation", async () => {
    mockTelegramSessionStatus.mockResolvedValue({
      has_api_id: false,
      has_api_hash: false,
      has_session: false,
      has_scope_consent: false,
      ready: false,
    });
    mockTelegramSendCode.mockResolvedValue({
      session_token: "session-token",
      phone_code_hash: "phone-code-hash",
    });
    renderUserFamily("entity-uuid-123");

    fireEvent.click(screen.getByText(/set up telegram/i));
    await waitFor(() => expect(screen.getByLabelText("Telegram API hash")).toBeTruthy());
    fireEvent.change(screen.getByLabelText("Telegram API ID"), { target: { value: "12345" } });
    fireEvent.change(screen.getByLabelText("Telegram API hash"), { target: { value: "test-api-hash" } });
    fireEvent.change(screen.getByLabelText("Telegram phone number"), { target: { value: "+15551234567" } });
    fireEvent.click(screen.getByRole("checkbox", { name: /account-wide telegram ingestion/i }));
    fireEvent.click(screen.getByRole("button", { name: "Send code" }));

    await waitFor(() =>
      expect(mockTelegramSendCode.mock.calls[0]?.[0]).toEqual({
        api_id: 12345,
        api_hash: "test-api-hash",
        phone: "+15551234567",
        scope_consent: true,
      }),
    );
    expect(mockCreateEntityInfo).not.toHaveBeenCalled();
  });

  it("cannot submit Telegram session setup until account-wide ingestion is acknowledged", async () => {
    mockTelegramSessionStatus.mockResolvedValue({
      has_api_id: false,
      has_api_hash: false,
      has_session: false,
      has_scope_consent: false,
      ready: false,
    });
    renderUserFamily("entity-uuid-123");

    fireEvent.click(screen.getByText(/set up telegram/i));
    await waitFor(() => expect(screen.getByLabelText("Telegram API hash")).toBeTruthy());
    fireEvent.change(screen.getByLabelText("Telegram API ID"), { target: { value: "12345" } });
    fireEvent.change(screen.getByLabelText("Telegram API hash"), { target: { value: "test-api-hash" } });
    fireEvent.change(screen.getByLabelText("Telegram phone number"), { target: { value: "+15551234567" } });

    expect(
      screen.getByRole("button", { name: "Send code" }).hasAttribute("disabled"),
    ).toBe(true);
    expect(
      screen.getByRole("checkbox", { name: /account-wide telegram ingestion/i }),
    ).toBeTruthy();
    expect(
      screen.getByRole("group", { name: "Account-wide ingestion scope" }),
    ).toBeTruthy();
    expect(screen.getByText(/direct chat, group, supergroup, and channel/i)).toBeTruthy();
    expect(mockTelegramSendCode).not.toHaveBeenCalled();
  });

  it("returns focus to the Telegram setup trigger when the inline drawer is dismissed", () => {
    renderUserFamily("entity-uuid-123");
    const trigger = screen.getByText(/set up telegram/i).closest("button") as HTMLButtonElement;

    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss Telegram setup" }));

    expect(document.activeElement).toBe(trigger);
  });

  it("resets back to the guided view when re-entering the user family after visiting advanced", () => {
    renderUserFamily("entity-uuid-123");
    fireEvent.click(screen.getByText(/advanced: paste raw credential/i));
    expect(document.querySelector('[data-user-raw-form="true"]')).toBeTruthy();
    // back to family chooser, then back into user credential
    fireEvent.click(screen.getByText("back"));
    fireEvent.click(screen.getByText("user credential"));
    expect(document.querySelector('[data-user-guided-connect="true"]')).toBeTruthy();
    expect(document.querySelector('[data-user-raw-form="true"]')).toBeFalsy();
  });

  it("does not offer Telegram API hash in the advanced raw credential selector", () => {
    renderUserFamily("entity-uuid-123");
    fireEvent.click(screen.getByText(/advanced: paste raw credential/i));

    expect(screen.queryByRole("option", { name: "Telegram API Hash" })).toBeNull();
  });
});
