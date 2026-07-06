// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// PageCli — action-wiring tests [bu-ayp6v.5]
//
// Coverage:
//   - rotate: opens a danger confirm (bu-xn1sr) before calling
//     useRotateCliRuntime; shows copy-once panel on success
//   - revoke: shows danger confirm; calls useRevokeCliRuntime on confirm
//   - api-key save (isApiKeyMode): opens set-token panel, calls useSaveCLIAuthApiKey
//   - api-key delete (isApiKeyMode): calls useDeleteCLIAuthApiKey
//   - test button: calls useTestCLIAuthApiKey
//   - cli-auth/* mirror rows (bu-xn1sr): no rotate/generate button; paste-only
//     "update token" instead
// ---------------------------------------------------------------------------

import { describe, expect, it, vi, afterEach, beforeEach } from "vitest";
import { render, screen, fireEvent, act, cleanup, waitFor } from "@testing-library/react";
import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { PageCli } from "./pages.tsx";
import type { CliCredential } from "./types.ts";
import type { CliDeviceAuthState } from "@/hooks/use-cli-auth.ts";

// ---------------------------------------------------------------------------
// Mocks — vi.mock is hoisted; factory must not reference outer variables.
// ---------------------------------------------------------------------------

vi.mock("@/api/client.ts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client.ts")>();
  return {
    ...actual,
    rotateCliCredential: vi.fn(),
    revokeCliCredential: vi.fn(),
    reauthorizeCliCredential: vi.fn(),
    testCLIAuthApiKey: vi.fn(),
    saveCLIAuthApiKey: vi.fn(),
    deleteCLIAuthApiKey: vi.fn(),
    listCLIAuthProviders: vi.fn().mockResolvedValue([]),
    // ConfirmImpact (bu-cyyi3) fetches the breaks catalogue whenever the
    // revoke confirm panel opens. Mock it so it resolves immediately instead
    // of hitting the real network in jsdom — the "yes, revoke" pill is
    // disabled until this resolves.
    getBreaksCatalogue: vi.fn(),
  };
});
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

// Import the mocked module AFTER vi.mock declarations so vi.mocked() works.
import * as apiClient from "@/api/client.ts";

beforeEach(() => {
  // Default: empty catalogue, resolved immediately — clears the
  // ConfirmImpact "loading" gate on the revoke confirm button synchronously
  // after the panel opens.
  vi.mocked(apiClient.getBreaksCatalogue).mockResolvedValue({ data: [], meta: {} });
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderWithQuery(element: React.ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>{element}</QueryClientProvider>,
  );
}

function cred(overrides: Partial<CliCredential> = {}): CliCredential {
  return {
    id: "claude-cli",
    label: "Claude Code",
    fingerprint: "sha256:11a47cd2",
    state: "ok",
    lastUsed: "today",
    issued: "2026-02-10",
    expires: null,
    scopesGranted: [],
    scopesRequired: [],
    test: null,
    ...overrides,
  };
}

function deviceAuthState(overrides: Partial<CliDeviceAuthState> = {}): CliDeviceAuthState {
  return {
    supported: false,
    isApiKeyMode: false,
    providerName: "claude-cli",
    session: null,
    inProgress: false,
    starting: false,
    reauthorizing: false,
    apiKeyReauthPending: false,
    error: null,
    start: vi.fn(),
    reauthorize: vi.fn(),
    cancel: vi.fn(),
    acknowledgeApiKeyReauth: vi.fn(),
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// Rotate: copy-once reveal panel
// ---------------------------------------------------------------------------

describe("PageCli: rotate action", () => {
  it("rotate button opens a danger confirm before calling rotateCliCredential", () => {
    renderWithQuery(<PageCli credential={cred()} />);

    const rotateBtn = screen.getByRole("button", { name: /^rotate$/i });
    fireEvent.click(rotateBtn);

    expect(document.querySelector("[data-generate-confirm]")).toBeTruthy();
    expect(screen.getByText(/cannot be recovered/i)).toBeTruthy();
    expect(apiClient.rotateCliCredential).not.toHaveBeenCalled();
  });

  it("cancel on the generate confirm closes it without calling rotateCliCredential", () => {
    renderWithQuery(<PageCli credential={cred()} />);
    fireEvent.click(screen.getByRole("button", { name: /^rotate$/i }));
    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));

    expect(document.querySelector("[data-generate-confirm]")).toBeNull();
    expect(apiClient.rotateCliCredential).not.toHaveBeenCalled();
  });

  it("confirming generate triggers rotateCliCredential and shows copy-once panel", async () => {
    vi.mocked(apiClient.rotateCliCredential).mockResolvedValueOnce(
      { data: { fingerprint: "abc123", value: "new-tok-xyz" } } as ReturnType<typeof apiClient.rotateCliCredential> extends Promise<infer T> ? T : never,
    );

    renderWithQuery(<PageCli credential={cred()} />);
    fireEvent.click(screen.getByRole("button", { name: /^rotate$/i }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /yes, generate/i }));
    });

    expect(apiClient.rotateCliCredential).toHaveBeenCalledWith("claude-cli");
    // Copy-once panel rendered with the new value
    await waitFor(() => expect(screen.getByText("new-tok-xyz")).toBeTruthy());
    expect(screen.getByText(/copy now, won't be shown again/i)).toBeTruthy();
  });

  it("rotate copy-once panel has copy and dismiss buttons; dismiss hides value", async () => {
    vi.mocked(apiClient.rotateCliCredential).mockResolvedValueOnce(
      { data: { fingerprint: "abc123", value: "sec-tok" } } as ReturnType<typeof apiClient.rotateCliCredential> extends Promise<infer T> ? T : never,
    );

    renderWithQuery(<PageCli credential={cred()} />);
    fireEvent.click(screen.getByRole("button", { name: /^rotate$/i }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /yes, generate/i }));
    });

    await waitFor(() => expect(screen.getByText("sec-tok")).toBeTruthy());
    // Both copy and dismiss present
    expect(screen.getByRole("button", { name: /^copy$/i })).toBeTruthy();
    const dismissBtn = screen.getByRole("button", { name: /^dismiss$/i });

    // After dismiss, panel disappears
    fireEvent.click(dismissBtn);
    expect(screen.queryByText("sec-tok")).toBeNull();
  });

  it("rotate panel is absent before rotate is called", () => {
    renderWithQuery(<PageCli credential={cred()} />);
    expect(document.querySelector("[data-rotated-secret-panel]")).toBeNull();
    expect(document.querySelector("[data-generate-confirm]")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// cli-auth/* mirror rows: no generate, paste-only (bu-xn1sr defect 2)
// ---------------------------------------------------------------------------

describe("PageCli: cli-auth/* mirror rows never offer generate", () => {
  it("shows 'update token' instead of 'rotate' for a cli-auth/* id", () => {
    renderWithQuery(<PageCli credential={cred({ id: "cli-auth/some-provider" })} />);

    expect(screen.queryByRole("button", { name: /^rotate$/i })).toBeNull();
    expect(screen.getByRole("button", { name: /^update token$/i })).toBeTruthy();
  });

  it("'update token' opens the paste panel and persists the exact pasted value", async () => {
    vi.mocked(apiClient.rotateCliCredential).mockResolvedValueOnce(
      { data: { fingerprint: "fp", value: "real-auth-json" } } as ReturnType<typeof apiClient.rotateCliCredential> extends Promise<infer T> ? T : never,
    );

    renderWithQuery(<PageCli credential={cred({ id: "cli-auth/some-provider" })} />);
    fireEvent.click(screen.getByRole("button", { name: /^update token$/i }));

    const panel = document.querySelector("[data-set-token-panel]");
    expect(panel).toBeTruthy();
    const textarea = panel!.querySelector("textarea")!;
    fireEvent.change(textarea, { target: { value: "real-auth-json" } });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    });

    expect(apiClient.rotateCliCredential).toHaveBeenCalledWith("cli-auth/some-provider", "real-auth-json");
  });

  it("bare (non cli-auth/*) ids still get the generate/rotate path", () => {
    renderWithQuery(<PageCli credential={cred({ id: "generic-token" })} />);

    expect(screen.getByRole("button", { name: /^rotate$/i })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^update token$/i })).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Revoke: danger confirm
// ---------------------------------------------------------------------------

describe("PageCli: revoke action", () => {
  it("revoke button shows danger confirm panel", () => {
    renderWithQuery(<PageCli credential={cred()} />);

    const revokeBtn = screen.getByRole("button", { name: /^revoke$/i });
    fireEvent.click(revokeBtn);

    expect(document.querySelector("[data-revoke-confirm]")).toBeTruthy();
    expect(screen.getByText(/yes, revoke/i)).toBeTruthy();
  });

  it("confirming revoke calls revokeCliCredential", async () => {
    vi.mocked(apiClient.revokeCliCredential).mockResolvedValueOnce(
      { data: { status: "revoked" } } as ReturnType<typeof apiClient.revokeCliCredential> extends Promise<infer T> ? T : never,
    );

    renderWithQuery(<PageCli credential={cred()} />);
    fireEvent.click(screen.getByRole("button", { name: /^revoke$/i }));

    // ConfirmImpact gates "yes, revoke" until the breaks-catalogue fetch
    // resolves (bu-cyyi3 review follow-up) — an uninformed confirm must not
    // be clickable while impact is still loading.
    await waitFor(() => {
      const btn = screen.getByRole("button", { name: /yes, revoke/i }) as HTMLButtonElement;
      expect(btn.disabled).toBe(false);
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /yes, revoke/i }));
    });

    expect(apiClient.revokeCliCredential).toHaveBeenCalledWith("claude-cli");
  });

  it("cancel hides the danger confirm panel", () => {
    renderWithQuery(<PageCli credential={cred()} />);
    fireEvent.click(screen.getByRole("button", { name: /^revoke$/i }));
    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
    expect(document.querySelector("[data-revoke-confirm]")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// api-key mode: save / test / delete
// ---------------------------------------------------------------------------

describe("PageCli: api-key mode (e.g. Claude)", () => {
  const apiKeyDeviceAuth = deviceAuthState({ isApiKeyMode: true, supported: false });

  it("shows 'save key' for missing credential in api-key mode", () => {
    renderWithQuery(
      <PageCli
        credential={cred({ state: "never_set", fingerprint: null })}
        deviceAuth={apiKeyDeviceAuth}
      />,
    );
    expect(screen.getByRole("button", { name: /save key/i })).toBeTruthy();
  });

  it("shows 'update key' for present credential in api-key mode", () => {
    renderWithQuery(
      <PageCli
        credential={cred({ state: "ok" })}
        deviceAuth={apiKeyDeviceAuth}
      />,
    );
    expect(screen.getByRole("button", { name: /update key/i })).toBeTruthy();
  });

  it("save key opens set-token panel and calls saveCLIAuthApiKey on submit", async () => {
    vi.mocked(apiClient.saveCLIAuthApiKey).mockResolvedValueOnce(
      { provider: "claude-cli", stored: true, message: null },
    );

    renderWithQuery(
      <PageCli
        credential={cred({ state: "never_set", fingerprint: null })}
        deviceAuth={apiKeyDeviceAuth}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /save key/i }));
    const panel = document.querySelector("[data-set-token-panel]");
    expect(panel).toBeTruthy();

    const textarea = panel!.querySelector("textarea")!;
    fireEvent.change(textarea, { target: { value: "sk-test-api-key-12345" } });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    });

    expect(apiClient.saveCLIAuthApiKey).toHaveBeenCalledWith("claude-cli", "sk-test-api-key-12345");
  });

  it("delete key calls deleteCLIAuthApiKey for api-key mode", async () => {
    vi.mocked(apiClient.deleteCLIAuthApiKey).mockResolvedValueOnce(
      { status: "deleted" } as ReturnType<typeof apiClient.deleteCLIAuthApiKey> extends Promise<infer T> ? T : never,
    );

    renderWithQuery(
      <PageCli
        credential={cred({ state: "ok" })}
        deviceAuth={apiKeyDeviceAuth}
      />,
    );

    const deleteBtn = screen.getByRole("button", { name: /delete key/i });
    await act(async () => { fireEvent.click(deleteBtn); });

    expect(apiClient.deleteCLIAuthApiKey).toHaveBeenCalledWith("claude-cli");
  });

  it("api-key mode: no rotate button, no revoke button", () => {
    renderWithQuery(
      <PageCli
        credential={cred({ state: "ok" })}
        deviceAuth={apiKeyDeviceAuth}
      />,
    );
    expect(screen.queryByRole("button", { name: /^rotate$/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /^revoke$/i })).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Token mode: paste-to-save persists the exact pasted value [bu-f63t9]
//
// Regression: the "set token" textarea must persist the EXACT pasted value via
// rotateCliCredential(id, value) — not trigger a server-side random generate.
// First-time save for a never_set provider must work (no 404).
// ---------------------------------------------------------------------------

describe("PageCli: token-mode set-token (paste-to-save)", () => {
  // Default deviceAuth is token mode (isApiKeyMode: false, supported: false).
  it("set token for never_set provider opens panel and saves the pasted value", async () => {
    vi.mocked(apiClient.rotateCliCredential).mockResolvedValueOnce(
      { data: { fingerprint: "fp123", value: "my-pasted-token" } } as ReturnType<typeof apiClient.rotateCliCredential> extends Promise<infer T> ? T : never,
    );

    renderWithQuery(
      <PageCli credential={cred({ state: "never_set", fingerprint: null })} />,
    );

    fireEvent.click(screen.getByRole("button", { name: /^set token$/i }));
    const panel = document.querySelector("[data-set-token-panel]");
    expect(panel).toBeTruthy();

    const textarea = panel!.querySelector("textarea")!;
    fireEvent.change(textarea, { target: { value: "my-pasted-token" } });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    });

    // The EXACT pasted value is forwarded — not discarded for a random one.
    expect(apiClient.rotateCliCredential).toHaveBeenCalledWith("claude-cli", "my-pasted-token");
  });

  it("token-mode save does NOT trigger the server-generate (no-value) rotate path", async () => {
    vi.mocked(apiClient.rotateCliCredential).mockResolvedValueOnce(
      { data: { fingerprint: "fp", value: "tok" } } as ReturnType<typeof apiClient.rotateCliCredential> extends Promise<infer T> ? T : never,
    );

    renderWithQuery(
      <PageCli credential={cred({ state: "never_set", fingerprint: null })} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /^set token$/i }));
    const textarea = document.querySelector("[data-set-token-panel] textarea")!;
    fireEvent.change(textarea, { target: { value: "keep-me" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    });

    // Must be called WITH the value (2 args), never the id-only generate form.
    expect(apiClient.rotateCliCredential).toHaveBeenCalledWith("claude-cli", "keep-me");
    expect(apiClient.rotateCliCredential).not.toHaveBeenCalledWith("claude-cli");
  });
});

// ---------------------------------------------------------------------------
// Test button
//
// bu-eptoz: PageCli has exactly one test/probe control — the probe block's
// "run probe" / "probe again" button (ProbeResult in atoms.tsx). A duplicate
// footer "test" pill (CommitFooter) used to fire the identical handleTest
// action across every footer branch (device-code / api-key / cli-auth-mirror
// / generic); it's removed, so these click the probe-block button by its own
// label instead. cred()'s default `test: null` means the label is
// "run probe" (a credential with a prior result shows "probe again").
// ---------------------------------------------------------------------------

describe("PageCli: test button", () => {
  it("test button calls testCLIAuthApiKey for token-mode provider", async () => {
    vi.mocked(apiClient.testCLIAuthApiKey).mockResolvedValueOnce(
      { provider: "claude-cli", success: true, detail: "ok" } as ReturnType<typeof apiClient.testCLIAuthApiKey> extends Promise<infer T> ? T : never,
    );

    renderWithQuery(<PageCli credential={cred()} />);
    const testBtn = screen.getByRole("button", { name: /^run probe$/i });
    await act(async () => { fireEvent.click(testBtn); });

    expect(apiClient.testCLIAuthApiKey).toHaveBeenCalledWith("claude-cli");
  });

  it("test button is present for device-code provider when not missing", () => {
    const deviceCodeAuth = deviceAuthState({ supported: true, isApiKeyMode: false });
    renderWithQuery(
      <PageCli
        credential={cred({ state: "ok", fingerprint: "abc" })}
        deviceAuth={deviceCodeAuth}
      />,
    );
    expect(screen.getByRole("button", { name: /^run probe$/i })).toBeTruthy();
  });
});
