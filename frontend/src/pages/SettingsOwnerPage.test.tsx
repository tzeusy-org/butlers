// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import SettingsOwnerPage from "@/pages/SettingsOwnerPage";
import {
  getGoogleCredentialStatus,
  getGoogleOAuthStartUrl,
  upsertGoogleCredentials,
} from "@/api/index.ts";

vi.mock("@/api/index.ts", () => ({
  getGoogleCredentialStatus: vi.fn(),
  getGoogleOAuthStartUrl: vi.fn(),
  upsertGoogleCredentials: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <SettingsOwnerPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getGoogleCredentialStatus).mockResolvedValue({
    client_id_configured: true,
    client_secret_configured: true,
    refresh_token_present: false,
    scope: null,
    oauth_health: "not_configured",
    oauth_health_remediation: "Connect Google.",
    oauth_health_detail: null,
  });
  vi.mocked(getGoogleOAuthStartUrl).mockReturnValue("/api/oauth/google/start?page_of_origin=settings_owner");
  vi.mocked(upsertGoogleCredentials).mockResolvedValue({
    success: true,
    message: "Google app credentials stored.",
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("SettingsOwnerPage", () => {
  it("renders owner settings and Google OAuth app credential controls", async () => {
    renderPage();

    expect(await screen.findByRole("heading", { name: "Owner Config" })).toBeTruthy();
    expect(screen.getByLabelText("Google OAuth client ID")).toBeTruthy();
    expect(screen.getByLabelText("Google OAuth client secret")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Save app credentials" })).toBeTruthy();
  });

  it("saves Google OAuth app credentials through the OAuth credentials endpoint", async () => {
    renderPage();

    fireEvent.change(await screen.findByLabelText("Google OAuth client ID"), {
      target: { value: "client-id.apps.googleusercontent.com" },
    });
    fireEvent.change(screen.getByLabelText("Google OAuth client secret"), {
      target: { value: "client-secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save app credentials" }));

    await waitFor(() => {
      expect(upsertGoogleCredentials).toHaveBeenCalledWith({
        client_id: "client-id.apps.googleusercontent.com",
        client_secret: "client-secret",
      });
    });
  });

  it("builds owner-settings OAuth start URL for browser reauthorization", async () => {
    renderPage();

    await screen.findByRole("heading", { name: "Owner Config" });
    expect(getGoogleOAuthStartUrl).toHaveBeenCalledWith({
      forceConsent: true,
      pageOfOrigin: "settings_owner",
    });
  });
});
