/**
 * Smoke tests for the Secrets v2 API client functions (bu-ayp6v.1).
 *
 * Verifies:
 * - Correct URL paths (including path segments and ?identity= / ?target= params)
 * - Correct HTTP methods for each mutation endpoint
 * - Response pass-through for all functions
 * - CLI rotate returns the one-time {fingerprint, value} shape
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

function mockApiResponse(data: unknown, status = 200) {
  mockFetch.mockResolvedValueOnce({
    ok: status >= 200 && status < 300,
    status,
    json: async () => ({ data, meta: {} }),
    text: async () => JSON.stringify({ data, meta: {} }),
    statusText: status === 200 ? "OK" : "Error",
    headers: { get: () => "application/json" },
  });
}

// ---------------------------------------------------------------------------
// Import the functions under test (after mock setup)
// ---------------------------------------------------------------------------

import {
  // Per-credential reads
  getUserCredential,
  getSystemCredential,
  getCliCredential,
  getCredentialAudit,
  // User mutations
  rotateUserCredential,
  disconnectUserCredential,
  probeUserCredential,
  // System mutations
  setSystemCredential,
  probeSystemCredential,
  deleteSystemCredential,
  // CLI mutations
  rotateCliCredential,
  revokeCliCredential,
  reauthorizeCliCredential,
  // Already-existing reauthorize (must not be duplicated)
  reauthorizeUserCredential,
} from "./client.ts";

// ---------------------------------------------------------------------------
// Per-credential reads
// ---------------------------------------------------------------------------

describe("getUserCredential", () => {
  it("calls GET /api/secrets/user/<provider>", async () => {
    mockApiResponse({ id: "uuid", entity_id: "eid", type: "google_oauth_refresh", provider: "google", state: "ok", fingerprint: "abc12345" });
    await getUserCredential("google");
    const [url, opts] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/secrets/user/google");
    expect(opts?.method).toBeUndefined(); // GET (no method = default)
  });

  it("appends ?identity= when provided", async () => {
    mockApiResponse({});
    await getUserCredential("google", "entity-uuid-1");
    const [url] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("?identity=entity-uuid-1");
  });

  it("does NOT append ?identity= when omitted", async () => {
    mockApiResponse({});
    await getUserCredential("spotify");
    const [url] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).not.toContain("identity");
  });
});

describe("getSystemCredential", () => {
  it("calls GET /api/secrets/system/<key>", async () => {
    mockApiResponse({ key: "BUTLER_TELEGRAM_TOKEN", state: "ok", butler: "messenger" });
    await getSystemCredential("BUTLER_TELEGRAM_TOKEN");
    const [url] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/secrets/system/BUTLER_TELEGRAM_TOKEN");
  });
});

describe("getCliCredential", () => {
  it("calls GET /api/secrets/cli/<id>", async () => {
    mockApiResponse({ id: "claude", state: "ok" });
    await getCliCredential("claude");
    const [url] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/secrets/cli/claude");
  });
});

describe("getCredentialAudit", () => {
  it("calls GET /api/secrets/audit/<scope>/<key>", async () => {
    mockApiResponse([]);
    await getCredentialAudit("user", "google");
    const [url] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/secrets/audit/user/google");
  });

  it("appends ?limit= when provided", async () => {
    mockApiResponse([]);
    await getCredentialAudit("system", "MY_KEY", { limit: 20 });
    const [url] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("?limit=20");
  });

  it("does NOT append limit when omitted", async () => {
    mockApiResponse([]);
    await getCredentialAudit("cli", "claude");
    const [url] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).not.toContain("limit");
  });
});

// ---------------------------------------------------------------------------
// User mutations
// ---------------------------------------------------------------------------

describe("rotateUserCredential", () => {
  it("calls POST /api/secrets/user/<provider>/rotate", async () => {
    mockApiResponse({ state: "ok" });
    await rotateUserCredential("google", { value: "new-token-value" });
    const [url, opts] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/secrets/user/google/rotate");
    expect(opts?.method).toBe("POST");
    expect(JSON.parse(opts?.body as string)).toEqual({ value: "new-token-value" });
  });

  it("appends ?identity= when provided", async () => {
    mockApiResponse({});
    await rotateUserCredential("spotify", { value: "tok" }, "entity-id-99");
    const [url] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("?identity=entity-id-99");
  });
});

describe("disconnectUserCredential", () => {
  it("calls POST /api/secrets/user/<provider>/disconnect", async () => {
    mockApiResponse({ status: "disconnected" });
    await disconnectUserCredential("google");
    const [url, opts] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/secrets/user/google/disconnect");
    expect(opts?.method).toBe("POST");
  });

  it("appends ?identity= when provided", async () => {
    mockApiResponse({ status: "disconnected" });
    await disconnectUserCredential("google", "eid-1");
    const [url] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("?identity=eid-1");
  });
});

describe("probeUserCredential", () => {
  it("calls POST /api/secrets/user/<provider>/probe", async () => {
    mockApiResponse({ ok: true, code: null, message: null, at: "14:21 today" });
    const result = await probeUserCredential("google");
    const [url, opts] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/secrets/user/google/probe");
    expect(opts?.method).toBe("POST");
    expect(result.data.ok).toBe(true);
  });

  it("appends ?identity= when provided", async () => {
    mockApiResponse({ ok: false });
    await probeUserCredential("github", "eid-2");
    const [url] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("?identity=eid-2");
  });
});

// ---------------------------------------------------------------------------
// System mutations
// ---------------------------------------------------------------------------

describe("setSystemCredential", () => {
  it("calls POST /api/secrets/system/<key> with shared target", async () => {
    mockApiResponse({ key: "MY_KEY", state: "ok" });
    await setSystemCredential("MY_KEY", { value: "secret-val", target: "shared" });
    const [url, opts] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/secrets/system/MY_KEY");
    expect(opts?.method).toBe("POST");
    const body = JSON.parse(opts?.body as string);
    expect(body).toEqual({ value: "secret-val", target: "shared" });
  });

  it("sends butler name as target for per-butler override", async () => {
    mockApiResponse({ key: "MY_KEY", state: "ok", row_state: "local" });
    await setSystemCredential("MY_KEY", { value: "override-val", target: "messenger" });
    const [, opts] = mockFetch.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(opts?.body as string);
    expect(body.target).toBe("messenger");
  });
});

describe("probeSystemCredential", () => {
  it("calls POST /api/secrets/system/<key>/probe", async () => {
    mockApiResponse({ ok: true, code: null, message: null, at: "09:05 today" });
    const result = await probeSystemCredential("BUTLER_OPENAI_KEY");
    const [url, opts] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/secrets/system/BUTLER_OPENAI_KEY/probe");
    expect(opts?.method).toBe("POST");
    expect(result.data.ok).toBe(true);
  });
});

describe("deleteSystemCredential", () => {
  it("calls DELETE /api/secrets/system/<key>?target=shared by default", async () => {
    mockApiResponse({ status: "disconnected" });
    await deleteSystemCredential("MY_KEY");
    const [url, opts] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/secrets/system/MY_KEY");
    expect(url).toContain("target=shared");
    expect(opts?.method).toBe("DELETE");
  });

  it("passes butler name in ?target= for override deletion", async () => {
    mockApiResponse({ status: "revoked" });
    await deleteSystemCredential("MY_KEY", "messenger");
    const [url] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("target=messenger");
  });
});

// ---------------------------------------------------------------------------
// CLI mutations
// ---------------------------------------------------------------------------

describe("rotateCliCredential", () => {
  it("calls POST /api/secrets/cli/<id>/rotate", async () => {
    mockApiResponse({ fingerprint: "ab12cd34", value: "raw-secret-value-ONCE" });
    const result = await rotateCliCredential("claude");
    const [url, opts] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/secrets/cli/claude/rotate");
    expect(opts?.method).toBe("POST");
    // The one-time value must be present in the response
    expect(result.data.fingerprint).toBe("ab12cd34");
    expect(result.data.value).toBe("raw-secret-value-ONCE");
  });
});

describe("revokeCliCredential", () => {
  it("calls POST /api/secrets/cli/<id>/revoke", async () => {
    mockApiResponse({ status: "revoked" });
    const result = await revokeCliCredential("old-cli-token");
    const [url, opts] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/secrets/cli/old-cli-token/revoke");
    expect(opts?.method).toBe("POST");
    expect(result.data.status).toBe("revoked");
  });
});

// ---------------------------------------------------------------------------
// reauthorizeCliCredential — bu-3wg2l (C10 bridge)
// ---------------------------------------------------------------------------

describe("reauthorizeCliCredential", () => {
  it("calls POST /api/secrets/cli/<id>/reauthorize for device_code branch", async () => {
    mockApiResponse({
      auth_mode: "device_code",
      provider: "codex",
      session_id: "sess-abc",
      auth_url: "https://auth.openai.com/device",
      device_code: "AAAA-BBBB",
      message: null,
    });
    const result = await reauthorizeCliCredential("codex");
    const [url, opts] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/secrets/cli/codex/reauthorize");
    expect(opts?.method).toBe("POST");
    expect(result.data.auth_mode).toBe("device_code");
    expect(result.data.session_id).toBe("sess-abc");
  });

  it("calls POST /api/secrets/cli/<id>/reauthorize for api_key branch", async () => {
    mockApiResponse({
      auth_mode: "api_key",
      provider: "claude",
      env_var: "ANTHROPIC_API_KEY",
      prompt: "Enter your API key for Claude.",
    });
    const result = await reauthorizeCliCredential("claude");
    const [url, opts] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/secrets/cli/claude/reauthorize");
    expect(opts?.method).toBe("POST");
    expect(result.data.auth_mode).toBe("api_key");
    expect(result.data.env_var).toBe("ANTHROPIC_API_KEY");
  });

  it("URL-encodes the credential id", async () => {
    mockApiResponse({ auth_mode: "device_code", provider: "opencode-openai" });
    await reauthorizeCliCredential("opencode-openai");
    const [url] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/secrets/cli/opencode-openai/reauthorize");
  });
});

// ---------------------------------------------------------------------------
// reauthorizeUserCredential — verify not duplicated (already exists in client.ts)
// ---------------------------------------------------------------------------

describe("reauthorizeUserCredential (existing — not duplicated)", () => {
  it("calls POST /api/secrets/user/<provider>/reauthorize?identity=<uuid>", async () => {
    mockApiResponse({ redirect_url: "/api/oauth/google/start?page_of_origin=secrets" });
    const result = await reauthorizeUserCredential("google", "entity-uuid-42");
    const [url, opts] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/secrets/user/google/reauthorize");
    expect(url).toContain("identity=entity-uuid-42");
    expect(opts?.method).toBe("POST");
    expect(result.data.redirect_url).toContain("/api/oauth/google/start");
  });
});
