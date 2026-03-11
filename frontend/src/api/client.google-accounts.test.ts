/**
 * Tests for Google accounts API client functions.
 *
 * Verifies:
 * - Correct path prefixes for all account management endpoints
 * - Correct HTTP methods (GET, PUT, DELETE)
 * - Correct query parameter construction (hard_delete, force_consent, account_hint)
 * - Response pass-through for typed API functions
 */

import { afterEach, describe, expect, it, vi } from "vitest";

// ---------------------------------------------------------------------------
// Mock fetch so we never hit the network
// ---------------------------------------------------------------------------

const mockFetch = vi.fn();
global.fetch = mockFetch as unknown as typeof fetch;

afterEach(() => {
  vi.clearAllMocks();
});

function mockResponse(data: unknown, status = 200) {
  mockFetch.mockResolvedValueOnce({
    ok: status >= 200 && status < 300,
    status,
    json: async () => data,
    text: async () => JSON.stringify(data),
    statusText: status === 200 ? "OK" : "Error",
    headers: { get: () => "application/json" },
  });
}

// ---------------------------------------------------------------------------
// Import the functions under test (after mock setup)
// ---------------------------------------------------------------------------

import {
  getGoogleAccounts,
  setPrimaryAccount,
  disconnectAccount,
  getAccountStatus,
  getGoogleOAuthStartUrl,
} from "./client.ts";

// ---------------------------------------------------------------------------
// getGoogleAccounts
// ---------------------------------------------------------------------------

describe("getGoogleAccounts", () => {
  it("calls GET /api/oauth/google/accounts", async () => {
    mockResponse([]);
    await getGoogleAccounts();
    const [url] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/oauth/google/accounts");
    expect(url).toMatch(/\/api\/oauth\/google\/accounts$/);
  });

  it("returns the account list", async () => {
    const accounts = [
      {
        id: "uuid-1",
        email: "test@example.com",
        display_name: "Test User",
        is_primary: true,
        status: "active",
        granted_scopes: ["https://www.googleapis.com/auth/gmail.modify"],
        connected_at: "2026-01-01T00:00:00Z",
        last_token_refresh_at: null,
      },
    ];
    mockResponse(accounts);
    const result = await getGoogleAccounts();
    expect(result).toEqual(accounts);
  });
});

// ---------------------------------------------------------------------------
// setPrimaryAccount
// ---------------------------------------------------------------------------

describe("setPrimaryAccount", () => {
  it("calls PUT /api/oauth/google/accounts/<id>/primary", async () => {
    mockResponse({ success: true, account: {} });
    await setPrimaryAccount("uuid-1");
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/oauth/google/accounts/uuid-1/primary"),
      expect.objectContaining({ method: "PUT" }),
    );
  });
});

// ---------------------------------------------------------------------------
// disconnectAccount
// ---------------------------------------------------------------------------

describe("disconnectAccount", () => {
  it("calls DELETE /api/oauth/google/accounts/<id> (soft delete)", async () => {
    mockResponse({ success: true, message: "Account disconnected.", auto_promoted_id: null });
    await disconnectAccount("uuid-2");
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/oauth/google/accounts/uuid-2"),
      expect.objectContaining({ method: "DELETE" }),
    );
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).not.toContain("hard_delete");
  });

  it("appends hard_delete=true when requested", async () => {
    mockResponse({
      success: true,
      message: "Account disconnected (hard deleted).",
      auto_promoted_id: null,
    });
    await disconnectAccount("uuid-3", true);
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("hard_delete=true");
  });
});

// ---------------------------------------------------------------------------
// getAccountStatus
// ---------------------------------------------------------------------------

describe("getAccountStatus", () => {
  it("calls GET /api/oauth/google/accounts/<id>/status", async () => {
    const status = {
      has_refresh_token: true,
      has_app_credentials: true,
      granted_scopes: [],
      missing_scopes: [],
      token_valid: true,
      last_token_refresh_at: null,
    };
    mockResponse(status);
    const result = await getAccountStatus("uuid-4");
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/oauth/google/accounts/uuid-4/status"),
      expect.anything(),
    );
    expect(result).toEqual(status);
  });
});

// ---------------------------------------------------------------------------
// getGoogleOAuthStartUrl
// ---------------------------------------------------------------------------

describe("getGoogleOAuthStartUrl", () => {
  it("returns base URL when no options provided", () => {
    const url = getGoogleOAuthStartUrl();
    expect(url).toContain("/api/oauth/google/start");
    expect(url).not.toContain("?");
  });

  it("appends account_hint when provided", () => {
    const url = getGoogleOAuthStartUrl({ accountHint: "user@example.com" });
    expect(url).toContain("account_hint=user%40example.com");
  });

  it("appends force_consent=true when requested", () => {
    const url = getGoogleOAuthStartUrl({ forceConsent: true });
    expect(url).toContain("force_consent=true");
  });

  it("appends both params when both provided", () => {
    const url = getGoogleOAuthStartUrl({
      accountHint: "user@example.com",
      forceConsent: true,
    });
    expect(url).toContain("account_hint=");
    expect(url).toContain("force_consent=true");
  });
});
