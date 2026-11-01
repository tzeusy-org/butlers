import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { approveApproval, confirmAutonomySuggestion, createApprovalRule, createApprovalRuleFromAction } from "./client";
import { clearOwnerSession, rememberOwnerCsrf } from "./owner-session";

const fetchMock = vi.fn();
const getCredential = vi.fn();
const bytes = (n: number) => new Uint8Array(n).buffer;
const b64 = (n: number) => Buffer.from(bytes(n)).toString("base64url");
const options = { ceremony_id: b64(32), publicKey: { challenge: b64(32) } };
const rule = { butler: "home", tool_name: "synthetic_send", arg_constraints: {}, description: "Synthetic" };
const response = (data: unknown, status = 200) => new Response(JSON.stringify({ data }), { status });
function credential(): Credential {
  return { id: b64(32), rawId: bytes(32), type: "public-key", authenticatorAttachment: "platform",
    getClientExtensionResults: () => ({}),
    response: { clientDataJSON: bytes(12), authenticatorData: bytes(37), signature: bytes(70), userHandle: bytes(32) },
  } as unknown as Credential;
}
beforeEach(() => {
  clearOwnerSession();
  rememberOwnerCsrf({ csrf_token: "synthetic-independent-csrf", csrf_expires_at: new Date(Date.now() + 60_000).toISOString() });
  vi.stubGlobal("fetch", fetchMock); fetchMock.mockReset();
  vi.stubGlobal("navigator", { credentials: { get: getCredential } });
  getCredential.mockReset().mockResolvedValue(credential());
});
afterEach(() => vi.unstubAllGlobals());

describe("standing permission browser ceremony", () => {
  it("binds the exact intent and fresh assertion before each mutation, keeping proof only in its header", async () => {
    fetchMock.mockImplementation(async (url: string) => response(url.endsWith("/options") ? options
      : url.endsWith("/finish") ? { nonce: b64(32), expires_at: new Date(Date.now() + 60_000).toISOString() } : { id: "synthetic" }));
    await createApprovalRule(rule);
    await createApprovalRuleFromAction({ action_id: "synthetic-action" });
    expect(getCredential).toHaveBeenCalledTimes(2);
    const calls = fetchMock.mock.calls;
    expect(calls.map(c => c[0])).toEqual([
      "/api/approvals/step-up/options", "/api/auth/owner/step-up/finish", "/api/approvals/rules",
      "/api/approvals/step-up/options", "/api/auth/owner/step-up/finish", "/api/approvals/rules/from-action",
    ]);
    expect(JSON.parse(calls[0][1].body)).toEqual({ kind: "create", parameters: rule });
    for (const i of [2, 5]) {
      expect(new Headers(calls[i][1].headers).get("X-Dashboard-Step-Up")).toBe(b64(32));
      expect(calls[i][1].body).not.toContain(b64(32));
      expect(calls[i][1].credentials).toBe("same-origin");
    }
    expect(JSON.parse(calls[1][1].body).credential.response).toHaveProperty("signature");
  });
  it("does not submit a rule or finish ceremony when the native chooser is cancelled", async () => {
    fetchMock.mockResolvedValue(response(options));
    getCredential.mockRejectedValue(new DOMException("Synthetic cancellation", "NotAllowedError"));
    await expect(createApprovalRule(rule)).rejects.toThrow("Synthetic cancellation");
    expect(fetchMock).toHaveBeenCalledOnce();
  });
  it("does not revive authority after session loss while the chooser is open", async () => {
    fetchMock.mockResolvedValue(response(options));
    getCredential.mockImplementation(async () => { clearOwnerSession(); return credential(); });
    await expect(createApprovalRule(rule)).rejects.toThrow();
    expect(fetchMock).toHaveBeenCalledOnce();
  });
  it("keeps ordinary approval and server-classified demotion one-tap without a factor", async () => {
    fetchMock.mockImplementation(async () => response({ required: false }));
    await approveApproval("synthetic-action", {});
    await confirmAutonomySuggestion("synthetic-demotion");
    expect(getCredential).not.toHaveBeenCalled();
    expect(fetchMock.mock.calls.map(c => c[0])).toEqual([
      "/api/approvals/synthetic-action/approve", "/api/approvals/step-up/options",
      "/api/approvals/suggestions/synthetic-demotion/confirm",
    ]);
    for (const [, init] of fetchMock.mock.calls) expect(new Headers(init.headers).has("X-Dashboard-Step-Up")).toBe(false);
  });
  it("refuses downgrade-shaped options and expired proof without sending a standing mutation", async () => {
    fetchMock.mockResolvedValueOnce(response({ required: false }));
    await expect(createApprovalRule(rule)).rejects.toThrow();
    expect(getCredential).not.toHaveBeenCalled();
    fetchMock.mockResolvedValueOnce(response(options)).mockResolvedValueOnce(response({ nonce: b64(32), expires_at: "2000-01-01T00:00:00Z" }));
    await expect(createApprovalRule(rule)).rejects.toThrow();
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls.some(c => c[0] === "/api/approvals/rules")).toBe(false);
  });
});
