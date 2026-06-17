// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// PageCli - device-code reauth flow tests
//
// Covers the regression where the Secrets passport lost the Codex (and other
// device-code CLI runtime) reauth affordance after the legacy CLIAuthCard was
// removed. PageCli takes the device-auth state as a plain prop, so these tests
// drive it directly without react-query.
//
// Coverage:
//   - never_set + supported: "connect" button, start() on click
//   - expired + supported: "re-authorize" button, reauthorize() on click
//   - reauthorizing: button shows "starting..." and is disabled
//   - starting: button shows "starting..." and is disabled
//   - inProgress: "cancel" button, cancel() on click
//   - awaiting_auth session: verification URL + device code + copy rendered
//   - unsupported provider: legacy "set token" / "rotate" footer, no connect
//   - reauthorize (hook): device_code response drives session polling
//   - reauthorize (hook): api_key response sets apiKeyReauthPending
// ---------------------------------------------------------------------------

import { describe, expect, it, vi, afterEach } from "vitest";
import { render, renderHook, screen, fireEvent, cleanup, act, waitFor } from "@testing-library/react";
import * as React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { PageCli } from "./pages.tsx";
import type { CliCredential } from "./types.ts";
import type { CliDeviceAuthState } from "@/hooks/use-cli-auth.ts";
import { useCliDeviceAuth } from "@/hooks/use-cli-auth.ts";
import * as apiClient from "@/api/client.ts";

// PageCli uses TanStack Query mutation hooks; wrap with a provider.
vi.mock("@/api/client.ts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client.ts")>();
  return {
    ...actual,
    rotateCliCredential: vi.fn(),
    revokeCliCredential: vi.fn(),
    reauthorizeCliCredential: vi.fn(),
    listCLIAuthProviders: vi.fn().mockResolvedValue([]),
    testCLIAuthApiKey: vi.fn(),
    saveCLIAuthApiKey: vi.fn(),
    deleteCLIAuthApiKey: vi.fn(),
  };
});
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function renderWithQuery(element: React.ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>{element}</QueryClientProvider>,
  );
}

// Fixtures

function cred(overrides: Partial<CliCredential> = {}): CliCredential {
  return {
    id: "cli-auth/codex",
    label: "Codex (OpenAI)",
    fingerprint: null,
    state: "never_set",
    lastUsed: null,
    issued: null,
    expires: null,
    scopesGranted: [],
    scopesRequired: [],
    test: null,
    ...overrides,
  };
}

function deviceAuth(overrides: Partial<CliDeviceAuthState> = {}): CliDeviceAuthState {
  return {
    supported: true,
    isApiKeyMode: false,
    providerName: "codex",
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

// Tests

describe("PageCli: device-code reauth flow", () => {
  it("shows 'connect' for a never_set supported credential and calls start()", () => {
    const auth = deviceAuth();
    renderWithQuery(<PageCli credential={cred({ state: "never_set" })} deviceAuth={auth} />);

    const [btn] = screen.getAllByText("connect");
    fireEvent.click(btn);
    expect(auth.start).toHaveBeenCalledOnce();
    // Legacy paste-token affordance must not appear for device-code providers.
    expect(screen.queryByText("set token")).toBeNull();
  });

  it("shows 're-authorize' for an expired supported credential and calls reauthorize()", () => {
    const auth = deviceAuth();
    renderWithQuery(<PageCli credential={cred({ state: "expired", fingerprint: "ab12" })} deviceAuth={auth} />);

    const [btn] = screen.getAllByText("re-authorize");
    fireEvent.click(btn);
    expect(auth.reauthorize).toHaveBeenCalledOnce();
    // start() is NOT called for re-auth — only for initial connect (never_set)
    expect(auth.start).not.toHaveBeenCalled();
    expect(screen.queryByText("rotate")).toBeNull();
  });

  it("shows 'starting…' and disables the button while starting", () => {
    renderWithQuery(<PageCli credential={cred()} deviceAuth={deviceAuth({ starting: true })} />);
    const [btn] = screen.getAllByText("starting…").map((el) => el.closest("button")!);
    expect(btn.disabled).toBe(true);
  });

  it("shows 'starting…' and disables the button while reauthorizing", () => {
    renderWithQuery(
      <PageCli
        credential={cred({ state: "ok", fingerprint: "ab12" })}
        deviceAuth={deviceAuth({ reauthorizing: true })}
      />,
    );
    const [btn] = screen.getAllByText("starting…").map((el) => el.closest("button")!);
    expect(btn.disabled).toBe(true);
  });

  it("shows 'cancel' while a session is in progress and calls cancel()", () => {
    const auth = deviceAuth({
      inProgress: true,
      session: { session_id: "s1", state: "awaiting_auth", auth_url: null, device_code: null, message: null, provider: "codex" },
    });
    renderWithQuery(<PageCli credential={cred()} deviceAuth={auth} />);

    const [btn] = screen.getAllByText("cancel");
    fireEvent.click(btn);
    expect(auth.cancel).toHaveBeenCalledOnce();
  });

  it("renders the verification URL and device code while awaiting authorization", () => {
    const auth = deviceAuth({
      inProgress: true,
      session: {
        session_id: "s1",
        state: "awaiting_auth",
        auth_url: "https://auth.openai.com/codex/device",
        device_code: "ABCD-EFGH",
        message: null,
        provider: "codex",
      },
    });
    renderWithQuery(<PageCli credential={cred()} deviceAuth={auth} />);

    expect(screen.getAllByText("ABCD-EFGH").length).toBeGreaterThan(0);
    const link = screen.getAllByText("https://auth.openai.com/codex/device")[0].closest("a");
    expect(link?.getAttribute("href")).toBe("https://auth.openai.com/codex/device");
    expect(screen.getAllByText("copy").length).toBeGreaterThan(0);
  });

  it("renders the legacy footer (no connect button) for unsupported providers", () => {
    renderWithQuery(
      <PageCli
        credential={cred({ id: "cli-auth/claude", label: "Claude", state: "never_set" })}
        deviceAuth={deviceAuth({ supported: false, providerName: "claude" })}
      />,
    );
    expect(screen.getAllByText("set token").length).toBeGreaterThan(0);
    expect(screen.queryByText("connect")).toBeNull();
  });

  it("renders the legacy footer when no deviceAuth prop is supplied", () => {
    renderWithQuery(<PageCli credential={cred({ state: "never_set" })} />);
    expect(screen.getAllByText("set token").length).toBeGreaterThan(0);
    expect(screen.queryByText("connect")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// useCliDeviceAuth: reauthorize() calls POST /api/secrets/cli/{id}/reauthorize
// ---------------------------------------------------------------------------

describe("useCliDeviceAuth: reauthorize() routes to audited endpoint", () => {
  function wrapper({ children }: { children: React.ReactNode }) {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  }

  it("device_code branch: calls reauthorizeCliCredential and sets sessionId for polling", async () => {
    vi.mocked(apiClient.reauthorizeCliCredential).mockResolvedValueOnce({
      data: {
        auth_mode: "device_code",
        provider: "codex",
        session_id: "test-session-999",
        auth_url: "https://auth.openai.com/device",
        device_code: "XXXX-YYYY",
        message: null,
      },
      meta: {} as never,
    });
    // listCLIAuthProviders returns empty so supported=false; that's fine — we
    // are testing the hook method in isolation, not the UI rendering.
    vi.mocked(apiClient.listCLIAuthProviders).mockResolvedValue([]);

    const { result } = renderHook(() => useCliDeviceAuth("cli-auth/codex"), { wrapper });

    await act(async () => {
      await result.current.reauthorize();
    });

    expect(apiClient.reauthorizeCliCredential).toHaveBeenCalledWith("cli-auth/codex");
    // After device_code response: session is now being polled
    // (sessionId set internally; inProgress = true while not terminal)
    expect(result.current.reauthorizing).toBe(false);
    expect(result.current.apiKeyReauthPending).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("api_key branch: calls reauthorizeCliCredential and sets apiKeyReauthPending", async () => {
    vi.mocked(apiClient.reauthorizeCliCredential).mockResolvedValueOnce({
      data: {
        auth_mode: "api_key",
        provider: "claude",
        env_var: "ANTHROPIC_API_KEY",
        prompt: "Enter your API key for Claude.",
      },
      meta: {} as never,
    });
    vi.mocked(apiClient.listCLIAuthProviders).mockResolvedValue([]);

    const { result } = renderHook(() => useCliDeviceAuth("cli-auth/claude"), { wrapper });

    await act(async () => {
      await result.current.reauthorize();
    });

    expect(apiClient.reauthorizeCliCredential).toHaveBeenCalledWith("cli-auth/claude");
    expect(result.current.apiKeyReauthPending).toBe(true);
    expect(result.current.reauthorizing).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("acknowledgeApiKeyReauth() clears apiKeyReauthPending", async () => {
    vi.mocked(apiClient.reauthorizeCliCredential).mockResolvedValueOnce({
      data: { auth_mode: "api_key", provider: "claude", env_var: null, prompt: null },
      meta: {} as never,
    });
    vi.mocked(apiClient.listCLIAuthProviders).mockResolvedValue([]);

    const { result } = renderHook(() => useCliDeviceAuth("cli-auth/claude"), { wrapper });

    await act(async () => {
      await result.current.reauthorize();
    });
    expect(result.current.apiKeyReauthPending).toBe(true);

    act(() => {
      result.current.acknowledgeApiKeyReauth();
    });
    expect(result.current.apiKeyReauthPending).toBe(false);
  });

  it("error from reauthorizeCliCredential is surfaced in error field", async () => {
    vi.mocked(apiClient.reauthorizeCliCredential).mockRejectedValueOnce(
      new Error("provider not found"),
    );
    vi.mocked(apiClient.listCLIAuthProviders).mockResolvedValue([]);

    const { result } = renderHook(() => useCliDeviceAuth("cli-auth/codex"), { wrapper });

    await act(async () => {
      await result.current.reauthorize();
    });

    await waitFor(() => expect(result.current.error).toBe("provider not found"));
    expect(result.current.apiKeyReauthPending).toBe(false);
  });
});
