// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// PageCli — action-wiring tests [bu-ayp6v.5]
//
// Coverage:
//   - rotate: calls useRotateCliRuntime and shows copy-once panel on success
//   - revoke: shows danger confirm; calls useRevokeCliRuntime on confirm
//   - api-key save (isApiKeyMode): opens set-token panel, calls useSaveCLIAuthApiKey
//   - api-key delete (isApiKeyMode): calls useDeleteCLIAuthApiKey
//   - test button: calls useTestCLIAuthApiKey
// ---------------------------------------------------------------------------

import { describe, expect, it, vi, afterEach } from "vitest";
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
    revealSecret: vi.fn(),
    listCLIAuthProviders: vi.fn().mockResolvedValue([]),
  };
});
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

// Import the mocked module AFTER vi.mock declarations so vi.mocked() works.
import * as apiClient from "@/api/client.ts";

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
  it("rotate button triggers rotateCliCredential and shows copy-once panel", async () => {
    vi.mocked(apiClient.rotateCliCredential).mockResolvedValueOnce(
      { data: { fingerprint: "abc123", value: "new-tok-xyz" } } as ReturnType<typeof apiClient.rotateCliCredential> extends Promise<infer T> ? T : never,
    );

    renderWithQuery(<PageCli credential={cred()} />);

    const rotateBtn = screen.getByRole("button", { name: /^rotate$/i });
    await act(async () => { fireEvent.click(rotateBtn); });

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
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /^rotate$/i })); });

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
// Test button
// ---------------------------------------------------------------------------

describe("PageCli: test button", () => {
  it("test button calls testCLIAuthApiKey for token-mode provider", async () => {
    vi.mocked(apiClient.testCLIAuthApiKey).mockResolvedValueOnce(
      { provider: "claude-cli", success: true, detail: "ok" } as ReturnType<typeof apiClient.testCLIAuthApiKey> extends Promise<infer T> ? T : never,
    );

    renderWithQuery(<PageCli credential={cred()} />);
    const testBtn = screen.getByRole("button", { name: /^test$/i });
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
    expect(screen.getByRole("button", { name: /^test$/i })).toBeTruthy();
  });
});
